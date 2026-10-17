"""Cliente de PagoOK, contrato docs/consulta-pagos-v1.md.

  consultar()     -> POST /pagos/consultar
  marcar_usado()  -> POST /pagos/{pago_id}/marcar-usado

Devuelven (status, body). status 0 = error de red o timeout.
"""
import logging

import httpx

from app import config
from app.servicios import mocks

log = logging.getLogger("demo.pagook")
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
    # Sin `canal`: la billetera es informativa y no debe filtrar (un Plin al número
    # de Yape llega igual como pago al Yape).
    cuerpo = {"monto": monto, "fecha_hora_declarada": fecha_hora_declarada,
              "ventana_minutos": ventana_minutos, "nombre_pagador_declarado": nombre_pagador}
    if config.MOCK:
        return mocks.pagook_consultar(cuerpo)
    return await _post("/pagos/consultar", cuerpo)


async def marcar_usado(pago_id: int, referencia_externa: str) -> tuple[int, dict]:
    cuerpo = {"referencia_externa": referencia_externa, "sistema": SISTEMA}
    if config.MOCK:
        return mocks.pagook_marcar_usado(pago_id, cuerpo)
    return await _post(f"/pagos/{pago_id}/marcar-usado", cuerpo)
