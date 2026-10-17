"""demo.perusistemas.pro — "Paga y recibe tu comprobante" (PagoOK + Facturalo)."""
import io
import logging
import re
import secrets
from contextlib import asynccontextmanager
from datetime import timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import quote

import segno
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import config, limites, motor
from app.db import Demo, Interesado, Sesion, crear_tablas
from app.rubros import existe, rubro, todos
from app.servicios import facturalo, identidad, mocks, pagook

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("demo")
RAIZ = Path(__file__).resolve().parent.parent
ALFABETO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


@asynccontextmanager
async def vida(app: FastAPI):
    crear_tablas()
    motor.reanudar_pendientes()
    if config.MOCK:
        log.warning("DEMO_MOCK=1: PagoOK, Facturalo y RUC/DNI están SIMULADOS")
    yield


app = FastAPI(title="Demo PSP", lifespan=vida, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=RAIZ / "static"), name="static")
plantillas = Jinja2Templates(directory=RAIZ / "templates")


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "")) or "?"


def _limite(tipo: str, request: Request):
    if not limites.permitido(tipo, _ip(request)):
        raise HTTPException(429, "Demasiados intentos desde esta red. Espera un minuto e intenta otra vez.")


def _url_publica(request: Request, ruta: str) -> str:
    if config.PUBLIC_URL:
        return config.PUBLIC_URL + ruta
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}{ruta}"


def _qr_yape() -> str | None:
    for nombre in ("yape-qr.png", "yape-qr.jpg", "yape-qr.jpeg", "yape-qr.webp"):
        if (RAIZ / "static" / "img" / nombre).exists():
            return f"/static/img/{nombre}"
    return None


@app.exception_handler(HTTPException)
async def _error_simple(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"ok": False, "error": exc.detail}, status_code=exc.status_code)
    return HTMLResponse(f"<p style='font:18px system-ui;padding:24px'>{exc.detail}</p>", status_code=exc.status_code)


# --- Páginas -------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def portada(request: Request):
    return plantillas.TemplateResponse(request, "inicio.html", {"rubros": todos()})


@app.get("/healthz")
async def salud():
    return {"ok": True, "mock": config.MOCK, "pagook": pagook.configurado(),
            "facturalo": facturalo.configurado(), "apis_net": bool(config.APIS_NET_TOKEN),
            "series": facturalo.problema_series() or "ok",
            "serie_boleta": config.FACTURALO_SERIE_BOLETA or "(auto)",
            "serie_factura": config.FACTURALO_SERIE_FACTURA or "(auto)",
            "pagook_consultas_ultimo_minuto": limites.pagook.usadas()}


@app.get("/sala", response_class=HTMLResponse)
async def sala(request: Request, k: str = ""):
    if not config.SALA_CLAVE or not secrets.compare_digest(k, config.SALA_CLAVE):
        raise HTTPException(404, "No encontrado")
    return plantillas.TemplateResponse(request, "sala.html", {"clave": k})


# --- API: consultas RUC / DNI -------------------------------------------------------
@app.get("/api/consulta/{tipo}/{numero}")
async def api_consulta(tipo: str, numero: str, request: Request):
    numero = re.sub(r"\D", "", numero)
    if tipo == "ruc":
        if not identidad.ruc_valido(numero):
            return {"ok": False, "error": "El RUC debe tener 11 dígitos y empezar con 10, 15, 17 o 20."}
        _limite("consulta", request)
        datos = await identidad.consultar_ruc(numero)
    elif tipo == "dni":
        if not identidad.dni_valido(numero):
            return {"ok": False, "error": "El DNI debe tener 8 dígitos."}
        _limite("consulta", request)
        datos = await identidad.consultar_dni(numero)
    else:
        raise HTTPException(404, "No encontrado")
    if not datos:
        return {"ok": False, "manual": True, "error": "No pudimos consultarlo ahora. Escríbelo a mano, por favor."}
    return {"ok": True, "nombre": datos.get("nombre"), "direccion": datos.get("direccion", ""),
            "estado": datos.get("estado", ""), "condicion": datos.get("condicion", "")}


# --- API: demostraciones ---------------------------------------------------------------
def _limpio(texto, largo: int) -> str:
    return re.sub(r"\s+", " ", str(texto or "")).strip()[:largo]


@app.post("/api/demos")
async def crear_demo(request: Request):
    _limite("demo", request)
    try:
        b = await request.json()
    except Exception:
        raise HTTPException(400, "Datos inválidos.")
    errores = {}

    slug = b.get("rubro") if existe(b.get("rubro") or "") else "otro"
    tipo = "factura" if b.get("tipo") == "factura" else "boleta"
    doc = re.sub(r"\D", "", str(b.get("documento") or ""))
    nombre = _limpio(b.get("nombre"), 200).upper()
    direccion = _limpio(b.get("direccion"), 250)
    titular = _limpio(b.get("titular"), 120)
    billetera = b.get("billetera") if b.get("billetera") in ("yape", "plin", "otra") else "yape"

    if tipo == "factura":
        if not identidad.ruc_valido(doc):
            errores["documento"] = "Escribe un RUC válido de 11 dígitos (empieza con 10, 15, 17 o 20)."
        doc_tipo = "6"
    elif doc:
        if not identidad.dni_valido(doc):
            errores["documento"] = "El DNI debe tener 8 dígitos (o déjalo vacío)."
        doc_tipo = "1"
    else:
        doc_tipo, doc = "0", "0"
    if len(nombre) < 3:
        errores["nombre"] = "Escribe la razón social." if tipo == "factura" else "Escribe tu nombre."
    if len(titular.split()) < 2:
        errores["titular"] = "Escribe nombre y apellido del titular del Yape o Plin."
    try:
        monto = Decimal(str(b.get("monto") or "1").replace(",", ".")).quantize(Decimal("0.01"))
    except InvalidOperation:
        monto = Decimal("0")
    if monto < Decimal("1.00"):
        errores["monto"] = "El monto mínimo es S/ 1.00."
    elif monto > config.MONTO_MAXIMO:
        errores["monto"] = f"Para la demo, el monto máximo es S/ {config.MONTO_MAXIMO:.2f}."
    elif facturalo.precio_sin_igv(monto) is None:
        opciones = " o ".join(f"S/ {m}" for m in facturalo.montos_cercanos(monto))
        errores["monto"] = f"Con IGV, S/ {monto:.2f} no cuadra al céntimo en el comprobante. Usa {opciones}."
    if errores:
        return JSONResponse({"ok": False, "errores": errores}, status_code=422)
    if not (pagook.configurado() and facturalo.configurado()):
        raise HTTPException(503, "La demo aún no está configurada. Avísale al expositor.")
    if (problema := facturalo.problema_series()):
        # Mejor no cobrar que cobrar un comprobante que SUNAT va a rechazar.
        motor.incidente("-", "configuración", "series", -1, problema)
        raise HTTPException(503, "La demo está en mantenimiento por un momento. Avísale al expositor.")

    demo_id = "".join(secrets.choice(ALFABETO) for _ in range(10))
    with Sesion() as s:
        s.add(Demo(id=demo_id, rubro=slug, tipo=tipo, doc_tipo=doc_tipo, doc_numero=doc,
                   cliente_nombre=nombre, cliente_direccion=direccion or None, titular=titular,
                   monto=monto, billetera=billetera, ip=_ip(request), estado="esperando_pago",
                   registrado_en=config.ahora(), consultas_pagook=0))
        s.commit()
    motor.asegurar_tarea(demo_id, "esperando_pago")
    log.info("demo %s creada: %s %s S/ %s", demo_id, slug, tipo, monto)
    return {"ok": True, "id": demo_id}


def _demo_json(d: Demo, request: Request) -> dict:
    r = rubro(d.rubro)
    ahora = config.ahora()
    out = {
        "ok": True, "id": d.id, "estado": d.estado, "nivel": d.nivel, "mensaje": d.mensaje,
        "rubro": {"slug": r["slug"], "nombre": r["nombre"], "que_destraba": r["que_destraba"]},
        "tipo": d.tipo, "monto": f"{d.monto:.2f}", "billetera": d.billetera,
        "titular": d.titular, "cliente_nombre": d.cliente_nombre, "voucher_hora": d.voucher_hora,
        "esperando_seg": int((ahora - d.registrado_en).total_seconds()),
        "restante_seg": max(0, int(config.VENTANA_PAGO_SEG - (ahora - d.registrado_en).total_seconds())),
        "registrado": config.hora_lima(d.registrado_en),
        "pago": config.hora_lima(d.pago_en),
        "aceptado": config.hora_lima(d.aceptado_en),
        "seg_registro_pago": round((d.pago_en - d.registrado_en).total_seconds()) if d.pago_en else None,
        "seg_pago_aceptado": d.segundos,
        "segundos": d.segundos,
        "emitiendo_seg": int((ahora - d.pago_en).total_seconds()) if d.pago_en and not d.aceptado_en else None,
        "numero_formato": d.numero_formato, "mensaje_sunat": d.mensaje_sunat, "leyenda": d.leyenda,
    }
    if d.facturalo_id:
        out["pdf"] = f"/c/{d.id}.pdf"
        out["descargar"] = f"/c/{d.id}.pdf?descargar=1"
        out["qr"] = f"/c/{d.id}/qr.svg"
    return out


@app.get("/api/demos/{demo_id}")
async def ver_demo(demo_id: str, request: Request):
    with Sesion() as s:
        d = s.get(Demo, demo_id)
    if not d:
        raise HTTPException(404, "No encontramos esa demostración.")
    motor.asegurar_tarea(d.id, d.estado)
    return _demo_json(d, request)


@app.post("/api/demos/{demo_id}/voucher")
async def hora_voucher(demo_id: str, request: Request):
    _limite("voucher", request)
    b = await request.json()
    hora = str(b.get("hora") or "").strip()
    if not motor.hora_voucher_valida(hora):
        return JSONResponse({"ok": False, "error": "Escribe la hora como 14:05."}, status_code=422)
    with Sesion() as s:
        d = s.get(Demo, demo_id)
        if not d:
            raise HTTPException(404, "No encontramos esa demostración.")
        if d.estado == "esperando_pago":
            d.voucher_hora = hora
            d.mensaje = f"Buscando tu pago a las {hora}…"
            s.commit()
    motor.despertar(demo_id)
    return {"ok": True}


@app.post("/api/interesados")
async def interesado(request: Request):
    _limite("interesado", request)
    b = await request.json()
    nombre = _limpio(b.get("nombre"), 120)
    celular = re.sub(r"\D", "", str(b.get("celular") or ""))[-9:]
    ruc = re.sub(r"\D", "", str(b.get("ruc") or ""))
    errores = {}
    if len(nombre) < 2:
        errores["nombre"] = "Escribe tu nombre."
    if not re.fullmatch(r"9\d{8}", celular):
        errores["celular"] = "Escribe un celular de 9 dígitos."
    if ruc and not identidad.ruc_valido(ruc):
        errores["ruc"] = "El RUC no es válido (o déjalo vacío)."
    if not b.get("acepta"):
        errores["acepta"] = "Necesitamos tu permiso para contactarte."
    if errores:
        return JSONResponse({"ok": False, "errores": errores}, status_code=422)
    slug = b.get("rubro") if existe(b.get("rubro") or "") else "otro"
    demo_id = str(b.get("demo_id") or "")[:16] or None
    with Sesion() as s:
        segundos = None
        if demo_id and (d := s.get(Demo, demo_id)):
            segundos = d.segundos
        s.add(Interesado(demo_id=demo_id, nombre=nombre, celular=celular, ruc=ruc or None, rubro=slug,
                         acepta_contacto=True, creado_en=config.ahora(), ip=_ip(request)))
        s.commit()
    texto = (f"Hola, soy {nombre}. Acabo de probar la demo de PagoOK + Facturalo"
             + (f" y mi comprobante salió en {segundos} segundos" if segundos else "")
             + f". Quiero esto en mi negocio ({rubro(slug)['nombre']})."
             + (f" Mi RUC es {ruc}." if ruc else ""))
    return {"ok": True, "whatsapp": f"https://wa.me/{config.WHATSAPP_VENTAS}?text={quote(texto)}"}


# --- Comprobante: PDF y QR -----------------------------------------------------------
@app.get("/c/{demo_id}.pdf")
async def ver_pdf(demo_id: str, descargar: int = 0, formato: str = "A4"):
    with Sesion() as s:
        d = s.get(Demo, demo_id)
    if not d or not d.facturalo_id:
        raise HTTPException(404, "Ese comprobante aún no está listo.")
    formato = formato.upper() if formato.upper() in ("A4", "A5", "TICKET") else "A4"
    st, contenido = await facturalo.pdf(d.facturalo_id, formato)
    if st != 200 or not contenido.startswith(b"%PDF"):
        raise HTTPException(502, "No pudimos traer el PDF en este momento. Intenta de nuevo en unos segundos.")
    nombre = f"{d.numero_formato or 'comprobante'}.pdf"
    modo = "attachment" if descargar else "inline"
    return Response(contenido, media_type="application/pdf",
                    headers={"Content-Disposition": f'{modo}; filename="{nombre}"'})


@app.get("/c/{demo_id}/qr.svg")
async def qr_pdf(demo_id: str, request: Request):
    url = _url_publica(request, f"/c/{demo_id}.pdf")
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=6, border=2, dark="#000000", light="#ffffff")
    return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=3600"})


# --- /sala --------------------------------------------------------------------------
@app.get("/api/sala")
async def api_sala(k: str = ""):
    if not config.SALA_CLAVE or not secrets.compare_digest(k, config.SALA_CLAVE):
        raise HTTPException(404, "No encontrado")
    hoy = config.a_lima(config.ahora()).replace(hour=0, minute=0, second=0, microsecond=0)
    desde = hoy.astimezone(timezone.utc).replace(tzinfo=None)
    with Sesion() as s:
        filas = (s.query(Demo).filter(Demo.estado == "aceptado", Demo.aceptado_en >= desde)
                 .order_by(Demo.aceptado_en.desc()).all())
        en_curso = s.query(Demo).filter(Demo.estado.in_(motor.ACTIVOS),
                                        Demo.registrado_en >= config.ahora() - timedelta(minutes=10)).count()
    segs = [f.segundos for f in filas if f.segundos]
    return {
        "ultimas": [{"nombre": (f.titular.split()[0] if f.titular else "").title(),
                     "rubro": rubro(f.rubro)["nombre"], "monto": f"{f.monto:.2f}",
                     "segundos": f.segundos, "hora": config.hora_lima(f.aceptado_en)} for f in filas[:12]],
        "total": len(filas),
        "promedio": round(sum(segs) / len(segs), 1) if segs else None,
        "record": min(segs) if segs else None,
        "en_curso": en_curso,
        # Errores técnicos de los últimos 15 min (los clientes solo ven "Estamos verificando tu pago").
        "incidentes": [{"hora": config.hora_lima(i["en"]), "demo": i["demo"], "servicio": i["servicio"],
                        "operacion": i["operacion"], "detalle": i["detalle"]}
                       for i in reversed(motor.incidentes)
                       if config.ahora() - i["en"] < timedelta(minutes=15)][:5],
    }


# --- Operación: reemitir sin pedir otro pago -----------------------------------------------
@app.post("/api/admin/demos/{demo_id}/reemitir")
async def reemitir(demo_id: str, k: str = ""):
    """Vuelve a emitir el comprobante de una demo cuyo pago ya está confirmado y marcado como usado.

    Usa una referencia_externa nueva (DEMO-{id}-R{n}) para no reutilizar el comprobante rechazado.
    Protegido con la misma clave de /sala.
    """
    if not config.SALA_CLAVE or not secrets.compare_digest(k, config.SALA_CLAVE):
        raise HTTPException(404, "No encontrado")
    if (problema := facturalo.problema_series()):
        raise HTTPException(409, f"Corrige la configuración antes de reemitir: {problema}")
    with Sesion() as s:
        d = s.get(Demo, demo_id)
        if not d:
            raise HTTPException(404, "No existe esa demo.")
        if not d.pagook_pago_id or not d.pago_en:
            raise HTTPException(409, "Esa demo no tiene un pago confirmado.")
        if d.estado == "aceptado":
            raise HTTPException(409, f"Ya está aceptado: {d.numero_formato}.")
        anterior = {"estado": d.estado, "numero": d.numero_formato, "codigo_sunat": d.codigo_sunat}
        d.reemisiones = (d.reemisiones or 0) + 1
        d.estado, d.mensaje = "pago_confirmado", None
        d.facturalo_id = d.numero_formato = d.codigo_sunat = d.mensaje_sunat = None
        d.emitido_en = d.aceptado_en = d.segundos = None
        s.commit()
        ref = motor.referencia(d)
    motor.reiniciar(demo_id)
    log.warning("demo %s reemitida (%s) desde %s", demo_id, ref, anterior)
    return {"ok": True, "demo": demo_id, "referencia_externa": ref, "anterior": anterior,
            "seguir": f"/api/demos/{demo_id}"}


# --- Solo local: simular un Yape --------------------------------------------------------
if config.MOCK:
    @app.post("/mock/yape")
    async def mock_yape(request: Request):
        b = await request.json()
        return mocks.registrar_pago(str(b.get("monto", "1.00")), b.get("nombre", ""), int(b.get("hace_seg", 0)))

    @app.post("/mock/pagook-falla")
    async def mock_falla(request: Request):
        b = await request.json()
        mocks.FALLAS.update(status=int(b.get("status", 500)), veces=int(b.get("veces", 1)))
        return {"ok": True}

    @app.post("/mock/facturalo-rechazo")
    async def mock_rechazo(request: Request):
        mocks.RECHAZOS["veces"] = int((await request.json()).get("veces", 1))
        return {"ok": True}

    @app.get("/mock/comprobante/{demo_id}")
    async def mock_comprobante(demo_id: str):
        with Sesion() as s:
            d = s.get(Demo, demo_id)
        c = mocks.comprobante(d.facturalo_id) if d and d.facturalo_id else None
        return {k: v for k, v in (c or {}).items() if not k.startswith("_")}


# --- Enlaces por rubro: /ecommerce, /restaurante, ... (al final, para no tapar otras rutas)
@app.get("/{slug}", response_class=HTMLResponse)
async def pagina_rubro(slug: str, request: Request):
    if not existe(slug):
        raise HTTPException(404, "No encontramos esa página.")
    return plantillas.TemplateResponse(request, "demo.html", {
        "r": rubro(slug), "yape": config.YAPE_NUMERO, "qr_yape": _qr_yape(),
        "monto_maximo": config.MONTO_MAXIMO, "whatsapp": config.WHATSAPP_VENTAS})
