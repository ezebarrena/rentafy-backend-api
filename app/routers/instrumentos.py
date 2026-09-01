"""RF-13 a RF-29: listado, cobertura de categorías, búsqueda, filtrado, ordenamiento y
detalle de instrumentos. Espeja rentafy-frontend/src/data/filters.ts y sort.ts."""

import math
from collections import defaultdict
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..financial_utils import obtener_rem_inflacion
from ..deps import get_db_financiera
from ..models_financiera import Cotizacion, Instrumento, Scoring
from ..schemas import (
    CurvaRendimiento,
    InstrumentoOpcion,
    InstrumentoOut,
    PaginatedInstrumentos,
    PerfilInversor,
    PuntoCurva,
    PuntoHistorico,
    PuntoScore,
)
from ..scoring import compute_score
from ..serializers import to_detail, to_list_item, ultimas_cotizaciones, ultimos_scoring

DIAS_SCORING_HISTORICO = 20
CURVA_MINIMO_PUNTOS = 3

_CURVA_LABELS = {
    "ON": "Obligaciones Negociables",
    "LECAP": "LECAP",
    "BONCAP": "BONCAP",
    "LETRA": "Letras",
}

# Orden fijo de las pestañas/pills en el frontend (no alfabético): los grupos más consultados
# primero. Cualquier grupo no listado acá cae al final, ordenado alfabéticamente entre sí.
_CURVA_ORDEN = ["Bonos USD", "Bonos BONCER", "Obligaciones Negociables", "LECAP", "BONCAP"]


def _curva_label(tipo: str, subtipo: Optional[str], moneda: str) -> str:
    if tipo == "BONO":
        if subtipo and subtipo.startswith("Bono "):
            return f"Bonos {subtipo[len('Bono '):]}"  # "Bono USD" -> "Bonos USD"
        if subtipo:
            return f"Bonos {subtipo}"
        return f"Bonos {moneda}"
    return _CURVA_LABELS.get(tipo, f"{tipo} {moneda}")


def _curva_orden_key(label: str) -> tuple[int, str]:
    try:
        return (_CURVA_ORDEN.index(label), "")
    except ValueError:
        return (len(_CURVA_ORDEN), label)


def _ajustar_curva(puntos: list[tuple[float, float]]) -> Optional[tuple[float, float, float]]:
    """Regresión TIR = a + b*ln(duration) por mínimos cuadrados, sin depender de numpy (no es
    una dependencia del backend) — son un puñado de sumas, no hace falta más que eso."""
    xs = [math.log(duration) for duration, _ in puntos]
    ys = [tir for _, tir in puntos]
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
    return round(a, 4), round(b, 4), round(r2, 4)

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
    #
    # Todo lo que se puede resolver con una columna de Instrumento se filtra acá, en SQL, en
    # vez de traer el catálogo entero y filtrarlo con list comprehensions en Python — con
    # filtros activos (la mayoría de los usos reales) esto reduce bastante cuántas filas
    # siquiera llegan a Python.
    query = db.query(Instrumento).filter(Instrumento.activo.is_(True))
    if tipo and tipo != "TODOS":
        query = query.filter(Instrumento.tipo == tipo)
    if subtipo and subtipo != "TODOS":
        query = query.filter(Instrumento.subtipo == subtipo)
    if moneda:
        query = query.filter(Instrumento.moneda == moneda)
    if riesgo and riesgo != "TODOS":
        query = query.filter(Instrumento.riesgo == riesgo)
    if emisor and emisor != "TODOS":
        query = query.filter(Instrumento.emisor == emisor)
    if q:
        patron = f"%{q}%"
        query = query.filter(Instrumento.ticker.ilike(patron) | Instrumento.nombre.ilike(patron))
    instrumentos = query.all()

    # Score y TIR no son columnas de Instrumento (se calculan a partir de Scoring/Cotizacion,
    # con la ponderación por perfil recién aplicada acá), así que ordenar/filtrar por ellos
    # sigue siendo un paso en Python — pero ya sobre el subconjunto filtrado arriba, no sobre
    # todo el catálogo activo. La cotización y el scoring más recientes de cada ticker se
    # traen en dos queries batcheadas (no una por instrumento, ver serializers.py).
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


@router.get("/curvas", response_model=list[CurvaRendimiento])
def curvas_rendimiento(db: Session = Depends(get_db_financiera)):
    """Curva de rendimiento (TIR contra duration) por grupo de pares — mismo agrupamiento
    (tipo, subtipo, moneda) que usan los factores del Servicio de IA. Solo se devuelven grupos
    con {CURVA_MINIMO_PUNTOS}+ instrumentos con TIR y duration/plazo residual calculables: sin
    eso no hay curva que ajustar (ej. DUAL/TAMAR/Dólar Linked, que hoy no tienen TIR — ver
    limitación conocida de estimación de margen)."""
    instrumentos = db.query(Instrumento).filter(Instrumento.activo.is_(True)).all()
    cotizaciones = ultimas_cotizaciones(db, [i.ticker for i in instrumentos])

    grupos: dict[tuple[str, Optional[str], str], list[PuntoCurva]] = defaultdict(list)
    for inst in instrumentos:
        cot = cotizaciones.get(inst.ticker)
        if cot is None or cot.tir is None:
            continue
        duration = cot.duration if cot.duration is not None else cot.plazo_residual
        if duration is None or duration <= 0:
            continue
        clave = (inst.tipo, inst.subtipo, inst.moneda)
        grupos[clave].append(
            PuntoCurva(ticker=inst.ticker, nombre=inst.nombre, duration=round(duration, 2), tir=round(cot.tir, 2))
        )

    resultado = []
    for (tipo, subtipo, moneda), puntos in grupos.items():
        if len(puntos) < CURVA_MINIMO_PUNTOS:
            continue
        ajuste = _ajustar_curva([(p.duration, p.tir) for p in puntos])
        if ajuste is None:
            continue
        a, b, r2 = ajuste
        puntos.sort(key=lambda p: p.duration)
        resultado.append(
            CurvaRendimiento(
                tipo=tipo, subtipo=subtipo, moneda=moneda,
                label=_curva_label(tipo, subtipo, moneda),
                puntos=puntos, a=a, b=b, r2=r2,
            )
        )

    resultado.sort(key=lambda c: _curva_orden_key(c.label))
    return resultado


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


@router.get("/{ticker}/scoring-historico", response_model=list[PuntoScore])
def scoring_historico(
    ticker: str, db: Session = Depends(get_db_financiera), perfil: PerfilInversor = Query("moderado")
):
    """Score de los últimos {DIAS_SCORING_HISTORICO} días con Scoring calculado, según el
    perfil solicitado. No hay ningún dato nuevo que almacenar para esto: Scoring ya acumula
    una fila por instrumento y día desde que corre el Servicio de IA (no se pisa, a
    diferencia del resumen en Instrumento) — acá solo se les aplica compute_score(), la misma
    función que ya arma el valor vigente en to_detail/to_list_item."""
    filas = (
        db.query(Scoring)
        .filter(Scoring.instrumento_ticker == ticker.upper())
        .order_by(Scoring.fecha_calculo.desc())
        .limit(DIAS_SCORING_HISTORICO)
        .all()
    )
    filas.reverse()  # ascendente para el gráfico, igual que /historico
    return [
        PuntoScore(
            fecha=f.fecha_calculo,
            score=compute_score(f.rendimiento, f.riesgo, f.liquidez, f.estabilidad, perfil),
        )
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
