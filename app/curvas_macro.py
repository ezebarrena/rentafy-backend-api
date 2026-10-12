"""Cuentas de las pestañas "Dólar de equilibrio", "Sensibilidad al riesgo país" y "Riesgo país" de Rendimientos. Información histórica o de
equilibrio, no recomendaciones. Funciones puras (sin base de datos) salvo la descarga del riesgo país, para poder probarlas con datos armados."""

import logging
import math
import statistics
import threading
import time
from datetime import date, timedelta

import requests

from .financial_utils import ARGENTINADATOS_DOLARES_URL, ARGENTINADATOS_RIESGO_PAIS_URL

logger = logging.getLogger("rentafy.curvas_macro")

# --- Riesgo país (serie diaria de ArgentinaDatos) ---------------------------------------------------------------------------------------
_CACHE_SEG = 6 * 3600
_cache: dict = {"hasta": 0.0, "serie": []}
_candado = threading.Lock()


def serie_riesgo_pais() -> list[tuple[date, float]]:
    """Serie completa (desde 1999) de riesgo país en puntos básicos, ordenada por fecha. Se baja una vez cada 6 horas; si la fuente falla
    devuelve lo último que se tuvo (o una lista vacía): es contexto, no debe romper la pantalla."""
    ahora = time.monotonic()
    with _candado:
        if ahora < _cache["hasta"] and _cache["serie"]:
            return _cache["serie"]
        try:
            respuesta = requests.get(ARGENTINADATOS_RIESGO_PAIS_URL, timeout=15)
            respuesta.raise_for_status()
            serie = []
            for fila in respuesta.json():
                try:
                    serie.append((date.fromisoformat(fila["fecha"]), float(fila["valor"])))
                except (KeyError, TypeError, ValueError):
                    continue
            serie.sort()
            if serie:
                _cache["serie"], _cache["hasta"] = serie, ahora + _CACHE_SEG
        except Exception as exc:  # noqa: BLE001 — fuente externa
            logger.warning("No se pudo bajar la serie de riesgo país: %s", exc)
            _cache["hasta"] = ahora + 300
        return _cache["serie"]


# --- Dólar MEP y CCL (series diarias de ArgentinaDatos, cotización de venta) -------------------------------------------------------------
_CASAS = {"mep": "bolsa", "ccl": "contadoconliqui"}
_cache_dolar: dict[str, dict] = {k: {"hasta": 0.0, "serie": []} for k in _CASAS}


def serie_dolar(clave: str) -> list[tuple[date, float]]:
    """Serie diaria (venta) del dólar MEP (`mep`, casa «bolsa») o CCL (`ccl`, casa «contadoconliqui»), ordenada por fecha. Caché de 3 horas;
    si la fuente falla devuelve lo último que se tuvo (o una lista vacía)."""
    ahora = time.monotonic()
    memoria = _cache_dolar[clave]
    with _candado:
        if ahora < memoria["hasta"] and memoria["serie"]:
            return memoria["serie"]
        try:
            respuesta = requests.get(f"{ARGENTINADATOS_DOLARES_URL}/{_CASAS[clave]}", timeout=20)
            respuesta.raise_for_status()
            serie = []
            for fila in respuesta.json():
                try:
                    valor = float(fila["venta"])
                    if valor > 0:
                        serie.append((date.fromisoformat(fila["fecha"]), valor))
                except (KeyError, TypeError, ValueError):
                    continue
            serie.sort()
            if serie:
                memoria["serie"], memoria["hasta"] = serie, ahora + 3 * 3600
        except Exception as exc:  # noqa: BLE001 — fuente externa
            logger.warning("No se pudo bajar la serie del dólar %s: %s", clave, exc)
            memoria["hasta"] = ahora + 300
        return memoria["serie"]


def unir_dolares(mep: list[tuple[date, float]], ccl: list[tuple[date, float]], desde: date) -> list[dict]:
    """Una fila por fecha desde `desde`, con el MEP y el CCL de ese día (None si alguno no cotizó)."""
    por_fecha: dict[date, dict] = {}
    for f, v in mep:
        if f >= desde:
            por_fecha.setdefault(f, {"fecha": f, "mep": None, "ccl": None})["mep"] = v
    for f, v in ccl:
        if f >= desde:
            por_fecha.setdefault(f, {"fecha": f, "mep": None, "ccl": None})["ccl"] = v
    return [por_fecha[f] for f in sorted(por_fecha)]


# --- Inflación (IPC mensual e interanual, ArgentinaDatos) ---------------------------------------------------------------------------------
_URL_INFLACION = "https://api.argentinadatos.com/v1/finanzas/indices"
_cache_inflacion: dict[str, dict] = {k: {"hasta": 0.0, "serie": []} for k in ("inflacion", "inflacionInteranual")}


def serie_inflacion(clave: str) -> list[tuple[date, float]]:
    """IPC mensual (`inflacion`) o variación interanual (`inflacionInteranual`), en %, por mes (fecha = último día del mes). Caché de 12 horas;
    si la fuente falla devuelve lo último que se tuvo (o una lista vacía)."""
    ahora = time.monotonic()
    memoria = _cache_inflacion[clave]
    with _candado:
        if ahora < memoria["hasta"] and memoria["serie"]:
            return memoria["serie"]
        try:
            respuesta = requests.get(f"{_URL_INFLACION}/{clave}", timeout=20)
            respuesta.raise_for_status()
            serie = []
            for fila in respuesta.json():
                try:
                    serie.append((date.fromisoformat(fila["fecha"]), float(fila["valor"])))
                except (KeyError, TypeError, ValueError):
                    continue
            serie.sort()
            if serie:
                memoria["serie"], memoria["hasta"] = serie, ahora + 12 * 3600
        except Exception as exc:  # noqa: BLE001 — fuente externa
            logger.warning("No se pudo bajar la serie «%s»: %s", clave, exc)
            memoria["hasta"] = ahora + 300
        return memoria["serie"]


def unir_inflacion(mensual: list[tuple[date, float]], interanual: list[tuple[date, float]], desde: date) -> list[dict]:
    """Una fila por mes desde `desde`, con el IPC del mes y la variación interanual (None si falta alguno)."""
    por_mes: dict[date, dict] = {}
    for f, v in mensual:
        if f >= desde:
            por_mes.setdefault(f, {"fecha": f, "mensual": None, "interanual": None})["mensual"] = v
    for f, v in interanual:
        if f >= desde:
            por_mes.setdefault(f, {"fecha": f, "mensual": None, "interanual": None})["interanual"] = v
    return [por_mes[f] for f in sorted(por_mes)]


def inflacion_de_equilibrio(tir_real: float, tir_nominal: float) -> tuple[float, float]:
    """Inflación (anual %, mensual %) que iguala a un bono CER (tasa real `tir_real`, %) con una tasa fija en pesos del mismo plazo
    (`tir_nominal`, % anual efectivo): (1 + nominal) / (1 + real) − 1, y su equivalente mensual compuesto."""
    anual = ((1 + tir_nominal / 100) / (1 + tir_real / 100) - 1) * 100
    mensual = ((1 + anual / 100) ** (1 / 12) - 1) * 100 if anual > -100 else float("nan")
    return anual, mensual


# --- Dólar de equilibrio ----------------------------------------------------------------------------------------------------------------
def suba_del_dolar_para_igualar(tir_anual: float, plazo_dias: int) -> float:
    """Suba (%) que tiene que tener el dólar hasta el vencimiento para que comprar dólares rinda lo mismo que el bono en pesos, dada su TIR
    anual efectiva (%) y su plazo."""
    return ((1 + tir_anual / 100) ** (plazo_dias / 365) - 1) * 100


# --- Sensibilidad al riesgo país --------------------------------------------------------------------------------------------------------
VENTANA_SENSIBILIDAD_DIAS = 730
MINIMO_OBSERVACIONES = 120
# Un cupón o una amortización hace caer el precio de golpe sin que haya pasado nada en el mercado: se descartan los movimientos que se
# alejan más de esto (en desvíos robustos) de lo típico, y cualquier variación diaria de más de 15 %.
CORTE_ATIPICOS = 6.0
VARIACION_MAXIMA = 0.15


def sensibilidad_al_riesgo_pais(
    precios: list[tuple[date, float]], riesgo_pais: dict[date, float]
) -> dict | None:
    """Regresión de la variación diaria (logarítmica) del precio contra el cambio diario del riesgo país, sobre las fechas que tienen las
    dos series. Devuelve la caída aproximada del precio (%) por cada +100 pb de riesgo país (`porCada100pb`, negativa cuando el precio baja),
    el R² y la cantidad de observaciones; None si hay menos de MINIMO_OBSERVACIONES."""
    comunes = [(f, p, riesgo_pais[f]) for f, p in sorted(precios) if f in riesgo_pais and p > 0]
    pares = []
    for (_, p0, r0), (_, p1, r1) in zip(comunes, comunes[1:]):
        y = math.log(p1 / p0)
        if abs(y) <= VARIACION_MAXIMA:
            pares.append(((r1 - r0) / 100.0, y))
    if len(pares) < MINIMO_OBSERVACIONES:
        return None
    ys = [y for _, y in pares]
    mediana = statistics.median(ys)
    mad = statistics.median(abs(y - mediana) for y in ys) * 1.4826
    if mad > 0:
        pares = [(x, y) for x, y in pares if abs(y - mediana) <= CORTE_ATIPICOS * mad]
    n = len(pares)
    if n < MINIMO_OBSERVACIONES:
        return None
    xs, ys = [x for x, _ in pares], [y for _, y in pares]
    xm, ym = sum(xs) / n, sum(ys) / n
    sxx = sum((x - xm) ** 2 for x in xs)
    syy = sum((y - ym) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
    beta = sxy / sxx
    return {"porCada100pb": (math.exp(beta) - 1) * 100, "r2": sxy**2 / (sxx * syy), "observaciones": n}


def desde_ventana(hoy: date) -> date:
    return hoy - timedelta(days=VENTANA_SENSIBILIDAD_DIAS)
