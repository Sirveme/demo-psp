"""Rubros de la demo, definidos en rubros.json (se relee si el archivo cambia)."""
import json
import os
from pathlib import Path

ARCHIVO = Path(__file__).resolve().parent.parent / "rubros.json"
_cache = {"mtime": None, "lista": []}


def todos() -> list[dict]:
    mtime = os.path.getmtime(ARCHIVO)
    if _cache["mtime"] != mtime:
        with open(ARCHIVO, encoding="utf-8") as f:
            _cache["lista"] = json.load(f)["rubros"]
        _cache["mtime"] = mtime
    return _cache["lista"]


def rubro(slug: str) -> dict:
    """El rubro pedido o, si no existe, "otro"."""
    lista = todos()
    return next((r for r in lista if r["slug"] == slug),
                next((r for r in lista if r["slug"] == "otro"), lista[-1]))


def existe(slug: str) -> bool:
    return any(r["slug"] == slug for r in todos())
