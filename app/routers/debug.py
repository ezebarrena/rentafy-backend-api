"""Herramienta de TESTEO INTERNO — no es parte del producto final. Desglosa paso a paso cómo
se combinan los 4 factores (ya calculados por rentafy-servicioIA, ver ese repo /debug/{ticker}
para el desglose de CADA factor) en el Score final: pesos por perfil, ajuste por plazo de
inversión, y la redistribución cuando falta algún factor (RNF-29).
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..deps import get_db_financiera
from ..models_financiera import Instrumento, Scoring
from ..scoring import AJUSTE_PLAZO, PESOS_PERFIL, _pesos_ajustados, pesos_vigentes
from ..schemas import PerfilInversor, PlazoInversion
from ..serializers import ultimas_cotizaciones, ultimos_scoring

router = APIRouter(prefix="/instrumentos", tags=["debug"])


@router.get("/{ticker}/pesos-explicado")
def pesos_explicado(
    ticker: str,
    db: Session = Depends(get_db_financiera),
    perfil: PerfilInversor = Query("moderado"),
    plazo: PlazoInversion = Query("mediano"),
):
    """TESTEO INTERNO: cómo se combinan los 4 factores YA CALCULADOS (ver Scoring) en el Score
    final para `perfil`/`plazo` — pesos vigentes, ajuste por plazo, redistribución si falta
    algún factor. Para el desglose de CÓMO se llegó a cada factor en sí, ver
    rentafy-servicioIA GET /debug/{ticker}."""
    ticker = ticker.upper()
    instrumento = db.query(Instrumento).filter(Instrumento.ticker == ticker).first()
    if instrumento is None:
        raise HTTPException(404, f"No se encontró el instrumento «{ticker}»")

    sc: Scoring | None = ultimos_scoring(db, [ticker]).get(ticker)
    if sc is None:
        raise HTTPException(409, f"«{ticker}» todavía no tiene Scoring calculado")
    cot = ultimas_cotizaciones(db, [ticker]).get(ticker)

    pesos_base = pesos_vigentes(db)
    pesos_hipotesis = PESOS_PERFIL[perfil]
    ajuste = AJUSTE_PLAZO[plazo]
    pesos_finales = _pesos_ajustados(perfil, plazo, pesos_base)

    factores = {
        "rendimiento": sc.rendimiento,
        "riesgo": sc.riesgo,
        "liquidez": sc.liquidez,
        "estabilidad": sc.estabilidad,
    }
    presentes = {k: v for k, v in factores.items() if v is not None}
    faltantes = [k for k, v in factores.items() if v is None]
    peso_total = sum(pesos_finales[k] for k in presentes)
    numerador = sum(v * pesos_finales[k] for k, v in presentes.items())
    score = round(numerador / peso_total) if peso_total else None

    terminos = " + ".join(f"{v:.2f}×{pesos_finales[k]:.4f}" for k, v in presentes.items())
    formula_final = (
        f"({terminos}) / ({' + '.join(f'{pesos_finales[k]:.4f}' for k in presentes)}) "
        f"= {numerador:.4f} / {peso_total:.4f} = {numerador / peso_total:.4f} → round → {score}"
        if peso_total
        else "sin factores presentes"
    )

    return {
        "ticker": ticker,
        "perfil": perfil,
        "plazo": plazo,
        "factoresVigentes": {
            "fechaCalculo": sc.fecha_calculo.isoformat(),
            "modeloId": sc.modelo_id,
            **factores,
        },
        "factoresFaltantes": faltantes,
        "pesos": {
            "hipotesisPerfil": pesos_hipotesis.model_dump(),
            "vigentesPublicadosPorEntrenamiento": pesos_base[perfil].model_dump(),
            "ajustePlazo": ajuste,
            "formulaAjuste": (
                f"peso_ajustado[factor] = max(0, peso_vigente[factor] + ajuste_plazo[factor]), "
                f"luego renormalizado a que la suma dé 1 (plazo='{plazo}')"
            ),
            "finalesTrasAjusteYRenormalizacion": pesos_finales,
        },
        "redistribucion": {
            "notas": "Los factores faltantes NO se promedian en partes iguales: el peso_total "
            "de la fórmula de abajo ya excluye su peso, así que los presentes se combinan en la "
            "MISMA proporción relativa que ya tenían entre sí (ver compute_score en scoring.py).",
            "pesoTotalPresentes": peso_total,
        },
        "scoreFinal": {"formula": formula_final, "valor": score},
        "precioActual": cot.precio if cot else None,
    }
