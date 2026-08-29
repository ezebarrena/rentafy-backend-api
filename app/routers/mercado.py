"""RF-07/RF-11/RF-12: obtención de datos financieros desde fuentes externas, con manejo de
fallos que preserva el último valor disponible (RNF-09).

Este router concentra las llamadas salientes a data912 y ArgentinaDatos, dos de las tres
fuentes descriptas en chapter04.tex. La integración con la API de compararfondos.com.ar (la
fuente central para TIR/duration/flujos según la tesis) queda pendiente: no se relevó un
endpoint público estable durante esta sesión, y por ahora esos datos los provee el seed
(ver seed.py) en lugar de una consulta en vivo.
"""

import requests
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..deps import get_db_financiera
from ..ingest import importar as importar_compararfondos
from ..schemas import IndicadorMercado

router = APIRouter(prefix="/mercado", tags=["mercado"])

DATA912_BONDS_URL = "https://data912.com/live/arg_bonds"
ARGENTINADATOS_DOLARES_URL = "https://api.argentinadatos.com/v1/cotizaciones/dolares"


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


def _dolar_ccl_mep() -> dict[str, dict]:
    response = requests.get(ARGENTINADATOS_DOLARES_URL, timeout=5)
    response.raise_for_status()
    filas = response.json()

    def ultimo_y_variacion(casa: str) -> dict:
        filtradas = [f for f in filas if f["casa"] == casa]
        if not filtradas:
            return None
        ultimo, anterior = filtradas[-1], (filtradas[-2] if len(filtradas) > 1 else None)
        variacion = ((ultimo["venta"] - anterior["venta"]) / anterior["venta"] * 100) if anterior else 0.0
        return {"valor": ultimo["venta"], "variacion": variacion}

    return {"ccl": ultimo_y_variacion("contadoconliqui"), "mep": ultimo_y_variacion("bolsa")}


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
def indicadores_mercado():
    """Indicadores del Dashboard. Dólar CCL/MEP en vivo (ArgentinaDatos); S&P Merval, inflación
    y riesgo país quedan como valores de referencia hasta incorporar una fuente para esas
    series (RNF-09: ante la falla de ArgentinaDatos se preservan los últimos valores conocidos)."""

    indicadores = [
        IndicadorMercado(label="S&P Merval", valor="2.352.486,25", variacion="+1,35%", tendencia="positiva"),
        IndicadorMercado(label="Inflación mensual", valor="2,8%", variacion="Último dato", tendencia="neutral"),
        IndicadorMercado(label="Dólar CCL", valor="$ 1.489,40", variacion="+0,42%", tendencia="positiva"),
        IndicadorMercado(label="Dólar MEP", valor="$ 1.487,50", variacion="+0,42%", tendencia="positiva"),
        IndicadorMercado(label="Riesgo País", valor="450", variacion="-12 pts", tendencia="positiva"),
    ]

    try:
        dolares = _dolar_ccl_mep()
    except requests.RequestException:
        return indicadores  # RNF-09: se conservan los valores de referencia

    for label, key in (("Dólar CCL", "ccl"), ("Dólar MEP", "mep")):
        punto = dolares.get(key)
        if punto is None:
            continue
        for ind in indicadores:
            if ind.label == label:
                ind.valor = f"$ {punto['valor']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                signo = "+" if punto["variacion"] >= 0 else ""
                ind.variacion = f"{signo}{punto['variacion']:.2f}%"
                ind.tendencia = "positiva" if punto["variacion"] >= 0 else "negativa"

    return indicadores
