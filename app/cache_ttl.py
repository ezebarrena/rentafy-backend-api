"""Memoria con vencimiento (caché en memoria de un proceso), con su candado: reemplaza los `dict` + `Lock` + `time.time()` escritos a mano en cada módulo.

Es la misma clase en rentafy-backend-api y en rentafy-servicioIA (son servicios independientes, cada uno con su copia; si se cambia una, se cambia la otra)."""

import threading
import time
from collections.abc import Callable, Hashable
from typing import Any


class CacheTTL:
    """Guarda valores por clave durante `segundos`; pasado ese tiempo `obtener` devuelve None. Un valor None no se guarda (se toma como "no hay")."""

    def __init__(self, segundos: float):
        self._segundos = segundos
        self._guardado: dict[Hashable, tuple[float, Any]] = {}
        # Público a propósito: quien necesite que el cálculo y el guardado ocurran juntos (ver obtener_o_calcular) o serializar otro cálculo con el mismo candado lo puede tomar.
        self.lock = threading.Lock()

    def obtener(self, clave: Hashable = None) -> Any | None:
        """El valor vigente de `clave`, o None si no hay o ya venció."""
        with self.lock:
            return self._vigente(clave)

    def guardar(self, valor: Any, clave: Hashable = None) -> None:
        with self.lock:
            self._guardado[clave] = (time.monotonic(), valor)

    def invalidar(self, clave: Hashable = None) -> None:
        """Descarta el valor de `clave` (el próximo `obtener` devuelve None)."""
        with self.lock:
            self._guardado.pop(clave, None)

    def obtener_o_calcular(self, calcular: Callable[[], Any], clave: Hashable = None) -> Any:
        """El valor vigente o, si no hay, lo calcula y lo guarda. El cálculo corre con el candado tomado: si dos pedidos llegan juntos, el segundo espera y reusa el resultado en lugar de calcular otra vez."""
        with self.lock:
            valor = self._vigente(clave)
            if valor is None:
                valor = calcular()
                self._guardado[clave] = (time.monotonic(), valor)
            return valor

    def _vigente(self, clave: Hashable) -> Any | None:
        guardado = self._guardado.get(clave)
        if guardado is not None and time.monotonic() - guardado[0] < self._segundos:
            return guardado[1]
        return None
