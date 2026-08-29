"""RF-47/RF-48: calendario de pagos, cupones y vencimientos agregados de todos los instrumentos."""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..deps import get_db
from ..models import FlujoFondo, Instrumento
from ..schemas import EventoCalendario

router = APIRouter(prefix="/calendario", tags=["calendario"])


@router.get("", response_model=list[EventoCalendario])
def eventos_calendario(
    db: Session = Depends(get_db),
    desde: Optional[date] = None,
    hasta: Optional[date] = None,
):
    query = db.query(FlujoFondo, Instrumento).join(Instrumento, FlujoFondo.instrumento_ticker == Instrumento.ticker)
    if desde:
        query = query.filter(FlujoFondo.fecha >= desde)
    if hasta:
        query = query.filter(FlujoFondo.fecha <= hasta)

    eventos = [
        EventoCalendario(
            fecha=flujo.fecha,
            ticker=instrumento.ticker,
            tipo=flujo.tipo,
            importe=flujo.importe,
            emisor=instrumento.emisor,
        )
        for flujo, instrumento in query.order_by(FlujoFondo.fecha).all()
    ]
    return eventos
