"""RF-07/RF-11/RF-12: obtención de datos financieros desde fuentes externas, con manejo de
fallos que preserva el último valor disponible (RNF-09).

Este router concentra las llamadas salientes a data912 y ArgentinaDatos, dos de las tres
fuentes descriptas en chapter04.tex. La integración con la API de compararfondos.com.ar (la
fuente central para TIR/duration/flujos según la tesis) queda pendiente: no se relevó un
endpoint público estable durante esta sesión, y por ahora esos datos los provee el seed
(ver seed.py) en lugar de una consulta en vivo.
"""

from datetime import datetime

import requests
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..deps import get_db_financiera
from ..financial_utils import REM_INFLACION_12M_URL, obtener_indicador_mercado
from ..ingest import importar as importar_compararfondos
from ..schemas import IndicadorMercado

router = APIRouter(prefix="/mercado", tags=["mercado"])

DATA912_BONDS_URL = "https://data912.com/live/arg_bonds"
ARGENTINADATOS_INFLACION_URL = "https://api.argentinadatos.com/v1/finanzas/indices/inflacion"

_MESES_ES = [
    "ene.", "feb.", "mar.", "abr.", "may.", "jun.",
    "jul.", "ago.", "sep.", "oct.", "nov.", "dic.",
]


@router.get("/bonos/{symbol}")
def precio_bono(symbol: str):
    """Cotización en vivo de un bono/ON/letra vía data912 (sin TIR/duration, ver docstring)."""
    try:
        response = requests.get(DATA912_BONDS_URL, timeout=5)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(502, f"Fuente externa (data912) no disponible: {exc}") from exc

    for bond in response.json():
        if bond["symbol"] == symbol.upper():
            return {"symbol": bond["symbol"], "price": bond["c"], "fuente": "data912"}
    raise HTTPException(404, f"No se encontró el bono «{symbol}» en data912")


def _inflacion_mensual() -> dict:
    """Último dato de inflación mensual (IPC) publicado, con el mes al que corresponde."""
    response = requests.get(ARGENTINADATOS_INFLACION_URL, timeout=5)
    response.raise_for_status()
    filas = response.json()

    ultimo, anterior = filas[-1], (filas[-2] if len(filas) > 1 else None)
    fecha = datetime.strptime(ultimo["fecha"], "%Y-%m-%d")
    mes_label = f"{_MESES_ES[fecha.month - 1]} {fecha.year}"

    detalle = None
    if anterior is not None:
        delta = ultimo["valor"] - anterior["valor"]
        tendencia = "positiva" if delta < 0 else ("negativa" if delta > 0 else "neutral")
        fecha_anterior = datetime.strptime(anterior["fecha"], "%Y-%m-%d")
        mes_anterior_label = f"{_MESES_ES[fecha_anterior.month - 1]} {fecha_anterior.year}"
        valor_anterior = f"{anterior['valor']:.1f}".replace(".", ",")
        detalle = f"vs. {valor_anterior}% ({mes_anterior_label})"
    else:
        tendencia = "neutral"

    return {"valor": ultimo["valor"], "mes_label": mes_label, "tendencia": tendencia, "detalle": detalle}


def _rem_inflacion_esperada() -> dict:
    """Inflación esperada a 12 meses (REM, BCRA) — mismo criterio que _inflacion_mensual(): la
    encuesta es mensual, así que se consulta en vivo en cada carga en vez de cachearse (a
    diferencia de Dólar CCL/MEP y Riesgo País, que sí varían dentro del día)."""
    response = requests.get(REM_INFLACION_12M_URL, timeout=10)
    response.raise_for_status()
    # "detalle" viene descendente por fecha (el más reciente primero, ver financial_utils.py).
    detalle = response.json()["results"][0]["detalle"]
    ultimo, anterior = detalle[0], (detalle[1] if len(detalle) > 1 else None)
    fecha = datetime.strptime(ultimo["fecha"], "%Y-%m-%d")
    mes_label = f"{_MESES_ES[fecha.month - 1]} {fecha.year}"

    detalle_txt, tendencia = None, "neutral"
    if anterior is not None:
        delta = ultimo["valor"] - anterior["valor"]
        tendencia = "positiva" if delta < 0 else ("negativa" if delta > 0 else "neutral")  # bajar es buena noticia
        fecha_anterior = datetime.strptime(anterior["fecha"], "%Y-%m-%d")
        mes_anterior_label = f"{_MESES_ES[fecha_anterior.month - 1]} {fecha_anterior.year}"
        valor_anterior = f"{anterior['valor']:.1f}".replace(".", ",")
        detalle_txt = f"vs. {valor_anterior}% ({mes_anterior_label})"

    return {"valor": ultimo["valor"], "mes_label": mes_label, "tendencia": tendencia, "detalle": detalle_txt}


@router.post("/importar/compararfondos")
def importar_bonos_compararfondos(db: Session = Depends(get_db_financiera)):
    """Dispara la importación en vivo desde compararfondos.com.ar (RF-07/RF-08).

    En producción esto correría como un job periódico (ver RNF-05, "procesamiento
    asincrónico"); acá es un endpoint disparado manualmente para poder probarlo.
    """
    try:
        return importar_compararfondos(db)
    except requests.RequestException as exc:
        raise HTTPException(502, f"Fuente externa (compararfondos.com.ar) no disponible: {exc}") from exc


@router.get("/indicadores", response_model=list[IndicadorMercado])
def indicadores_mercado(db: Session = Depends(get_db_financiera)):
    """Indicadores del Dashboard. Inflación mensual e inflación esperada (REM) siguen en vivo
    (cadencia mensual en la fuente — no justifica cachear). Dólar CCL/MEP y Riesgo País se leen
    de la cache que mantiene al día el job de las 18:05 (ver financial_utils.py) en vez de
    pegarle a ArgentinaDatos en cada carga del Dashboard. RNF-09: ante la falla de una fuente en
    vivo se conserva el valor de referencia hardcodeado más abajo."""

    indicadores = [
        IndicadorMercado(label="Inflación esperada 12 meses", valor="21,8%", variacion="Último dato", tendencia="neutral"),
        IndicadorMercado(label="Inflación mensual", valor="2,8%", variacion="Último dato", tendencia="neutral"),
        IndicadorMercado(label="Dólar CCL", valor="$ 1.489,40", variacion="+0,42%", tendencia="positiva"),
        IndicadorMercado(label="Dólar MEP", valor="$ 1.487,50", variacion="+0,42%", tendencia="positiva"),
        IndicadorMercado(label="Riesgo País", valor="450", variacion="-12 pts", tendencia="positiva"),
    ]

    try:
        inflacion = _inflacion_mensual()
        for ind in indicadores:
            if ind.label == "Inflación mensual":
                ind.valor = f"{inflacion['valor']:.1f}%".replace(".", ",")
                ind.variacion = inflacion["mes_label"]
                ind.tendencia = inflacion["tendencia"]
                ind.enVivo = True
                ind.detalle = inflacion["detalle"]
    except requests.RequestException:
        pass  # RNF-09: se conserva el valor de referencia

    try:
        rem = _rem_inflacion_esperada()
        for ind in indicadores:
            if ind.label == "Inflación esperada 12 meses":
                ind.valor = f"{rem['valor']:.1f}%".replace(".", ",")
                ind.variacion = rem["mes_label"]
                ind.tendencia = rem["tendencia"]
                ind.enVivo = True
                ind.detalle = rem["detalle"]
    except requests.RequestException:
        pass  # RNF-09: se conserva el valor de referencia

    for ind in indicadores:
        if ind.label in ("Dólar CCL", "Dólar MEP", "Riesgo País"):
            cache = obtener_indicador_mercado(db, ind.label)
            if cache is not None:
                ind.valor = cache.valor
                ind.variacion = cache.variacion
                ind.tendencia = cache.tendencia
                ind.enVivo = True
                ind.detalle = cache.detalle

    return indicadores
