"""Cliente de la API v1 de Facturalo.pro, contrato docs/api-v1-emision.md.

Emisión asíncrona: POST /comprobantes devuelve 'encolado'; el CDR se obtiene con
GET /comprobantes/{id}. Devuelven (status, body); status 0 = red o timeout.
"""
import logging
import re
from decimal import ROUND_HALF_UP, Decimal

import httpx

from app import config
from app.servicios import mocks

log = logging.getLogger("demo.facturalo")
TIMEOUT = 8.0
ACEPTADOS = ("aceptado", "aceptado_con_observaciones")
TERMINALES = ACEPTADOS + ("rechazado", "anulado")


def configurado() -> bool:
    return config.MOCK or bool(config.FACTURALO_API_KEY and config.FACTURALO_API_SECRET)


def problema_series() -> str | None:
    """SUNAT exige B### para boletas y F### para facturas. Otra serie = rechazo seguro (Client.0151)."""
    if config.FACTURALO_SERIE_BOLETA and not re.fullmatch(r"B[A-Z0-9]{3}", config.FACTURALO_SERIE_BOLETA):
        return f"FACTURALO_SERIE_BOLETA={config.FACTURALO_SERIE_BOLETA} no es válida (debe ser B + 3, p. ej. B001)"
    if config.FACTURALO_SERIE_FACTURA and not re.fullmatch(r"F[A-Z0-9]{3}", config.FACTURALO_SERIE_FACTURA):
        return f"FACTURALO_SERIE_FACTURA={config.FACTURALO_SERIE_FACTURA} no es válida (debe ser F + 3, p. ej. F001)"
    return None


def base() -> str:
    """URL final <host>/api/v1, sin duplicar /api/v1 si la variable ya lo trae."""
    b = config.FACTURALO_BASE_URL.rstrip("/")
    if b.endswith("/api/v1"):
        b = b[: -len("/api/v1")]
    return b + "/api/v1"


def _headers() -> dict:
    return {"X-API-Key": config.FACTURALO_API_KEY, "X-API-Secret": config.FACTURALO_API_SECRET,
            "Content-Type": "application/json"}


async def _pedir(metodo: str, ruta: str, cuerpo=None, timeout=TIMEOUT) -> tuple[int, dict]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as cli:
            r = await cli.request(metodo, base() + ruta, headers=_headers(), json=cuerpo)
    except Exception as e:
        log.warning("Facturalo %s %s: %s", metodo, ruta, e)
        return 0, {}
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {}


def _total_servidor(precio: float) -> float:
    """Mismo cálculo que Facturalo (src/api/v1/comprobantes.py) para un ítem gravado de cantidad 1."""
    base = round(precio, 2)
    return round(base + round(base * 0.18, 2), 2)


def precio_sin_igv(total: Decimal) -> Decimal | None:
    """Precio unitario SIN IGV (2 decimales, así lo guarda Facturalo) que da exactamente `total`.

    None si ningún precio de 2 decimales llega a ese total (p. ej. S/ 1.02): no se puede facturar
    al céntimo lo que la persona pagó.
    """
    c = Decimal("0.01")
    aprox = (total / Decimal("1.18")).quantize(c, ROUND_HALF_UP)
    for p in (aprox, aprox + c, aprox - c):
        if p > 0 and _total_servidor(float(p)) == float(total):
            return p
    return None


def montos_cercanos(total: Decimal) -> list[str]:
    """Los montos facturables al céntimo más cercanos por debajo y por encima."""
    c = Decimal("0.01")
    abajo = next((total - c * i for i in range(1, 6) if precio_sin_igv(total - c * i)), None)
    arriba = next((total + c * i for i in range(1, 6) if precio_sin_igv(total + c * i)), None)
    return [f"{m:.2f}" for m in (abajo, arriba) if m is not None and m >= 1]


def cuerpo_emision(demo, concepto: str, leyenda: str, referencia: str) -> dict:
    tipo = "01" if demo.tipo == "factura" else "03"
    cliente = {"tipo_documento": demo.doc_tipo, "numero_documento": demo.doc_numero,
               "razon_social": demo.cliente_nombre[:200]}
    if demo.cliente_direccion:
        cliente["direccion"] = demo.cliente_direccion[:200]
    cuerpo = {
        "tipo_comprobante": tipo,
        "cliente": cliente,
        "items": [{
            "descripcion": concepto[:250],
            "cantidad": 1,
            "unidad_medida": "ZZ",
            "precio_unitario": float(precio_sin_igv(Decimal(demo.monto))),
            "tipo_afectacion_igv": "10",
        }],
        "observaciones": leyenda[:500],
        "forma_pago": "Contado",
        "enviar_email": False,
        "referencia_externa": referencia,
    }
    serie = config.FACTURALO_SERIE_FACTURA if tipo == "01" else config.FACTURALO_SERIE_BOLETA
    if serie:
        cuerpo["serie"] = serie
    return cuerpo


async def emitir(cuerpo: dict) -> tuple[int, dict]:
    if config.MOCK:
        return mocks.facturalo_emitir(cuerpo)
    return await _pedir("POST", "/comprobantes", cuerpo)


async def consultar(comprobante_id: str) -> tuple[int, dict]:
    if config.MOCK:
        return mocks.facturalo_consultar(comprobante_id)
    return await _pedir("GET", f"/comprobantes/{comprobante_id}")


async def buscar_referencia(referencia: str) -> tuple[int, dict]:
    if config.MOCK:
        return mocks.facturalo_buscar(referencia)
    return await _pedir("GET", f"/comprobantes/buscar/referencia/{referencia}")


async def pdf(comprobante_id: str, formato: str = "A4") -> tuple[int, bytes]:
    if config.MOCK:
        return 200, mocks.PDF_MINIMO
    try:
        async with httpx.AsyncClient(timeout=15.0) as cli:
            r = await cli.get(f"{base()}/comprobantes/{comprobante_id}/pdf",
                              headers=_headers(), params={"formato": formato})
        return r.status_code, r.content
    except Exception as e:
        log.warning("Facturalo pdf: %s", e)
        return 0, b""
