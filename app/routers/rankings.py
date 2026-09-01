"""RF-30 a RF-36: ranking general y por categoría, adaptado al perfil inversor.

RF-31 exige no comparar en un mismo ranking instrumentos en pesos y dólares: el parámetro
`moneda` es obligatorio en la práctica (el frontend siempre lo envía, ver InstrumentFilterBar).
"""

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..deps import get_db_financiera
from ..models_financiera import Instrumento
from ..schemas import PaginatedInstrumentos, PerfilInversor, ScoreRentafyPesosOut
from ..scoring import PESOS_PERFIL
from ..serializers import to_list_item, ultimas_cotizaciones, ultimos_scoring

router = APIRouter(tags=["rankings"])


@router.get("/rankings", response_model=PaginatedInstrumentos)
def ranking(
    db: Session = Depends(get_db_financiera),
    perfil: PerfilInversor = Query("moderado"),
    moneda: str = Query("ARS"),
    tipo: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
):
    query = db.query(Instrumento).filter(Instrumento.moneda == moneda, Instrumento.activo.is_(True))
    if tipo and tipo != "TODOS":
        query = query.filter(Instrumento.tipo == tipo)
    instrumentos = query.all()

    tickers = [i.ticker for i in instrumentos]
    cotizaciones = ultimas_cotizaciones(db, tickers)
    scorings = ultimos_scoring(db, tickers)
    items = [
        item
        for item in (
            to_list_item(i, perfil, cot=cotizaciones.get(i.ticker), sc=scorings.get(i.ticker))
            for i in instrumentos
        )
        if item is not None
    ]
    items.sort(key=lambda i: i.score if i.score is not None else float("-inf"), reverse=True)

    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start : start + page_size]

    return PaginatedInstrumentos(items=page_items, total=total, page=page, pageSize=page_size)


@router.get("/score-rentafy/pesos", response_model=ScoreRentafyPesosOut)
def pesos_por_perfil(db: Session = Depends(get_db_financiera)):
    """RF-34: pesos vigentes por perfil, usados por la página "¿Qué es el Score Rentafy?"."""
    from ..models_financiera import Modelo

    modelo_activo = db.query(Modelo).filter(Modelo.activo == True).first()  # noqa: E712
    modelo_id = modelo_activo.id if modelo_activo else "v1.4.0"
    return ScoreRentafyPesosOut(modeloId=modelo_id, pesos=PESOS_PERFIL)
