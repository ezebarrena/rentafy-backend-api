"""Indicadores financieros auxiliares que antes se resolvían en vivo en cada request y ahora
se cachean, actualizados una vez por día por el job de las 18:05 (ver scheduler.py):

- REM (Relevamiento de Expectativas de Mercado, BCRA): inflación esperada a 12 meses, usada
  para estimar la TIR "nominal" de los BONCER (ver serializers.py:_tir_nominal_estimada).
- Riesgo País y Dólar CCL/MEP (ArgentinaDatos): las tarjetas de indicadores del Dashboard
  (GET /mercado/indicadores) — antes le pegaban a la fuente externa en cada carga de la
  página; ahora ese endpoint solo lee la cache que este módulo mantiene al día.

Todo con fallo silencioso (solo logueando): si una fuente externa no responde, se conserva
el último valor cacheado en vez de tumbar el resto del job.
"""

import logging
from datetime import date, datetime

import requests
from sqlalchemy.orm import Session

from .models_financiera import IndicadorMacro, IndicadorMercadoCache

logger = logging.getLogger("rentafy.financial_utils")

REM_INFLACION_12M_URL = "https://api.bcra.gob.ar/estadisticas/v4.0/Monetarias/29"
NOMBRE_REM_INFLACION = "rem_inflacion_12m"

ARGENTINADATOS_DOLARES_URL = "https://api.argentinadatos.com/v1/cotizaciones/dolares"
ARGENTINADATOS_RIESGO_PAIS_URL = "https://api.argentinadatos.com/v1/finanzas/indices/riesgo-pais"


def actualizar_rem_inflacion(db: Session) -> float | None:
    """Ver docstring del módulo. Se usa para la TIR nominal estimada de los BONCER."""
    try:
        response = requests.get(REM_INFLACION_12M_URL, timeout=10)
        response.raise_for_status()
        # "detalle" viene ordenado descendente por fecha (el más reciente primero).
        resultados = response.json()["results"][0]["detalle"]
        valor = float(resultados[0]["valor"])
    except Exception as exc:  # noqa: BLE001 — indicador secundario, no debe tumbar el job
        logger.warning("No se pudo actualizar el REM de inflación esperada: %s", exc)
        return None

    indicador = db.query(IndicadorMacro).filter(IndicadorMacro.nombre == NOMBRE_REM_INFLACION).first()
    if indicador is None:
        indicador = IndicadorMacro(nombre=NOMBRE_REM_INFLACION)
        db.add(indicador)
    indicador.valor = valor
    indicador.fecha = date.today()
    db.commit()
    logger.info("REM inflación esperada 12m actualizado: %s%%", valor)
    return valor


def obtener_rem_inflacion(db: Session) -> float | None:
    indicador = db.query(IndicadorMacro).filter(IndicadorMacro.nombre == NOMBRE_REM_INFLACION).first()
    return indicador.valor if indicador is not None else None


def _guardar_indicador_mercado(db: Session, label: str, valor: str, variacion: str, tendencia: str, detalle: str | None) -> None:
    cache = db.query(IndicadorMercadoCache).filter(IndicadorMercadoCache.label == label).first()
    if cache is None:
        cache = IndicadorMercadoCache(label=label)
        db.add(cache)
    cache.valor = valor
    cache.variacion = variacion
    cache.tendencia = tendencia
    cache.detalle = detalle
    cache.fecha = date.today()
    db.commit()


def actualizar_riesgo_pais(db: Session) -> None:
    try:
        response = requests.get(ARGENTINADATOS_RIESGO_PAIS_URL, timeout=10)
        response.raise_for_status()
        filas = response.json()
        ultimo, anterior = filas[-1], (filas[-2] if len(filas) > 1 else None)
        fecha = datetime.strptime(ultimo["fecha"], "%Y-%m-%d")
    except Exception as exc:  # noqa: BLE001 — indicador secundario, no debe tumbar el job
        logger.warning("No se pudo actualizar Riesgo País: %s", exc)
        return

    if anterior is not None:
        delta = ultimo["valor"] - anterior["valor"]
        tendencia = "positiva" if delta < 0 else ("negativa" if delta > 0 else "neutral")  # bajar es buena noticia
        signo = "+" if delta > 0 else ""
        variacion = f"{signo}{delta:.0f} pts"
    else:
        tendencia = "neutral"
        variacion = "—"

    _guardar_indicador_mercado(
        db, "Riesgo País", str(round(ultimo["valor"])), variacion, tendencia, fecha.strftime("%d/%m")
    )
    logger.info("Riesgo País actualizado: %s pts", round(ultimo["valor"]))


def actualizar_dolar_ccl_mep(db: Session) -> None:
    try:
        response = requests.get(ARGENTINADATOS_DOLARES_URL, timeout=10)
        response.raise_for_status()
        filas = response.json()
    except Exception as exc:  # noqa: BLE001 — indicador secundario, no debe tumbar el job
        logger.warning("No se pudo actualizar Dólar CCL/MEP: %s", exc)
        return

    for label, casa in (("Dólar CCL", "contadoconliqui"), ("Dólar MEP", "bolsa")):
        filtradas = [f for f in filas if f["casa"] == casa]
        if not filtradas:
            continue
        ultimo, anterior = filtradas[-1], (filtradas[-2] if len(filtradas) > 1 else None)
        variacion_pct = ((ultimo["venta"] - anterior["venta"]) / anterior["venta"] * 100) if anterior else 0.0
        fecha = datetime.strptime(ultimo["fecha"], "%Y-%m-%d")

        valor_fmt = f"$ {ultimo['venta']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        signo = "+" if variacion_pct >= 0 else ""
        _guardar_indicador_mercado(
            db,
            label,
            valor_fmt,
            f"{signo}{variacion_pct:.2f}%",
            "positiva" if variacion_pct >= 0 else "negativa",
            fecha.strftime("%d/%m"),
        )
    logger.info("Dólar CCL/MEP actualizado")


def obtener_indicador_mercado(db: Session, label: str) -> IndicadorMercadoCache | None:
    return db.query(IndicadorMercadoCache).filter(IndicadorMercadoCache.label == label).first()


def actualizar_todo(db: Session) -> None:
    """Punto de entrada único para el job diario de las 18:05 (ver scheduler.py)."""
    actualizar_rem_inflacion(db)
    actualizar_riesgo_pais(db)
    actualizar_dolar_ccl_mep(db)
