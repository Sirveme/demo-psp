"""Vigilancia del pago (PagoOK) y emisión del comprobante (Facturalo), una tarea por demostración.

Todo el estado vive en la fila `demos`. La tarea solo es el "reloj" que la avanza: si el proceso
se reinicia, `asegurar_tarea()` la relanza desde lo que diga la base.
"""
import asyncio
import json
import logging
import re
import unicodedata
from collections import deque
from datetime import datetime, timedelta, timezone

from app import clinica, config, limites
from app.db import Demo, Sesion, recortar
from app.rubros import rubro
from app.servicios import facturalo, pagook, whatsapp

log = logging.getLogger("demo.motor")

ACTIVOS = ("esperando_pago", "pago_confirmado", "emitiendo", "enviando")
VERIFICANDO = "Estamos verificando tu pago…"
TERMINALES_LOG = ("aceptado", "aceptado_con_observaciones", "rechazado", "anulado")
_tareas: dict[str, asyncio.Task] = {}
_despertar: dict[str, asyncio.Event] = {}
# Errores técnicos recientes (PagoOK/Facturalo): solo para el log y /sala, nunca para el cliente.
incidentes: deque = deque(maxlen=20)


def incidente(demo_id: str, servicio: str, operacion: str, status: int, detalle=None):
    texto = {0: "sin respuesta (red o timeout)", -1: "excepción"}.get(status, f"HTTP {status}")
    if detalle:
        texto += f": {json.dumps(detalle, ensure_ascii=False)[:160]}"
    log.warning("demo %s: %s %s -> %s", demo_id, servicio, operacion, texto)
    incidentes.append({"en": config.ahora(), "demo": demo_id, "servicio": servicio,
                       "operacion": operacion, "status": status, "detalle": texto})


def despertar(demo_id: str):
    """Interrumpe la espera entre consultas (p. ej. al recibir la hora del voucher)."""
    ev = _despertar.get(demo_id)
    if ev:
        ev.set()


async def _dormir(demo_id: str, segundos: float):
    ev = _despertar.setdefault(demo_id, asyncio.Event())
    try:
        await asyncio.wait_for(ev.wait(), timeout=segundos)
    except asyncio.TimeoutError:
        pass
    ev.clear()


def asegurar_tarea(demo_id: str, estado: str):
    if estado not in ACTIVOS:
        return
    t = _tareas.get(demo_id)
    if t and not t.done():
        return
    _tareas[demo_id] = asyncio.create_task(_ciclo(demo_id), name=f"demo-{demo_id}")


def reiniciar(demo_id: str):
    """Cancela la tarea en curso de la demo (si hay) y la relanza desde lo que diga la base."""
    t = _tareas.pop(demo_id, None)
    if t and not t.done():
        t.cancel()
    asegurar_tarea(demo_id, _leer(demo_id).estado)


def reanudar_pendientes():
    """Al arrancar: relanza las demos que quedaron a medio camino (máx. 30 min de antigüedad)."""
    limite = config.ahora() - timedelta(minutes=30)
    with Sesion() as s:
        filas = s.query(Demo.id, Demo.estado).filter(Demo.estado.in_(ACTIVOS),
                                                     Demo.registrado_en >= limite).all()
    for demo_id, estado in filas:
        asegurar_tarea(demo_id, estado)


def _leer(demo_id: str) -> Demo:
    with Sesion() as s:
        return s.get(Demo, demo_id)


def _guardar(demo_id: str, **campos):
    with Sesion() as s:
        d = s.get(Demo, demo_id)
        for k, v in recortar(Demo, campos).items():
            setattr(d, k, v)
        s.commit()


async def _ciclo(demo_id: str):
    try:
        d = _leer(demo_id)
        if d.estado == "esperando_pago":
            vigilar = _vigilar_pago_caja if d.caja else _vigilar_pago
            if not await vigilar(demo_id):
                return
            d = _leer(demo_id)
        if d.estado in ("pago_confirmado", "emitiendo"):
            if not await _emitir(demo_id):
                return
        await _esperar_sunat(demo_id)
    except Exception as e:
        log.exception("demo %s", demo_id)
        incidente(demo_id, "demo-psp", "error interno", -1, f"{type(e).__name__}: {str(e)[:200]}")
        _guardar(demo_id, mensaje="Tuvimos un problema interno. Seguimos intentando…")
        await asyncio.sleep(5)
        _tareas.pop(demo_id, None)
        asegurar_tarea(demo_id, _leer(demo_id).estado)


# --- Paso 2: el pago ---------------------------------------------------------------
def _voucher_iso(hhmm: str) -> str:
    hoy = config.a_lima(config.ahora()).date()
    h, m = (int(x) for x in hhmm.split(":"))
    return datetime(hoy.year, hoy.month, hoy.day, h, m, tzinfo=config.LIMA).isoformat()


def _aceptable(pago: dict, d: Demo) -> bool:
    """Regla de la demo: nombre compatible y pago posterior a registrado_en − 60 s."""
    if not pago or pago.get("coincide_nombre") is False:
        return False
    cuando = config.desde_iso(pago.get("fecha_hora"))
    return cuando is not None and cuando >= d.registrado_en - timedelta(seconds=60)


def _resumen(pagos) -> str:
    return "[" + ", ".join(f"{p.get('pago_id')}@{(p.get('fecha_hora') or '')[11:19]}"
                           f"/{p.get('contraparte')!r}/nombre={p.get('coincide_nombre')}"
                           for p in (pagos or [])) + "]"


def referencia(d: Demo) -> str:
    """referencia_externa en Facturalo. Al reemitir cambia (-R1, -R2…) para no reutilizar el rechazado."""
    return f"DEMO-{d.id}" + (f"-R{d.reemisiones}" if d.reemisiones else "")


def _voucher_dt(hhmm: str) -> datetime:
    """HH:MM de hoy en Lima -> UTC sin zona (como se guarda en la base)."""
    return config.desde_iso(_voucher_iso(hhmm))


def elegir_candidato(candidatos: list[dict], registrado_en: datetime,
                     voucher_hora: str | None) -> tuple[dict | None, str | None]:
    """Regla para nivel=media. Devuelve (pago elegido, mensaje para la pantalla).

    Solo cuentan los candidatos posteriores a registrado_en − 60 s, con nombre compatible con el
    titular declarado y no usados:
      - exactamente uno -> ese;
      - ninguno -> seguir esperando;
      - dos o más -> con la hora del voucher, el más cercano a ella, solo si el segundo más
        cercano está a más de 2 min; sin hora, se pide; si no hay distancia clara, "verificando".
    """
    desde = registrado_en - timedelta(seconds=60)
    buenos = []
    for p in candidatos:
        cuando = config.desde_iso(p.get("fecha_hora"))
        if (cuando is not None and cuando >= desde and p.get("coincide_nombre") is not False
                and p.get("estado") not in ("usado", "reclamado_por_otro_sistema")):
            buenos.append((cuando, p))

    if len(buenos) == 1:
        return buenos[0][1], None
    if not buenos:
        return None, (f"Aún no aparece a las {voucher_hora}, seguimos buscando…" if voucher_hora else None)
    if not voucher_hora:
        return None, "Vemos más de un pago parecido. Escribe la hora que dice tu voucher."
    ref = _voucher_dt(voucher_hora)
    buenos.sort(key=lambda x: abs((x[0] - ref).total_seconds()))
    segundo = abs((buenos[1][0] - ref).total_seconds())
    if segundo > 120:
        return buenos[0][1], None
    return None, VERIFICANDO


def _tokens_nombre(s: str | None) -> list[str]:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return [t for t in re.split(r"[^a-z0-9ñ]+", s) if t]


def _mismo_titular(nombre_pago: str | None, titular: str | None) -> bool:
    """Match tolerante con nombres truncados por Yape ('Erwin Sch*' ~ 'Erwin Schipper').
    Exige dos palabras compatibles (una puede ser prefijo de la otra) o mismo primer nombre."""
    A, B = _tokens_nombre(nombre_pago), _tokens_nombre(titular)
    if not A or not B:
        return False
    def calza(x, ys):
        return any(x == y or (len(x) >= 3 and y.startswith(x)) or (len(y) >= 3 and x.startswith(y))
                   for y in ys)
    comunes = sum(1 for x in A if calza(x, B))
    return comunes >= 2 or (A[0] == B[0] and comunes >= 1)


async def _aviso_otro_monto(d: Demo) -> str | None:
    """¿Llegó un pago de ESTE titular pero con otro monto (ej. pagó S/ 2 en vez de S/ 1)?
    Devuelve el texto para la pantalla, o None. Lectura; no reclama nada."""
    await limites.pagook.tomar()
    # `desde` SIN zona = UTC, igual que registrado_en y recibido_en en PagoOK.
    desde = (d.registrado_en - timedelta(seconds=90)).isoformat()
    status, r = await pagook.pagos_recientes(desde)
    if status != 200:
        return None
    demo_monto = float(pagook.MONTO_DEMO)
    for p in r.get("pagos") or []:
        try:
            m = float(p.get("monto"))
        except (TypeError, ValueError):
            continue
        if abs(m - demo_monto) < 0.005:
            continue  # es S/ 1.00: ese sí es el pago de la demo, no "otro monto"
        hora = str(p.get("hora") or "").replace("Z", "+00:00")
        try:
            cuando = datetime.fromisoformat(hora)
        except ValueError:
            continue
        if cuando.tzinfo is not None:
            cuando = cuando.astimezone(timezone.utc).replace(tzinfo=None)
        if cuando < d.registrado_en - timedelta(seconds=60):
            continue
        if _mismo_titular(p.get("nombre"), d.titular):
            return (f"Recibimos S/ {m:.2f} tuyo, pero esta demo es de S/ 1.00. "
                    "Vuelve a yapear S/ 1.00 para continuar.")
    return None


async def _vigilar_pago(demo_id: str) -> bool:
    """Devuelve True cuando el pago quedó confirmado y marcado como usado."""
    referencia = f"DEMO-{demo_id}"
    proximo_otro = config.ahora()   # cada cuánto chequear un pago del mismo titular con otro monto
    while True:
        d = _leer(demo_id)
        if d.estado != "esperando_pago":
            return d.estado in ACTIVOS
        edad = (config.ahora() - d.registrado_en).total_seconds()
        if edad > config.VENTANA_PAGO_SEG:
            _guardar(demo_id, estado="expirado", aviso_monto=None,
                     mensaje=f"Pasaron {config.VENTANA_PAGO_SEG // 60} minutos y no vimos tu pago. "
                             "Si ya pagaste, avísale al expositor.")
            return False
        intervalo = 3 if edad < 180 else 5

        await limites.pagook.tomar()
        hora = d.voucher_hora
        if hora:
            # ±15 min: la hora del voucher se escribe a mano (solo HH:MM) y el banco puede
            # entregar el aviso con algunos minutos de retraso. Antes era ±3 y se caía fácil.
            declarada, ventana = _voucher_iso(hora), 15
        else:
            declarada, ventana = config.iso_lima(config.ahora()), 10
        status, r = await pagook.consultar(f"{d.monto:.2f}", declarada, ventana, d.titular)
        cambios = {"consultas_pagook": (d.consultas_pagook or 0) + 1}
        log.info("demo %s PagoOK consultar monto=%s declarada=%s ventana=%s nombre=%r -> HTTP %s nivel=%s "
                 "motivo=%s pago=%s candidatos=%s ya_usados=%s", demo_id, f"{d.monto:.2f}", declarada, ventana,
                 d.titular, status, r.get("nivel"), r.get("motivo"),
                 _resumen([r["pago"]] if r.get("pago") else []), _resumen(r.get("candidatos")),
                 _resumen(r.get("pagos_ya_usados")))

        if status != 200:
            # 401/404/422/5xx/red: el detalle técnico va al log y a /sala; al cliente, un texto tranquilo.
            incidente(demo_id, "PagoOK", "consultar", status, r)
            cambios["mensaje"] = VERIFICANDO
            _guardar(demo_id, **cambios)
            await _dormir(demo_id, 15 if status == 429 else intervalo)
            continue

        nivel = r.get("nivel")
        cambios["nivel"] = nivel
        elegido = None
        if nivel == "alta" and _aceptable(r.get("pago"), d):
            elegido = r["pago"]
        elif nivel == "media":
            elegido, mensaje = elegir_candidato(r.get("candidatos") or [], d.registrado_en, hora)
            if not elegido:
                cambios["mensaje"] = mensaje
        elif hora:
            cambios["mensaje"] = f"Aún no aparece a las {hora}, seguimos buscando…"
        else:
            cambios["mensaje"] = None

        # ¿Pagó pero con otro monto (ej. S/ 2 en vez de S/ 1)? Se chequea cada ~20 s para no
        # duplicar el gasto de consultas. Va en su propia columna `aviso_monto` (persiste hasta
        # que el visitante lo cierre o llegue el pago correcto); NO pisa el `mensaje` de estado.
        if not elegido and config.ahora() >= proximo_otro:
            proximo_otro = config.ahora() + timedelta(seconds=20)
            cambios["aviso_monto"] = await _aviso_otro_monto(d)

        if not elegido and _leer(demo_id).voucher_hora != hora:
            # Llegó una hora de voucher mientras consultábamos: este resultado ya no aplica.
            _guardar(demo_id, consultas_pagook=cambios["consultas_pagook"])
            continue

        if elegido:
            st, u = await _marcar(demo_id, elegido, referencia)
            if st == 200:
                pago_en = config.desde_iso(elegido.get("fecha_hora"))
                detectado = config.ahora()
                if not pago_en or pago_en > detectado:
                    pago_en = detectado
                _guardar(demo_id, **cambios, estado="pago_confirmado", mensaje=None, aviso_monto=None,
                         pago_en=pago_en, pago_detectado_en=detectado,
                         pagook_pago_id=elegido["pago_id"], pago_canal=elegido.get("canal"),
                         pagook_contraparte=(elegido.get("contraparte") or "")[:150])
                return True
            if st == 409:
                cambios["mensaje"] = "Ese pago ya se usó en otra demostración. Seguimos buscando el tuyo…"
            else:
                incidente(demo_id, "PagoOK", "marcar-usado", st, u)
                cambios["mensaje"] = VERIFICANDO
                intervalo = 2
        _guardar(demo_id, **cambios)
        await _dormir(demo_id, intervalo)


async def _marcar(demo_id: str, pago: dict, referencia: str) -> tuple[int, dict]:
    """marcar-usado con el candado de S/ 1.00: un pago de otro monto nunca se marca."""
    try:
        return await pagook.marcar_usado(pago, referencia)
    except pagook.MontoNoPermitido as e:
        incidente(demo_id, "demo-psp", "marcar-usado bloqueado", -1, str(e))
        return -1, {}


async def _vigilar_pago_caja(demo_id: str) -> bool:
    """Clínica: el pago de S/ 1.00 de esta persona es de esta caja (lo distingue el nombre).

    Reserva de caja y detección del pago están DESACOPLADAS: la caja se libera sola al vencer
    `hasta` (reserva + gracia), pero seguimos buscando el pago hasta `DETECCION_SEG`, así quien
    tarda varios minutos en volver de Yape igual se confirma sin tener la caja tomada tanto rato.
    """
    referencia = f"DEMO-{demo_id}"
    proximo_otro = config.ahora()   # cada cuánto chequear un pago del mismo titular con otro monto
    while True:
        d = _leer(demo_id)
        if d.estado != "esperando_pago":
            return d.estado in ACTIVOS
        ahora = config.ahora()
        reserva_viva = ahora <= d.reserva_hasta + timedelta(seconds=clinica.GRACIA_SEG)
        if ahora > d.registrado_en + timedelta(seconds=clinica.DETECCION_SEG):
            _guardar(demo_id, estado="expirado", aviso_monto=None,
                     mensaje=f"Pasaron {clinica.DETECCION_SEG // 60} minutos y no vimos tu pago. "
                             "Si ya pagaste, avísale al expositor.")
            clinica.liberar(demo_id)
            return False

        await limites.pagook.tomar()
        # Ventana amplia alrededor de AHORA: cubre desde el registro hasta ahora, de modo que un
        # pago que llega tarde (ya liberada la caja) igual aparece. El filtro fino lo hace elegir_pago.
        status, r = await pagook.consultar(f"{d.monto:.2f}", config.iso_lima(ahora),
                                           clinica.DETECCION_SEG // 60, d.titular or "")
        cambios = {"consultas_pagook": (d.consultas_pagook or 0) + 1}
        if status != 200:
            incidente(demo_id, "PagoOK", "consultar", status, r)
            _guardar(demo_id, **cambios, mensaje=VERIFICANDO)
            await _dormir(demo_id, 15 if status == 429 else (3 if reserva_viva else 6))
            continue

        cambios["nivel"] = r.get("nivel")
        candidatos = ([r["pago"]] if r.get("pago") else []) + (r.get("candidatos") or [])
        # `fin = ahora`: acepta pagos hasta el momento actual, no solo dentro de la reserva.
        elegido = clinica.elegir_pago(candidatos, d.registrado_en, ahora)
        log.info("demo %s %s PagoOK nivel=%s candidatos=%s elegido=%s", demo_id, d.caja, r.get("nivel"),
                 _resumen(candidatos), elegido and elegido.get("pago_id"))
        if elegido:
            st, u = await _marcar(demo_id, elegido, referencia)
            if st == 200:
                detectado = config.ahora()
                pago_en = config.desde_iso(elegido.get("fecha_hora"))
                if not pago_en or pago_en > detectado:
                    pago_en = detectado
                _guardar(demo_id, **cambios, estado="pago_confirmado", mensaje=None, aviso_monto=None,
                         pago_en=pago_en, pago_detectado_en=detectado, pagook_pago_id=elegido["pago_id"],
                         pago_canal=elegido.get("canal"),
                         pagook_contraparte=(elegido.get("contraparte") or "")[:150])
                clinica.liberar(demo_id)
                await _avisar_whatsapp(demo_id)
                return True
            if st not in (409, -1):
                incidente(demo_id, "PagoOK", "marcar-usado", st, u)
            # 409: otra caja lo tomó primero; se sigue esperando el pago de esta.
        # ¿Pagó S/ 1.00 bien de nombre pero con otro monto? Mismo aviso persistente que /demo.
        if not elegido and config.ahora() >= proximo_otro:
            proximo_otro = config.ahora() + timedelta(seconds=20)
            cambios["aviso_monto"] = await _aviso_otro_monto(d)
        _guardar(demo_id, **cambios, mensaje=None)
        # Mientras la caja está reservada, mira seguido (2 s); ya liberada, más espaciado (6 s).
        await _dormir(demo_id, 2 if reserva_viva else 6)


async def _avisar_whatsapp(demo_id: str):
    """Best-effort: "Pago confirmado: S/ 1.00 — Caja Farmacia, Clínica Demo". Solo con plantilla aprobada."""
    d = _leer(demo_id)
    if not d.whatsapp or d.whatsapp_enviado or not whatsapp.configurado():
        return
    ok = await whatsapp.enviar_pago_confirmado(d.whatsapp, f"{d.monto:.2f}", clinica.CAJAS[d.caja]["nombre"],
                                               clinica.NEGOCIO)
    if ok:
        _guardar(demo_id, whatsapp_enviado=True)
    else:
        incidente(demo_id, "WhatsApp", "pago confirmado", 0)


def concepto(d: Demo) -> str:
    return clinica.CAJAS[d.caja]["concepto"] if d.caja else rubro(d.rubro)["concepto"]


# --- Paso 3: el comprobante --------------------------------------------------------
def leyenda(d: Demo, emitido_en: datetime) -> str:
    billetera = {"yape": "Yape", "plin": "Plin"}.get(d.billetera, "billetera digital")
    n = max(0, round((emitido_en - d.pago_en).total_seconds()))
    return "\n".join([
        f"Pago por {billetera} recibido el {config.fecha_hora_lima(d.pago_en)}. "
        f"Datos registrados el {config.fecha_hora_lima(d.registrado_en)}.",
        "Pago validado automáticamente por pagoOK, sin captura de voucher ni revisión manual.",
        f"Comprobante emitido en {n} s desde el pago con Facturalo.pro.",
    ])


def _datos_comprobante(c: dict) -> dict:
    return {"facturalo_id": c.get("id"), "numero_formato": c.get("numero_formato"),
            "codigo_sunat": None if c.get("codigo_sunat") is None else str(c["codigo_sunat"]),
            "mensaje_sunat": (c.get("mensaje_sunat") or "")[:500] or None}


async def _emitir(demo_id: str) -> bool:
    d = _leer(demo_id)
    ref = referencia(d)
    intento = 0
    while True:
        intento += 1
        # Idempotencia: la API no deduplica; antes de (re)intentar, ¿ya existe?
        if d.estado == "emitiendo" or intento > 1:
            st, b = await facturalo.buscar_referencia(ref)
            if st == 200 and b.get("exito") and (b.get("comprobante") or {}).get("id"):
                _guardar(demo_id, estado="enviando", mensaje=None,
                         emitido_en=d.emitido_en or config.ahora(), **_datos_comprobante(b["comprobante"]))
                return True

        emitido_en = config.ahora()
        texto = leyenda(d, emitido_en)
        _guardar(demo_id, estado="emitiendo", emitido_en=emitido_en, leyenda=texto,
                 mensaje="Emitiendo tu comprobante…" if intento == 1 else "Reintentando la emisión…")
        d = _leer(demo_id)
        cuerpo = facturalo.cuerpo_emision(d, concepto(d), texto, ref)
        log.info("demo %s Facturalo emitir tipo=%s serie=%s cliente=%s/%s total=%s ref=%s", demo_id,
                 cuerpo["tipo_comprobante"], cuerpo.get("serie", "(auto)"), cuerpo["cliente"]["tipo_documento"],
                 cuerpo["cliente"]["numero_documento"], d.monto, ref)
        st, b = await facturalo.emitir(cuerpo)
        log.info("demo %s Facturalo emitir -> HTTP %s %s", demo_id, st, json.dumps(b, ensure_ascii=False)[:400])

        if st == 200 and b.get("exito"):
            _guardar(demo_id, estado="enviando", mensaje=None, **_datos_comprobante(b.get("comprobante") or {}))
            return True
        if st == 0 or st >= 500 or st == 429:
            incidente(demo_id, "Facturalo", f"emitir (intento {intento})", st, b or None)
            _guardar(demo_id, mensaje="La conexión con Facturalo está lenta. Reintentando solo…")
            await asyncio.sleep(min(2 * intento, 10))
            continue
        # 4xx: datos o credencial. No se reintenta a ciegas.
        error = b.get("error") or b.get("detail") or f"HTTP {st}"
        incidente(demo_id, "Facturalo", "emitir", st, b or None)
        _guardar(demo_id, estado="error_emision",
                 mensaje=f"Facturalo no aceptó los datos del comprobante: {error}. Tu pago quedó registrado; "
                         "el expositor lo resolverá contigo.")
        return False


async def _esperar_sunat(demo_id: str):
    """GET /{id} cada 1 s hasta estado terminal (luego cada 5 s si SUNAT demora)."""
    inicio = config.ahora()
    while True:
        d = _leer(demo_id)
        if d.estado != "enviando" or not d.facturalo_id:
            return
        edad = (config.ahora() - inicio).total_seconds()
        if edad > 30 * 60:
            _guardar(demo_id, mensaje="SUNAT está tardando más de lo normal. Tu comprobante llegará igual.")
            return
        st, b = await facturalo.consultar(d.facturalo_id)
        c = (b or {}).get("comprobante") or {}
        if st != 200:
            incidente(demo_id, "Facturalo", f"GET /comprobantes/{d.facturalo_id}", st, b or None)
        if st == 200 and c:
            estado = c.get("estado")
            if estado in TERMINALES_LOG:
                log.info("demo %s Facturalo %s -> %s codigo=%s %s", demo_id, c.get("numero_formato"), estado,
                         c.get("codigo_sunat"), (c.get("mensaje_sunat") or "")[:300])
            datos = _datos_comprobante(c)
            datos.pop("facturalo_id", None)
            if estado in facturalo.ACEPTADOS:
                ahora = config.ahora()
                _guardar(demo_id, estado="aceptado", aceptado_en=ahora, mensaje=None,
                         segundos=max(1, round((ahora - d.pago_en).total_seconds())), **datos)
                return
            if estado in ("rechazado", "anulado"):
                _guardar(demo_id, estado="rechazado",
                         mensaje=f"SUNAT rechazó el comprobante: {c.get('mensaje_sunat') or 'sin detalle'}",
                         **datos)
                return
            _guardar(demo_id, mensaje="SUNAT está revisando tu comprobante…" if edad > 15 else None, **datos)
        await asyncio.sleep(1 if edad < 120 else 5)


def hora_voucher_valida(texto: str) -> bool:
    return bool(re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", texto or ""))
