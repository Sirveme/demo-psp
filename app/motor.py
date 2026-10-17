"""Vigilancia del pago (PagoOK) y emisión del comprobante (Facturalo), una tarea por demostración.

Todo el estado vive en la fila `demos`. La tarea solo es el "reloj" que la avanza: si el proceso
se reinicia, `asegurar_tarea()` la relanza desde lo que diga la base.
"""
import asyncio
import json
import logging
import re
from datetime import datetime, timedelta

from app import config, limites
from app.db import Demo, Sesion
from app.rubros import rubro
from app.servicios import facturalo, pagook

log = logging.getLogger("demo.motor")

ACTIVOS = ("esperando_pago", "pago_confirmado", "emitiendo", "enviando")
_tareas: dict[str, asyncio.Task] = {}


def asegurar_tarea(demo_id: str, estado: str):
    if estado not in ACTIVOS:
        return
    t = _tareas.get(demo_id)
    if t and not t.done():
        return
    _tareas[demo_id] = asyncio.create_task(_ciclo(demo_id), name=f"demo-{demo_id}")


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
        for k, v in campos.items():
            setattr(d, k, v)
        s.commit()


async def _ciclo(demo_id: str):
    try:
        d = _leer(demo_id)
        if d.estado == "esperando_pago":
            if not await _vigilar_pago(demo_id):
                return
            d = _leer(demo_id)
        if d.estado in ("pago_confirmado", "emitiendo"):
            if not await _emitir(demo_id):
                return
        await _esperar_sunat(demo_id)
    except Exception:
        log.exception("demo %s", demo_id)
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


async def _vigilar_pago(demo_id: str) -> bool:
    """Devuelve True cuando el pago quedó confirmado y marcado como usado."""
    referencia = f"DEMO-{demo_id}"
    while True:
        d = _leer(demo_id)
        if d.estado != "esperando_pago":
            return d.estado in ACTIVOS
        edad = (config.ahora() - d.registrado_en).total_seconds()
        if edad > config.VENTANA_PAGO_SEG:
            _guardar(demo_id, estado="expirado",
                     mensaje="Pasaron 10 minutos y no vimos tu pago. Si ya pagaste, avísale al expositor.")
            return False
        intervalo = 3 if edad < 180 else 5

        await limites.pagook.tomar()
        if d.voucher_hora:
            declarada, ventana = _voucher_iso(d.voucher_hora), 3
        else:
            declarada, ventana = config.iso_lima(config.ahora()), 10
        status, r = await pagook.consultar(f"{d.monto:.2f}", declarada, ventana, d.titular)
        cambios = {"consultas_pagook": (d.consultas_pagook or 0) + 1}

        if status != 200:
            if status == 429:
                intervalo = 15
            cambios["mensaje"] = ("La conexión con PagoOK está lenta. Seguimos vigilando tu pago…"
                                  if status in (0, 429) or status >= 500 else
                                  "No pudimos consultar PagoOK. Avísale al expositor.")
            log.warning("demo %s: PagoOK HTTP %s", demo_id, status)
            _guardar(demo_id, **cambios)
            await asyncio.sleep(intervalo)
            continue

        nivel = r.get("nivel")
        cambios["nivel"] = nivel
        elegido = None
        if nivel == "alta" and _aceptable(r.get("pago"), d):
            elegido = r["pago"]
        elif nivel == "media":
            if d.voucher_hora:
                # Con la hora del voucher (ventana de 3 min): si un único candidato es del titular,
                # posterior al registro, es ese.
                buenos = [p for p in r.get("candidatos") or []
                          if p.get("coincide_nombre") is True and _aceptable(p, d)]
                if len(buenos) == 1:
                    elegido = buenos[0]
                else:
                    cambios["mensaje"] = ("Aún vemos más de un pago parecido. Revisa la hora de tu voucher "
                                          "o pídele ayuda al expositor.")
            else:
                cambios["mensaje"] = "Vemos más de un pago parecido. Escribe la hora que dice tu voucher."
        else:
            cambios["mensaje"] = None

        if elegido:
            st, u = await pagook.marcar_usado(elegido["pago_id"], referencia)
            if st == 200:
                pago_en = config.desde_iso(elegido.get("fecha_hora"))
                detectado = config.ahora()
                if not pago_en or pago_en > detectado:
                    pago_en = detectado
                _guardar(demo_id, **cambios, estado="pago_confirmado", mensaje=None,
                         pago_en=pago_en, pago_detectado_en=detectado,
                         pagook_pago_id=elegido["pago_id"],
                         pagook_contraparte=(elegido.get("contraparte") or "")[:150])
                return True
            if st == 409:
                cambios["mensaje"] = "Ese pago ya se usó en otra demostración. Seguimos buscando el tuyo…"
            else:
                cambios["mensaje"] = "Encontramos tu pago; lo estamos confirmando…"
                log.warning("demo %s: marcar-usado HTTP %s %s", demo_id, st, u)
                intervalo = 2
        _guardar(demo_id, **cambios)
        await asyncio.sleep(intervalo)


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
    referencia = f"DEMO-{demo_id}"
    intento = 0
    while True:
        intento += 1
        # Idempotencia: la API no deduplica; antes de (re)intentar, ¿ya existe?
        if d.estado == "emitiendo" or intento > 1:
            st, b = await facturalo.buscar_referencia(referencia)
            if st == 200 and b.get("exito") and (b.get("comprobante") or {}).get("id"):
                _guardar(demo_id, estado="enviando", mensaje=None,
                         emitido_en=d.emitido_en or config.ahora(), **_datos_comprobante(b["comprobante"]))
                return True

        emitido_en = config.ahora()
        texto = leyenda(d, emitido_en)
        _guardar(demo_id, estado="emitiendo", emitido_en=emitido_en, leyenda=texto,
                 mensaje="Emitiendo tu comprobante…" if intento == 1 else "Reintentando la emisión…")
        d = _leer(demo_id)
        concepto = rubro(d.rubro)["concepto"]
        st, b = await facturalo.emitir(facturalo.cuerpo_emision(d, concepto, texto))

        if st == 200 and b.get("exito"):
            _guardar(demo_id, estado="enviando", mensaje=None, **_datos_comprobante(b.get("comprobante") or {}))
            return True
        if st == 0 or st >= 500 or st == 429:
            log.warning("demo %s: Facturalo HTTP %s, reintento %s", demo_id, st, intento)
            _guardar(demo_id, mensaje="La conexión con Facturalo está lenta. Reintentando solo…")
            await asyncio.sleep(min(2 * intento, 10))
            continue
        # 4xx: datos o credencial. No se reintenta a ciegas.
        error = b.get("error") or b.get("detail") or f"HTTP {st}"
        log.error("demo %s: Facturalo rechazó la emisión: %s %s", demo_id, st, json.dumps(b)[:300])
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
        if st == 200 and c:
            estado = c.get("estado")
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
