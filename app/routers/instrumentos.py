"""RF-13 a RF-29: listado, cobertura de categorías, búsqueda, filtrado, ordenamiento y
detalle de instrumentos. Espeja rentafy-frontend/src/data/filters.ts y sort.ts."""

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..financial_utils import obtener_rem_inflacion
from ..deps import get_db_financiera
from ..models_financiera import Cotizacion, Instrumento
from ..schemas import InstrumentoOpcion, InstrumentoOut, PaginatedInstrumentos, PerfilInversor, PuntoHistorico
from ..serializers import to_detail, to_list_item

router = APIRouter(prefix="/instrumentos", tags=["instrumentos"])

SortKey = Literal["ticker", "score", "tir", "vencimiento", "variacion", "riesgo", "liquidez", "volumen"]

_RIESGO_ORDEN = {"Bajo": 0, "Medio": 1, "Alto": 2}
_LIQUIDEZ_ORDEN = {"Baja": 0, "Media": 1, "Alta": 2}


@router.get("", response_model=PaginatedInstrumentos)
def listar_instrumentos(
    db: Session = Depends(get_db_financiera),
    perfil: PerfilInversor = Query("moderado"),
    tipo: Optional[str] = None,
    subtipo: Optional[str] = None,
    moneda: Optional[str] = None,
    riesgo: Optional[str] = None,
    emisor: Optional[str] = None,
    tir_min: Optional[float] = None,
    tir_max: Optional[float] = None,
    q: Optional[str] = Query(None, description="Búsqueda por ticker o nombre (RF-16)"),
    sort: SortKey = "ticker",
    direction: Literal["asc", "desc"] = "asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(15, ge=1, le=100),
):
    # activo=False: la fuente dejó de reportar el instrumento varias corridas seguidas (bono
    # vencido, delisted, etc. — ver ingest.py:_marcar_ausentes_como_inactivos). Se excluye de
    # los listados para no seguir mostrando un precio cada vez más viejo; el detalle
    # (GET /instrumentos/{ticker}) lo sigue sirviendo igual, por si alguien lo tiene en watchlist.
    instrumentos = db.query(Instrumento).filter(Instrumento.activo.is_(True)).all()

    if tipo and tipo != "TODOS":
        instrumentos = [i for i in instrumentos if i.tipo == tipo]
    if subtipo and subtipo != "TODOS":
        instrumentos = [i for i in instrumentos if i.subtipo == subtipo]
    if moneda:
        instrumentos = [i for i in instrumentos if i.moneda == moneda]
    if riesgo and riesgo != "TODOS":
        instrumentos = [i for i in instrumentos if i.riesgo == riesgo]
    if emisor and emisor != "TODOS":
        instrumentos = [i for i in instrumentos if i.emisor == emisor]
    if q:
        query = q.lower()
        instrumentos = [
            i for i in instrumentos if query in i.ticker.lower() or query in i.nombre.lower()
        ]

    items = [item for item in (to_list_item(i, perfil) for i in instrumentos) if item is not None]

    if tir_min is not None:
        items = [i for i in items if i.tir is not None and i.tir >= tir_min]
    if tir_max is not None:
        items = [i for i in items if i.tir is not None and i.tir <= tir_max]

    def sort_key(item):
        if sort == "score":
            return item.score if item.score is not None else float("-inf")
        if sort == "tir":
            return item.tir if item.tir is not None else float("-inf")
        if sort == "vencimiento":
            return item.vencimiento
        if sort == "variacion":
            return item.variacion
        if sort == "riesgo":
            return _RIESGO_ORDEN.get(item.riesgo, -1)
        if sort == "liquidez":
            return _LIQUIDEZ_ORDEN.get(item.liquidez, -1)
        if sort == "volumen":
            return item.volumen
        return item.ticker

    items.sort(key=sort_key, reverse=(direction == "desc"))

    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start : start + page_size]

    return PaginatedInstrumentos(items=page_items, total=total, page=page, pageSize=page_size)


@router.get("/emisores", response_model=list[str])
def emisores_disponibles(db: Session = Depends(get_db_financiera)):
    """Lista de emisores distintos del catálogo, para el filtro avanzado (RF-17)."""
    filas = (
        db.query(Instrumento.emisor)
        .filter(Instrumento.activo.is_(True))
        .distinct()
        .order_by(Instrumento.emisor)
        .all()
    )
    return [fila[0] for fila in filas]


@router.get("/subtipos", response_model=list[str])
def subtipos_disponibles(db: Session = Depends(get_db_financiera), tipo: Optional[str] = None):
    """Subtipos distintos del catálogo (ej. BONCER, TAMAR, DUAL, Dólar Linked, Bono ARS/USD
    dentro de tipo=BONO — ver ingest.py:_TIPO_MAP), para el filtro avanzado de "Más filtros"
    cuando ya se eligió un tipo. Solo BONO tiene subtipos hoy, pero no se hardcodea ese
    supuesto acá — se filtra por lo que realmente haya en el catálogo."""
    query = db.query(Instrumento.subtipo).filter(Instrumento.activo.is_(True), Instrumento.subtipo.isnot(None))
    if tipo and tipo != "TODOS":
        query = query.filter(Instrumento.tipo == tipo)
    filas = query.distinct().order_by(Instrumento.subtipo).all()
    return [fila[0] for fila in filas]


@router.get("/opciones", response_model=list[InstrumentoOpcion])
def opciones_instrumentos(db: Session = Depends(get_db_financiera)):
    """Catálogo completo sin paginar, para selectores (Comparador, Calculadora)."""
    instrumentos = (
        db.query(Instrumento).filter(Instrumento.activo.is_(True)).order_by(Instrumento.ticker).all()
    )
    return [
        InstrumentoOpcion(
            ticker=i.ticker, nombre=i.nombre, tipo=i.tipo, subtipo=i.subtipo, moneda=i.moneda
        )
        for i in instrumentos
    ]


@router.get("/{ticker}/historico", response_model=list[PuntoHistorico])
def historico_instrumento(ticker: str, db: Session = Depends(get_db_financiera)):
    """Serie de precios de cierre diarios (RF-07), tal como los fue dejando el job de las
    18hs (ver scheduler.py). Sin OHLC: ver docstring de PuntoHistorico."""
    filas = (
        db.query(Cotizacion)
        .filter(Cotizacion.instrumento_ticker == ticker.upper())
        .order_by(Cotizacion.fecha)
        .all()
    )
    return [
        PuntoHistorico(fecha=f.fecha, precio=f.precio, volumen=f.volumen, operaciones=f.operaciones)
        for f in filas
    ]


@router.get("/{ticker}", response_model=InstrumentoOut)
def detalle_instrumento(
    ticker: str, db: Session = Depends(get_db_financiera), perfil: PerfilInversor = Query("moderado")
):
    instrumento = db.query(Instrumento).filter(Instrumento.ticker == ticker.upper()).first()
    if instrumento is None:
        raise HTTPException(404, f"No se encontró el instrumento «{ticker}»")
    rem_inflacion_12m = obtener_rem_inflacion(db) if instrumento.subtipo == "BONCER" else None
    detalle = to_detail(instrumento, perfil, rem_inflacion_12m)
    if detalle is None:
        raise HTTPException(409, f"El instrumento «{ticker}» todavía no tiene una cotización cargada")
    return detalle
