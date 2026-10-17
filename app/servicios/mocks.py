"""Simuladores en memoria de PagoOK, Facturalo y RUC/DNI. Solo con DEMO_MOCK=1 (pruebas locales).

Imitan el contrato real lo suficiente para probar el flujo completo, varias demos a la vez
y el desempate por la hora del voucher. Nunca se activan en producción.
"""
import itertools
import time
import unicodedata
import uuid
from datetime import timedelta

from app import config

_PAGOS: list[dict] = []
_IDS = itertools.count(1000)
_COMPROBANTES: dict[str, dict] = {}
_CORRELATIVO = itertools.count(1)

PDF_MINIMO = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]"
              b"/Count 1>>endobj\n3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 100]>>endobj\n"
              b"trailer<</Root 1 0 R>>\n%%EOF\n")


# --- PagoOK ----------------------------------------------------------------
def registrar_pago(monto: str, nombre: str, hace_seg: int = 0) -> dict:
    """Simula que llega la notificación de un Yape."""
    p = {"pago_id": next(_IDS), "monto": f"{float(monto):.2f}", "contraparte": nombre,
         "fecha_hora": config.ahora() - timedelta(seconds=hace_seg), "usado_ref": None}
    _PAGOS.append(p)
    return {"pago_id": p["pago_id"], "fecha_hora": config.iso_lima(p["fecha_hora"])}


def _palabras(t: str) -> list[str]:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return [w for w in t.replace(".", " ").split() if w]


def _coincide(declarado: str, real: str):
    a, b = set(_palabras(declarado)), set(_palabras(real))
    if len(a) < 2 or len(b) < 2:
        return None
    return len(a & b) >= 2


def _pago_json(p, declarada, nombre):
    return {"pago_id": p["pago_id"], "monto": p["monto"], "moneda": "PEN", "canal": "yape",
            "institucion": "Yape", "contraparte": p["contraparte"], "codigo_operacion": None,
            "fecha_hora": config.iso_lima(p["fecha_hora"]),
            "diferencia_minutos": round((p["fecha_hora"] - declarada).total_seconds() / 60, 1),
            "coincide_nombre": _coincide(nombre, p["contraparte"]) if nombre else None}


FALLAS = {"status": 0, "veces": 0}     # fuerza respuestas de error de PagoOK en las pruebas


def pagook_consultar(c: dict):
    if FALLAS["veces"] > 0:
        FALLAS["veces"] -= 1
        return FALLAS["status"], {"detail": f"falla simulada {FALLAS['status']}"}
    declarada = config.desde_iso(c["fecha_hora_declarada"])
    v = timedelta(minutes=c.get("ventana_minutos", 15))
    nombre = c.get("nombre_pagador_declarado")
    en_ventana = [p for p in _PAGOS if p["monto"] == f"{float(c['monto']):.2f}"
                  and declarada - v <= p["fecha_hora"] <= declarada + v]
    libres = [_pago_json(p, declarada, nombre) for p in en_ventana if not p["usado_ref"]]
    usados = [_pago_json(p, declarada, nombre) | {"estado": "usado"} for p in en_ventana if p["usado_ref"]]
    base = {"candidatos": [], "pagos_ya_usados": usados, "pago": None, "criterios": {}}
    if not libres:
        return 200, base | {"nivel": "sin_coincidencia", "motivo": "ya_usado" if usados else "sin_pagos",
                            "accion_sugerida": "pedir_voucher", "mensaje": "Sin coincidencia."}
    if nombre:
        si = [p for p in libres if p["coincide_nombre"] is True]
        if len(si) == 1:
            return 200, base | {"nivel": "alta", "motivo": "monto_ventana_nombre",
                                "accion_sugerida": "aceptar", "pago": si[0], "mensaje": "Encontrado."}
        if len(libres) == 1 and libres[0]["coincide_nombre"] is False:
            return 200, base | {"nivel": "media", "motivo": "nombre_no_coincide", "candidatos": libres,
                                "accion_sugerida": "confirmar_manualmente", "mensaje": "Nombre distinto."}
    if len(libres) == 1:
        return 200, base | {"nivel": "alta", "motivo": "candidato_unico", "accion_sugerida": "aceptar",
                            "pago": libres[0], "mensaje": "Encontrado."}
    libres.sort(key=lambda p: abs(p["diferencia_minutos"]))
    return 200, base | {"nivel": "media", "motivo": "varios_candidatos", "candidatos": libres,
                        "accion_sugerida": "confirmar_manualmente", "mensaje": "Varios pagos."}


def pagook_marcar_usado(pago_id: int, c: dict):
    p = next((p for p in _PAGOS if p["pago_id"] == pago_id), None)
    if not p:
        return 404, {"detail": "No existe"}
    if p["usado_ref"] and p["usado_ref"] != c["referencia_externa"]:
        return 409, {"detail": f"Usado por {p['usado_ref']}"}
    ya = p["usado_ref"] is not None
    p["usado_ref"] = c["referencia_externa"]
    return 200, {"pago_id": pago_id, "usado": True, "ya_estaba_usado": ya,
                 "referencia_externa": c["referencia_externa"]}


# --- Facturalo -------------------------------------------------------------
def facturalo_emitir(c: dict):
    cid = str(uuid.uuid4())
    serie = c.get("serie") or ("F001" if c["tipo_comprobante"] == "01" else "B001")
    n = next(_CORRELATIVO)
    precio = c["items"][0]["precio_unitario"]
    comp = {"id": cid, "tipo": c["tipo_comprobante"], "serie": serie, "numero": n,
            "numero_formato": f"{serie}-{n:08d}", "total": round(round(precio, 2) + round(round(precio, 2) * 0.18, 2), 2),
            "estado": "encolado", "codigo_sunat": None, "mensaje_sunat": None,
            "referencia_externa": c.get("referencia_externa"), "observaciones": c.get("observaciones"),
            "_t": time.monotonic()}
    _COMPROBANTES[cid] = comp
    return 200, {"exito": True, "comprobante": {k: v for k, v in comp.items() if not k.startswith("_")}}


def facturalo_consultar(cid: str):
    comp = _COMPROBANTES.get(cid)
    if not comp:
        return 404, {"exito": False, "codigo": "NOT_FOUND"}
    if time.monotonic() - comp["_t"] > 2.0 and comp["estado"] == "encolado":
        comp.update(estado="aceptado", codigo_sunat="0",
                    mensaje_sunat=f"El comprobante {comp['numero_formato']} ha sido aceptado")
    return 200, {"exito": True, "comprobante": {k: v for k, v in comp.items() if not k.startswith("_")}}


def facturalo_buscar(ref: str):
    for comp in _COMPROBANTES.values():
        if comp["referencia_externa"] == ref:
            return 200, {"exito": True, "comprobante": {k: v for k, v in comp.items() if not k.startswith("_")}}
    return 404, {"exito": False, "codigo": "NOT_FOUND"}


def comprobante(cid: str) -> dict | None:
    return _COMPROBANTES.get(cid)


# --- RUC / DNI ---------------------------------------------------------------
def ruc(numero: str):
    if numero == "20615446565":
        return {"nombre": "PERU SISTEMAS PRO E.I.R.L.", "direccion": "LIMA - LIMA", "estado": "ACTIVO",
                "condicion": "HABIDO"}
    return {"nombre": f"EMPRESA DE PRUEBA {numero[-4:]} S.A.C.", "direccion": "AV. PRUEBA 123 - LIMA",
            "estado": "ACTIVO", "condicion": "HABIDO"}


def dni(numero: str):
    return {"nombre": f"PERSONA PRUEBA {numero[-3:]}"}
