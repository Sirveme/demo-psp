"""Aviso "Pago confirmado" por WhatsApp Cloud API (Meta). APAGADO hasta que exista la plantilla.

Se activa solo con las tres variables: WA_ACCESS_TOKEN, WA_PHONE_NUMBER_ID y WA_PLANTILLA_PAGO
(nombre de una plantilla de categoría UTILIDAD ya aprobada en Meta). Sin ellas, la demo no pide
el celular y no envía nada.

Plantilla esperada (3 variables, idioma WA_PLANTILLA_IDIOMA, por defecto "es"):
  "Pago confirmado: S/ {{1}} — {{2}}, {{3}}."
  ej. {{1}} = 1.00, {{2}} = Caja Farmacia, {{3}} = Clínica Demo
"""
import logging
import re

import httpx

from app import config

log = logging.getLogger("demo.whatsapp")
TIMEOUT = 8.0


def configurado() -> bool:
    return bool(config.WA_ACCESS_TOKEN and config.WA_PHONE_NUMBER_ID and config.WA_PLANTILLA_PAGO)


def celular_valido(texto: str) -> str | None:
    """'987 654 321' -> '51987654321'. None si no es un celular peruano."""
    n = re.sub(r"\D", "", texto or "")
    if len(n) == 11 and n.startswith("519"):
        n = n[2:]
    return "51" + n if re.fullmatch(r"9\d{8}", n) else None


async def enviar_pago_confirmado(celular_e164: str, monto: str, caja: str, negocio: str) -> bool:
    if not configurado():
        return False
    cuerpo = {
        "messaging_product": "whatsapp", "recipient_type": "individual", "to": celular_e164,
        "type": "template",
        "template": {"name": config.WA_PLANTILLA_PAGO, "language": {"code": config.WA_PLANTILLA_IDIOMA},
                     "components": [{"type": "body", "parameters": [
                         {"type": "text", "text": monto}, {"type": "text", "text": caja},
                         {"type": "text", "text": negocio}]}]},
    }
    url = f"https://graph.facebook.com/{config.GRAPH_API_VERSION}/{config.WA_PHONE_NUMBER_ID}/messages"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
            r = await cli.post(url, json=cuerpo, headers={"Authorization": f"Bearer {config.WA_ACCESS_TOKEN}"})
    except Exception as e:
        log.warning("WhatsApp: sin respuesta (%s)", type(e).__name__)
        return False
    if 200 <= r.status_code < 300:
        return True
    log.warning("WhatsApp: HTTP %s %s", r.status_code, r.text[:300])
    return False
