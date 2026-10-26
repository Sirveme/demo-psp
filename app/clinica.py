"""Demo de clínica con dos cajas (/clinica): reservas, regla de asignación del pago y monitores.

Todos pagan S/ 1.00. Al tocar "Voy a pagar", la caja queda reservada 3 minutos y el primer pago
de S/ 1.00 que llegue dentro de esa ventana es de esa caja. Una caja reservada no se puede usar
hasta que se libere (pago confirmado o fin de la ventana).
"""
import json
import os
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import or_, update

from app import config
from app.db import Caja, Demo, Sesion

NEGOCIO = "Clínica Demo"
MONTO = Decimal("1.00")
RESERVA_SEG = 3 * 60
# Tras el fin de la reserva se sigue mirando unos segundos: el aviso del banco puede llegar a
# PagoOK con un pequeño retraso. Solo cuentan pagos con hora DENTRO de la reserva.
GRACIA_SEG = 15
# Diferencia de reloj tolerada entre este servidor y PagoOK al inicio de la reserva.
TOLERANCIA_INICIO_SEG = 5

CAJAS = {
    "principal": {"nombre": "Caja Principal", "concepto": "Atención de prueba – Caja Principal, Clínica Demo"},
    "farmacia": {"nombre": "Caja Farmacia", "concepto": "Venta de prueba – Caja Farmacia, Clínica Demo"},
}
OCUPADA = "Caja ocupada, intenta en un momento."


class CajaOcupada(Exception):
    pass


def reservar(demo: Demo) -> None:
    """Guarda la demo y reserva su caja en una sola transacción. CajaOcupada si no está libre."""
    ahora = config.ahora()
    demo.reserva_hasta = ahora + timedelta(seconds=RESERVA_SEG)
    with Sesion() as s:
        s.add(demo)
        s.flush()
        r = s.execute(
            update(Caja)
            .where(Caja.slug == demo.caja, or_(Caja.hasta.is_(None), Caja.hasta < ahora))
            .values(demo_id=demo.id, hasta=demo.reserva_hasta + timedelta(seconds=GRACIA_SEG))
        )
        if r.rowcount != 1:
            s.rollback()
            raise CajaOcupada()
        s.commit()


def liberar(demo_id: str) -> None:
    with Sesion() as s:
        s.execute(update(Caja).where(Caja.demo_id == demo_id).values(demo_id=None, hasta=None))
        s.commit()


def elegir_pago(candidatos: list[dict], inicio: datetime, fin: datetime) -> dict | None:
    """El primer pago de S/ 1.00 con hora dentro de [inicio, fin] que no esté usado.

    Si PagoOK dice que el nombre del pagador NO coincide con el titular declarado, el pago no es de
    esta caja: así dos cajas que esperan a la vez no se cruzan los pagos.
    """
    desde = inicio - timedelta(seconds=TOLERANCIA_INICIO_SEG)
    buenos = []
    for p in candidatos or []:
        cuando = config.desde_iso(p.get("fecha_hora"))
        if (str(p.get("monto")) == f"{MONTO:.2f}" and cuando is not None and desde <= cuando <= fin
                and p.get("coincide_nombre") is not False
                and p.get("estado") not in ("usado", "reclamado_por_otro_sistema")):
            buenos.append((cuando, p))
    buenos.sort(key=lambda x: x[0])
    return buenos[0][1] if buenos else None


# --- Monitores ---------------------------------------------------------------------
def _inicio_del_dia() -> datetime:
    hoy = config.a_lima(config.ahora()).replace(hour=0, minute=0, second=0, microsecond=0)
    return config.desde_iso(hoy.isoformat())


def medio(canal: str | None) -> str:
    return {"yape": "Yape", "plin": "Plin"}.get(canal or "", "Billetera digital")


def _pago_json(d: Demo) -> dict:
    segundos = max(1, round((d.pago_detectado_en - d.pago_en).total_seconds())) if d.pago_detectado_en else None
    return {"id": d.id, "caja": d.caja, "caja_nombre": CAJAS[d.caja]["nombre"], "monto": f"{d.monto:.2f}",
            "hora": config.hora_lima(d.pago_en),
            "medio": medio(d.pago_canal),
            "segundos": segundos}


def estado() -> dict:
    """Lo que ven los tres monitores: dos cajas y contabilidad (todas las sedes)."""
    ahora = config.ahora()
    with Sesion() as s:
        cajas = {c.slug: c for c in s.query(Caja).all()}
        pagos = (s.query(Demo).filter(Demo.caja.isnot(None), Demo.pago_en.isnot(None),
                                      Demo.pago_en >= _inicio_del_dia())
                 .order_by(Demo.pago_en.desc()).limit(200).all())
        reservas = {d.id: d for d in s.query(Demo).filter(
            Demo.id.in_([c.demo_id for c in cajas.values() if c.demo_id])).all()}
    lista = [_pago_json(d) for d in pagos]
    salida = []
    for slug, info in CAJAS.items():
        c = cajas.get(slug)
        reservada = bool(c and c.demo_id and c.hasta and c.hasta >= ahora)
        d = reservas.get(c.demo_id) if reservada else None
        salida.append({
            "slug": slug, "nombre": info["nombre"], "reservada": reservada,
            "restante_seg": max(0, int((d.reserva_hasta - ahora).total_seconds())) if d else 0,
            "pagos": [p for p in lista if p["caja"] == slug][:8],
        })
    return {"negocio": NEGOCIO, "cajas": salida,
            "contabilidad": {"pagos": lista[:12], "cantidad": len(lista),
                             "total": f"{sum((Decimal(p['monto']) for p in lista), Decimal('0')):.2f}"}}


# --- "Clínicas que confían en PagoOK" ------------------------------------------------
ARCHIVO_CLINICAS = Path(__file__).resolve().parent.parent / "clinicas.json"
_cache = {"mtime": None, "lista": []}


def clinicas_autorizadas() -> list[dict]:
    """Solo las que tienen nombre y registro de autorización. Sin ninguna, la sección no se muestra."""
    try:
        mtime = os.path.getmtime(ARCHIVO_CLINICAS)
    except OSError:
        return []
    if _cache["mtime"] != mtime:
        try:
            with open(ARCHIVO_CLINICAS, encoding="utf-8") as f:
                datos = json.load(f).get("clinicas") or []
        except (OSError, ValueError):
            datos = []
        _cache["lista"] = [c for c in datos if isinstance(c, dict) and all(
            str(c.get(k) or "").strip() for k in ("nombre", "autorizado_por", "autorizado_el"))]
        _cache["mtime"] = mtime
    return _cache["lista"]
