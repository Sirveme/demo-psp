"""Consulta de RUC y DNI con cadena de respaldo y caché de 24 h.

RUC: RUC_API_URL (API-RUC propia, si está configurada) -> apis.net.pe/decolecta -> manual.
DNI: apis.net.pe/decolecta (RENIEC) -> manual.
Timeout de 4 s por proveedor. La clave nunca sale al navegador ni a los logs.
"""
import json
import logging
import re
from datetime import timedelta

import httpx

from app import config
from app.db import CacheConsulta, Sesion
from app.servicios import mocks

log = logging.getLogger("demo.identidad")
TIMEOUT = 4.0
CACHE = timedelta(hours=24)


def ruc_valido(ruc: str) -> bool:
    if not re.fullmatch(r"(10|15|17|20)\d{9}", ruc or ""):
        return False
    pesos = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
    resto = 11 - sum(int(d) * p for d, p in zip(ruc[:10], pesos)) % 11
    return int(ruc[10]) == {10: 0, 11: 1}.get(resto, resto)


def dni_valido(dni: str) -> bool:
    return bool(re.fullmatch(r"\d{8}", dni or ""))


def _apis_base_y_rutas() -> tuple[str, str, str]:
    """(base, ruta_dni, ruta_ruc). Token 'sk_...' = decolecta v1; si no, apis.net.pe v2."""
    base = config.APIS_NET_BASE
    if not base:
        base = ("https://api.decolecta.com" if config.APIS_NET_TOKEN.startswith("sk_")
                else "https://api.apis.net.pe")
    base = base.rstrip("/")
    if "apis.net" in base:
        return base, "/v2/reniec/dni", "/v2/sunat/ruc"
    return base, "/v1/reniec/dni", "/v1/sunat/ruc"


async def _get_json(url: str, params=None, headers=None) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as cli:
            r = await cli.get(url, params=params, headers=headers)
        if r.status_code != 200:
            log.info("consulta %s -> HTTP %s", url.split("?")[0], r.status_code)
            return None
        return r.json()
    except Exception as e:
        log.info("consulta %s -> %s", url.split("?")[0], type(e).__name__)
        return None


async def _apis(ruta_idx: int, numero: str) -> dict | None:
    if not config.APIS_NET_TOKEN:
        return None
    base, ruta_dni, ruta_ruc = _apis_base_y_rutas()
    ruta = ruta_dni if ruta_idx == 0 else ruta_ruc
    return await _get_json(base + ruta, params={"numero": numero},
                           headers={"Authorization": f"Bearer {config.APIS_NET_TOKEN}",
                                    "Accept": "application/json",
                                    "Referer": "https://demo.perusistemas.pro"})


def _normaliza_ruc(d: dict | None) -> dict | None:
    if not d:
        return None
    d = d.get("data") if isinstance(d.get("data"), dict) else d
    razon = d.get("razon_social") or d.get("razonSocial") or d.get("nombre_o_razon_social") or d.get("nombre")
    if not razon:
        return None
    direccion = d.get("direccion") or d.get("domicilio_fiscal") or d.get("direccion_completa") or ""
    partes = [direccion] + [d.get(k) or "" for k in ("distrito", "provincia", "departamento")]
    direccion = " ".join(p.strip() for p in partes if p and p.strip() and p.strip() != "-")
    return {"nombre": razon.strip(), "direccion": direccion.strip(),
            "estado": (d.get("estado") or "").strip(), "condicion": (d.get("condicion") or "").strip()}


def _normaliza_dni(d: dict | None) -> dict | None:
    if not d:
        return None
    nombres = d.get("nombres") or d.get("first_name") or ""
    pat = d.get("apellidoPaterno") or d.get("first_last_name") or ""
    mat = d.get("apellidoMaterno") or d.get("second_last_name") or ""
    completo = " ".join(x.strip() for x in (nombres, pat, mat) if x and x.strip())
    if not completo:
        completo = (d.get("full_name") or d.get("nombreCompleto") or d.get("nombre") or "").strip()
    return {"nombre": completo} if completo else None


def _cache_leer(clave: str) -> dict | None:
    with Sesion() as s:
        c = s.get(CacheConsulta, clave)
        if c and config.ahora() - c.creado_en < CACHE:
            return json.loads(c.datos) | {"fuente": c.fuente}
    return None


def _cache_guardar(clave: str, datos: dict, fuente: str):
    with Sesion() as s:
        c = s.get(CacheConsulta, clave) or CacheConsulta(clave=clave)
        c.datos = json.dumps(datos, ensure_ascii=False)
        c.fuente = fuente
        c.creado_en = config.ahora()
        s.merge(c)
        s.commit()


async def consultar_ruc(ruc: str) -> dict | None:
    clave = f"ruc:{ruc}"
    if (c := _cache_leer(clave)):
        return c
    if config.MOCK:
        datos, fuente = mocks.ruc(ruc), "mock"
    else:
        datos, fuente = None, ""
        if config.RUC_API_URL:
            url = (config.RUC_API_URL.replace("{ruc}", ruc) if "{ruc}" in config.RUC_API_URL
                   else f"{config.RUC_API_URL.rstrip('/')}/{ruc}")
            datos, fuente = _normaliza_ruc(await _get_json(url)), "api_ruc_propia"
        if not datos:
            datos, fuente = _normaliza_ruc(await _apis(1, ruc)), "apis_net"
    if datos:
        _cache_guardar(clave, datos, fuente)
        return datos | {"fuente": fuente}
    return None


async def consultar_dni(dni: str) -> dict | None:
    clave = f"dni:{dni}"
    if (c := _cache_leer(clave)):
        return c
    if config.MOCK:
        datos, fuente = mocks.dni(dni), "mock"
    else:
        datos, fuente = _normaliza_dni(await _apis(0, dni)), "apis_net"
    if datos:
        _cache_guardar(clave, datos, fuente)
        return datos | {"fuente": fuente}
    return None
