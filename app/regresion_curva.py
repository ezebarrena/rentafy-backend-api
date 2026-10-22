"""Regresión logarítmica TIR = a + b·ln(duration) por mínimos cuadrados: UNA sola implementación para todas las curvas del backend (curvas por grupo, inflación de equilibrio y evolución de la curva).

Sin numpy (no es una dependencia del backend): son un puñado de sumas."""

import math
from typing import Optional

Ajuste = tuple[float, float, float]  # (a, b, r²)


def ajuste_logaritmico(puntos: list[tuple[float, float]]) -> Optional[Ajuste]:
    """(a, b, r²) de `y = a + b·ln(x)` para puntos (x, y) con x > 0, sin redondear. None si no hay puntos o no varían (todos los x o todos los y iguales)."""
    if not puntos:
        return None
    xs = [math.log(x) for x, _ in puntos]
    ys = [y for _, y in puntos]
    n = len(xs)
    xbar, ybar = sum(xs) / n, sum(ys) / n
    sxx = sum((x - xbar) ** 2 for x in xs)
    syy = sum((y - ybar) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys))
    b = sxy / sxx
    a = ybar - b * xbar
    r2 = (sxy**2) / (sxx * syy)
    return a, b, r2


def ajustar_curva(puntos: list[tuple[float, float]]) -> Optional[Ajuste]:
    """Lo mismo, redondeado a 4 decimales: es lo que devuelven las curvas por grupo y la inflación de equilibrio."""
    ajuste = ajuste_logaritmico(puntos)
    if ajuste is None:
        return None
    a, b, r2 = ajuste
    return round(a, 4), round(b, 4), round(r2, 4)
