"""Rate limit por IP y presupuesto global de consultas a PagoOK.

Son contadores de protección del proceso, no estado de las demostraciones.
"""
import asyncio
import time
from collections import defaultdict, deque

from app import config

# Límites por IP: (máximo, segundos). Son amplios porque en un evento muchas
# personas salen por el mismo WiFi (misma IP pública).
LIMITES = {
    "demo": (40, 600),
    "consulta": (120, 600),
    "interesado": (20, 600),
    "voucher": (60, 600),
}

_marcas: dict[tuple[str, str], deque] = defaultdict(deque)


def permitido(tipo: str, ip: str) -> bool:
    maximo, ventana = LIMITES[tipo]
    q = _marcas[(tipo, ip or "?")]
    t = time.monotonic()
    while q and t - q[0] > ventana:
        q.popleft()
    if len(q) >= maximo:
        return False
    q.append(t)
    return True


class Presupuesto:
    """Ventana deslizante de 60 s: como mucho N consultas por minuto entre todas las demos.

    La credencial de PagoOK es la de MITA y el límite (120/min) es por credencial.
    """

    def __init__(self, por_minuto: int):
        self.por_minuto = max(1, por_minuto)
        self._marcas: deque = deque()
        self._lock = asyncio.Lock()

    async def tomar(self):
        while True:
            async with self._lock:
                t = time.monotonic()
                while self._marcas and t - self._marcas[0] > 60:
                    self._marcas.popleft()
                if len(self._marcas) < self.por_minuto:
                    self._marcas.append(t)
                    return
                espera = 60 - (t - self._marcas[0]) + 0.05
            await asyncio.sleep(min(espera, 2))

    def usadas(self) -> int:
        t = time.monotonic()
        return sum(1 for m in self._marcas if t - m <= 60)


pagook = Presupuesto(config.PAGOOK_MAX_POR_MIN)
