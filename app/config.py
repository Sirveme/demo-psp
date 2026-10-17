"""Configuración leída del entorno (Railway). Ningún secreto vive en el código ni en el repo."""
import os
from datetime import datetime, timedelta, timezone

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


def env(nombre: str, defecto: str = "") -> str:
    return (os.environ.get(nombre) or defecto).strip()


def env_int(nombre: str, defecto: int) -> int:
    try:
        return int(env(nombre) or defecto)
    except ValueError:
        return defecto


DATABASE_URL = env("DATABASE_URL", "sqlite:///./demo_local.db")
if DATABASE_URL.startswith("postgres://"):           # Railway a veces entrega el esquema antiguo
    DATABASE_URL = "postgresql://" + DATABASE_URL[len("postgres://"):]

FACTURALO_BASE_URL = env("FACTURALO_BASE_URL", "https://facturalo.pro")
FACTURALO_API_KEY = env("FACTURALO_API_KEY")
FACTURALO_API_SECRET = env("FACTURALO_API_SECRET")
# Series reales del emisor. Vacío = la API asigna la automática (B001 / F001).
FACTURALO_SERIE_BOLETA = env("FACTURALO_SERIE_BOLETA").upper()
FACTURALO_SERIE_FACTURA = env("FACTURALO_SERIE_FACTURA").upper()

PAGOOK_BASE_URL = env("PAGOOK_BASE_URL", "https://pagook.pro/api/v1")
PAGOOK_API_KEY = env("PAGOOK_API_KEY")
# La credencial se comparte con MITA (límite PagoOK: 120/min). La demo usa como máximo esto.
PAGOOK_MAX_POR_MIN = env_int("PAGOOK_MAX_POR_MIN", 60)

YAPE_NUMERO = env("YAPE_NUMERO", "938246208")
WHATSAPP_VENTAS = env("WHATSAPP_VENTAS", "51967317946")

APIS_NET_TOKEN = env("APIS_NET_TOKEN")
APIS_NET_BASE = env("APIS_NET_BASE")                 # opcional; se deduce del token si falta
RUC_API_URL = env("RUC_API_URL")                     # opcional; API-RUC propia si algún día existe

SALA_CLAVE = env("SALA_CLAVE")
PUBLIC_URL = env("PUBLIC_URL").rstrip("/")           # opcional, p. ej. https://demo.perusistemas.pro
MONTO_MAXIMO = env_int("MONTO_MAXIMO", 100)
MOCK = env("DEMO_MOCK") == "1"                       # solo local: PagoOK, Facturalo y RUC/DNI simulados

LIMA = timezone(timedelta(hours=-5))                 # Perú no tiene horario de verano
VENTANA_PAGO_SEG = 10 * 60


def ahora() -> datetime:
    """Hora del servidor en UTC sin zona (así se guarda en la base)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def a_lima(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone(LIMA)


def iso_lima(dt: datetime) -> str:
    return a_lima(dt).isoformat(timespec="seconds")


def desde_iso(texto: str | None) -> datetime | None:
    """ISO con zona -> UTC sin zona. Sin zona se interpreta como hora de Lima."""
    if not texto:
        return None
    try:
        dt = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=LIMA)
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def fecha_hora_lima(dt: datetime | None) -> str:
    return a_lima(dt).strftime("%d/%m/%Y %H:%M:%S") if dt else ""


def hora_lima(dt: datetime | None) -> str:
    return a_lima(dt).strftime("%H:%M:%S") if dt else ""
