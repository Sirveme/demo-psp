"""Cliente de PagoOK, contrato docs/consulta-pagos-v1.md.

  consultar()     -> POST /pagos/consultar
  marcar_usado()  -> POST /pagos/{pago_id}/marcar-usado

Devuelven (status, body). status 0 = error de red o timeout.

Candado de la demo: SOLO pagos de exactamente S/ 1.00. La credencial ve todos los pagos de
Perú Sistemas Pro; cualquier otro monto se rechaza aquí, antes de llamar a PagoOK.
"""
import logging

import httpx

from app import config
from app.servicios import mocks

log = logging.getLogger("demo.pagook")
MONTO_DEMO = "1.00"


class MontoNoPermitido(ValueError):
    """La demo intentó consultar o usar un pago que no es de S/ 1.00."""


def _exigir_monto_demo(monto) -> None:
    if f"{monto}".strip() != MONTO_DEMO:
        raise MontoNoPermitido(f"La demo solo trabaja con pagos de S/ {MONTO_DEMO} (recibió {monto!r})")
TIMEOUT = 8.0
SISTEMA = "Demo PSP (demo.perusistemas.pro)"


def configurado() -> bool:
    return config.MOCK or bool(config.PAGOOK_API_KEY)


def _base() -> str:
    base = config.PAGOOK_BASE_URL.rstrip("/")
    return base if base.endswith("/api/v1") else base + "/api/v1"


def _headers() -> dict:
    return {"X-API-Key": config.PAGOOK_API_KEY, "Content-Type": "application/json"}


async def _post(ruta: str, cuerpo: dict) -> tuple[int, dict]:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
            r = await cli.post(_base() + ruta, headers=_headers(), json=cuerpo)
    except Exception as e:
        log.warning("PagoOK %s: %s", ruta, e)
        return 0, {}
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {}


async def consultar(monto: str, fecha_hora_declarada: str, ventana_minutos: int,
                    nombre_pagador: str) -> tuple[int, dict]:
    _exigir_monto_demo(monto)
    # Sin `canal`: la billetera es informativa y no debe filtrar (un Plin al número
    # de Yape llega igual como pago al Yape).
    cuerpo = {"monto": monto, "fecha_hora_declarada": fecha_hora_declarada,
              "ventana_minutos": ventana_minutos, "nombre_pagador_declarado": nombre_pagador}
    if config.MOCK:
        return mocks.pagook_consultar(cuerpo)
    return await _post("/pagos/consultar", cuerpo)


async def pagos_recientes(desde_iso: str, limite: int = 50) -> tuple[int, dict]:
    """GET /pagos: lista los pagos recientes de la empresa (TODOS los montos).

    Solo lectura: no reclama ni marca nada, así que NO pasa por el candado de S/ 1.00.
    Sirve para avisar "recibimos S/ X tuyo, pero esta demo es de S/ 1.00".
    `desde_iso` sin zona = hora UTC (igual que recibido_en en PagoOK).
    """
    if config.MOCK:
        return 200, {"pagos": []}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
            r = await cli.get(_base() + "/pagos", headers=_headers(),
                              params={"desde": desde_iso, "limit": limite})
    except Exception as e:
        log.warning("PagoOK /pagos: %s", e)
        return 0, {}
    try:
        return r.status_code, r.json()
    except Exception:
        return r.status_code, {}


async def marcar_usado(pago: dict, referencia_externa: str) -> tuple[int, dict]:
    """`pago` es el que devolvió consultar(): se verifica su monto antes de marcarlo."""
    _exigir_monto_demo(pago.get("monto"))
    pago_id = int(pago["pago_id"])
    cuerpo = {"referencia_externa": referencia_externa, "sistema": SISTEMA}
    if config.MOCK:
        return mocks.pagook_marcar_usado(pago_id, cuerpo)
    return await _post(f"/pagos/{pago_id}/marcar-usado", cuerpo)
