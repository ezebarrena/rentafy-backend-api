"""RF-13 a RF-29: listado, cobertura de categorías, búsqueda, filtrado, ordenamiento y
detalle de instrumentos. Espeja rentafy-frontend/src/data/filters.ts y sort.ts."""

import math
import statistics
from collections import defaultdict
from datetime import date, timedelta
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..financial_utils import obtener_rem_inflacion
from ..deps import get_db_financiera
from ..models_financiera import Caucion, Cotizacion, Instrumento, Scoring
from ..schemas import (
    CurvaRendimiento,
    InstrumentoConsistente,
    InstrumentoEnAlza,
    InstrumentoListItem,
    InstrumentoOpcion,
    InstrumentoOut,
    PaginatedInstrumentos,
    PerfilInversor,
    PlazoInversion,
    PuntoCurva,
    PuntoHistorico,
    PuntoScore,
)
from ..scoring import compute_score, pesos_vigentes
from ..serializers import to_detail, to_list_item, ultimas_cotizaciones, ultimos_scoring

DIAS_SCORING_HISTORICO = 20
CONSISTENCIA_DIAS = 10
CONSISTENCIA_MINIMO_DIAS = 5
CONSISTENCIA_TOP_N = 10
# R² mínimo para que una pendiente positiva cuente como tendencia real en "Scores en alza" (ver
# instrumentos_en_alza) — 0.7 excluye el caso real detectado (RC2CC, R²≈0.62, puro zigzag) sin
# ser tan exigente como para pedir una recta casi perfecta.
UMBRAL_R2_EN_ALZA = 0.7
CURVA_MINIMO_PUNTOS = 3
# Candidatos a excluir: instrumentos a menos de ~55 días del vencimiento, donde la TIR
# anualizada amplifica cualquier ruido de precio. No se excluyen todos los que caen acá — ver
# CURVA_RESIDUO_MINIMO — porque no todo tramo corto es ruidoso: las LECAP más cortas del
# catálogo, por ejemplo, siguen la tendencia del resto sin desviarse.
CURVA_DURATION_MINIMA = 0.15
# De los candidatos por duration, solo se excluyen los que además se desvían más de esto (en
# puntos porcentuales de TIR) del ajuste hecho con todos los puntos del grupo — ej. X30S6
# (BONCER a 29 días) rinde 13,05% contra una curva que predice ~8,9% ahí: un desvío real, no
# ruido de tramo corto nomás. Con esto se distingue ese caso de una LECAP corta que sí sigue la
# tendencia (desvío chico) y no debería perderse del ajuste.
CURVA_RESIDUO_MINIMO = 1.5

_CURVA_LABELS = {
    "ON": "Obligaciones Negociables",
    "LECAP": "LECAP",
    "BONCAP": "BONCAP",
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

SortKey = Literal[
    "ticker", "tipo", "moneda", "precio", "score", "tir", "vencimiento", "variacion", "riesgo", "liquidez", "volumen"
]

_RIESGO_ORDEN = {"Bajo": 0, "Medio": 1, "Alto": 2}
_LIQUIDEZ_ORDEN = {"Baja": 0, "Media": 1, "Alta": 2}

# Mismos cortes que PlazoInversion (ver scoring.py: "corto ≤1 año, mediano 1-3 años, largo >3
# años"), pero para el PROPIO vencimiento del instrumento (filtro de "Más filtros"), no para el
# horizonte de inversión del usuario — son dos conceptos distintos que hoy comparten los mismos
# cortes de año, por eso el parámetro se llama distinto (`plazo_vencimiento`, no `plazo`).
_DIAS_CORTO_MAXIMO = 365
_DIAS_MEDIANO_MAXIMO = 365 * 3


@router.get("", response_model=PaginatedInstrumentos)
def listar_instrumentos(
    db: Session = Depends(get_db_financiera),
    perfil: PerfilInversor = Query("moderado"),
    plazo: PlazoInversion = Query("mediano"),
    tipo: Optional[str] = None,
    subtipo: Optional[str] = None,
    moneda: Optional[str] = None,
    riesgo: Optional[str] = None,
    liquidez: Optional[str] = None,
    plazo_vencimiento: Optional[Literal["corto", "mediano", "largo"]] = None,
    emisor: Optional[str] = None,
    tir_min: Optional[float] = None,
    tir_max: Optional[float] = None,
    # Filtro por calificación (ver scoreLabel/SCORE_RANGO_POR_CALIFICACION en el frontend,
    # data/scoring.ts): el frontend manda el rango exacto del tramo elegido (ej. Muy bueno =
    # 75-89.99), no un piso "de acá para arriba" — mismos tramos que ScoreBadge en toda la app.
    score_min: Optional[float] = None,
    score_max: Optional[float] = None,
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
    # siquiera llegan a Python. "riesgo" NO entra acá a propósito (ver más abajo, junto a
    # tir_min/score_min): ya no es una columna estática de Instrumento, se deriva del Scoring
    # de hoy (ver serializers.py:_riesgo_bucket) — recién se conoce después de resolver el
    # batch de Scoring, no antes.
    query = db.query(Instrumento).filter(Instrumento.activo.is_(True))
    if tipo and tipo != "TODOS":
        query = query.filter(Instrumento.tipo == tipo)
    if subtipo and subtipo != "TODOS":
        query = query.filter(Instrumento.subtipo == subtipo)
    if moneda:
        query = query.filter(Instrumento.moneda == moneda)
    if liquidez and liquidez != "TODOS":
        query = query.filter(Instrumento.liquidez == liquidez)
    if plazo_vencimiento:
        hoy = date.today()
        corte_corto = hoy + timedelta(days=_DIAS_CORTO_MAXIMO)
        corte_mediano = hoy + timedelta(days=_DIAS_MEDIANO_MAXIMO)
        if plazo_vencimiento == "corto":
            query = query.filter(Instrumento.vencimiento <= corte_corto)
        elif plazo_vencimiento == "mediano":
            query = query.filter(Instrumento.vencimiento > corte_corto, Instrumento.vencimiento <= corte_mediano)
        else:
            query = query.filter(Instrumento.vencimiento > corte_mediano)
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
    pesos_base = pesos_vigentes(db)
    items = [
        item
        for item in (
            to_list_item(i, perfil, plazo, cot=cotizaciones.get(i.ticker), sc=scorings.get(i.ticker), pesos_base=pesos_base)
            for i in instrumentos
        )
        if item is not None
    ]

    if riesgo and riesgo != "TODOS":
        items = [i for i in items if i.riesgo == riesgo]
    if tir_min is not None:
        items = [i for i in items if i.tir is not None and i.tir >= tir_min]
    if tir_max is not None:
        items = [i for i in items if i.tir is not None and i.tir <= tir_max]
    if score_min is not None:
        items = [i for i in items if i.score is not None and i.score >= score_min]
    if score_max is not None:
        items = [i for i in items if i.score is not None and i.score <= score_max]

    def sort_key(item):
        if sort == "tipo":
            return item.subtipo or item.tipo
        if sort == "moneda":
            return item.moneda
        if sort == "precio":
            return item.precio
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
            ticker=i.ticker, nombre=i.nombre, tipo=i.tipo, subtipo=i.subtipo, moneda=i.moneda,
            vencimiento=i.vencimiento,
        )
        for i in instrumentos
    ]


def _construir_curvas(
    instrumentos: list[Instrumento], cotizaciones: dict[str, Cotizacion]
) -> dict[tuple[str, Optional[str], str], CurvaRendimiento]:
    """Agrupa por (tipo, subtipo, moneda) — mismo agrupamiento que usan los factores del
    Servicio de IA — y ajusta una curva por grupo con {CURVA_MINIMO_PUNTOS}+ instrumentos con
    TIR y duration/plazo residual calculables (ej. DUAL/TAMAR/Dólar Linked no tienen TIR hoy —
    ver limitación conocida de estimación de margen, quedan afuera). Compartida por /curvas
    (todos los grupos) y /{ticker}/curva (uno solo) para no repetir el ajuste de dos pasadas."""
    grupos: dict[tuple[str, Optional[str], str], list[PuntoCurva]] = defaultdict(list)
    for inst in instrumentos:
        cot = cotizaciones.get(inst.ticker)
        if cot is None or cot.tir is None:
            continue
        duration = cot.duration if cot.duration is not None else cot.plazo_residual
        if duration is None:
            continue
        duration_redondeada = round(duration, 2)
        # ln(duration) no está definido en 0 — un instrumento a 1-2 días del vencimiento tiene
        # duration real pequeña pero positiva (ej. 0.003 años) que este redondeo a 2 decimales
        # colapsa a 0.0 exacto (caso real detectado: X30S6, vence en 2 días). Sin este chequeo,
        # ese punto rompía el ajuste de TODO el grupo (y por lo tanto /curvas entero, para
        # cualquier tipo) con un ValueError de math.log — se excluye acá, antes de que llegue a
        # _ajustar_curva, en vez de solo en el filtro de "candidato a excluir" de más abajo (ese
        # filtro corre DESPUÉS del primer ajuste con todos los puntos, ya tarde para evitar el
        # crash).
        if duration_redondeada <= 0:
            continue
        clave = (inst.tipo, inst.subtipo, inst.moneda)
        grupos[clave].append(
            PuntoCurva(ticker=inst.ticker, nombre=inst.nombre, duration=duration_redondeada, tir=round(cot.tir, 2))
        )

    resultado: dict[tuple[str, Optional[str], str], CurvaRendimiento] = {}
    for clave, todos in grupos.items():
        tipo, subtipo, moneda = clave
        if len(todos) < CURVA_MINIMO_PUNTOS:
            continue
        # Primera pasada con todos los puntos del grupo, para tener una curva de referencia
        # contra la cual medir el desvío de los candidatos a excluir (duration corta).
        ajuste_previo = _ajustar_curva([(p.duration, p.tir) for p in todos])
        if ajuste_previo is None:
            continue
        a0, b0, _ = ajuste_previo

        puntos, excluidos = [], []
        for p in todos:
            candidato = p.duration < CURVA_DURATION_MINIMA
            desvio = abs(p.tir - (a0 + b0 * math.log(p.duration)))
            if candidato and desvio > CURVA_RESIDUO_MINIMO:
                excluidos.append(p)
            else:
                puntos.append(p)

        if len(puntos) < CURVA_MINIMO_PUNTOS:
            continue
        # Si no hubo exclusiones, este ajuste final coincide exactamente con el previo.
        ajuste = _ajustar_curva([(p.duration, p.tir) for p in puntos])
        if ajuste is None:
            continue
        a, b, r2 = ajuste
        puntos.sort(key=lambda p: p.duration)
        resultado[clave] = CurvaRendimiento(
            tipo=tipo, subtipo=subtipo, moneda=moneda,
            label=_curva_label(tipo, subtipo, moneda),
            puntos=puntos, a=a, b=b, r2=r2,
            excluidos=sorted(excluidos, key=lambda p: p.duration),
        )

    return resultado


def _curva_cauciones(db: Session) -> Optional[CurvaRendimiento]:
    """Curva TNA vs plazo de cauciones — todos los tenores en la última fecha disponible.
    Plazo en days se convierte a años para compatibility con CurvaRendimiento (que usa
    duration). No hay ajuste OLS: solo 6 puntos, no es robusto."""
    ultima_fecha = db.query(func.max(Caucion.fecha)).scalar()
    if ultima_fecha is None:
        return None
    cauciones = db.query(Caucion).filter(Caucion.fecha == ultima_fecha).order_by(Caucion.plazo_dias).all()
    if not cauciones:
        return None
    # Convertir a PuntoCurva (duration en años, ticker sintético).
    puntos = [
        PuntoCurva(
            ticker=f"CAUC{c.plazo_dias}D",
            nombre=f"Caución {c.plazo_dias}d{'e' if c.plazo_dias == 1 else 'ías'}",
            duration=c.plazo_dias / 365.0,
            tir=c.tna
        )
        for c in cauciones
    ]
    # Sin ajuste OLS para cauciones (6 puntos, muy pocos para un fit robusto) — devolver
    # a=0, b=0, r2=0 para indicar "sin modelo" al frontend.
    return CurvaRendimiento(
        tipo='CAUCION',
        subtipo=None,
        moneda='ARS',
        label='Cauciones',
        puntos=puntos,
        a=0.0,
        b=0.0,
        r2=0.0,
        excluidos=[]
    )


@router.get("/curvas", response_model=list[CurvaRendimiento])
def curvas_rendimiento(db: Session = Depends(get_db_financiera)):
    """Curva de rendimiento (TIR contra duration) de los grupos de pares con datos
    suficientes — para la pestaña "Rendimientos". Incluye las cauciones (TNA vs plazo)
    si hay datos disponibles. El detalle de un instrumento puntual no usa este endpoint
    (ver /{ticker}/curva): no necesita los otros grupos."""
    instrumentos = db.query(Instrumento).filter(Instrumento.activo.is_(True)).all()
    cotizaciones = ultimas_cotizaciones(db, [i.ticker for i in instrumentos])
    resultado = list(_construir_curvas(instrumentos, cotizaciones).values())
    # Agregar curva de cauciones al final si existe.
    curva_cauciones = _curva_cauciones(db)
    if curva_cauciones:
        resultado.append(curva_cauciones)
    resultado.sort(key=lambda c: _curva_orden_key(c.label))
    return resultado


def _historial_scoring_reciente(db: Session, dias: int) -> dict[str, list]:
    """Últimas `dias` filas de Scoring por cada instrumento activo, en una sola consulta
    (window function) en vez de una consulta por instrumento — ver
    serializers.py:ultimos_scoring para el mismo patrón de batching aplicado a "el último
    valor" en vez de "los últimos N". Reutilizado por /consistentes y /en-alza: ambos parten
    de la misma serie reciente por ticker, solo difieren en cómo la agregan."""
    rn = func.row_number().over(
        partition_by=Scoring.instrumento_ticker, order_by=Scoring.fecha_calculo.desc()
    ).label("rn")
    subq = (
        db.query(
            Scoring.instrumento_ticker,
            Scoring.fecha_calculo,
            Scoring.rendimiento,
            Scoring.riesgo,
            Scoring.liquidez,
            Scoring.estabilidad,
            rn,
        )
        .join(Instrumento, Instrumento.ticker == Scoring.instrumento_ticker)
        .filter(Instrumento.activo.is_(True))
        .subquery()
    )
    filas = (
        db.query(subq)
        .filter(subq.c.rn <= dias)
        .order_by(subq.c.instrumento_ticker, subq.c.fecha_calculo)
        .all()
    )

    por_ticker: dict[str, list] = defaultdict(list)
    for fila in filas:
        por_ticker[fila.instrumento_ticker].append(fila)
    return por_ticker


def _salto_maximo(scores: list[int]) -> float:
    """Mayor |diferencia| entre dos ruedas consecutivas de la ventana. El desvío estándar de
    toda la ventana diluye un salto puntual entre el resto de valores parejos (ej.
    80/90/80/90/90/90/90/90/90/70: stdev ~6.6 pese a una caída de 20 puntos en dos ruedas) — el
    salto máximo lo penaliza directo, sin promediarlo con el resto."""
    return max(abs(b - a) for a, b in zip(scores, scores[1:]))


def _pendiente_y_ajuste(scores: list[int]) -> tuple[float, float]:
    """Pendiente de la regresión lineal simple (mínimos cuadrados, sin numpy) de `scores`
    contra el número de rueda (0, 1, 2, ...) — puntos de Score que gana (o pierde) en
    promedio por rueda — junto con el R² de ese ajuste (cuánta de la variación de los scores
    explica esa recta, 0 a 1).

    El R² importa porque una pendiente positiva por sí sola no distingue una tendencia real de
    puro ruido: una serie que zigzaguea (ej. 35, 35, 33, 50, 45, 39, 49, 49, 49, 50 — cae fuerte
    y después oscila en un piso más bajo, sin volver a subir de verdad) puede dar pendiente
    positiva solo porque el primer punto de la ventana cayó en un valle y el último en un pico,
    con R² bajo revelando que la recta ajusta mal. Ver `instrumentos_en_alza`, que exige un R²
    mínimo además de pendiente positiva."""
    n = len(scores)
    xs = range(n)
    x_prom = statistics.mean(xs)
    y_prom = statistics.mean(scores)
    numerador = sum((x - x_prom) * (y - y_prom) for x, y in zip(xs, scores))
    denominador = sum((x - x_prom) ** 2 for x in xs)
    pendiente = numerador / denominador if denominador else 0.0
    intercepto = y_prom - pendiente * x_prom
    ss_res = sum((y - (intercepto + pendiente * x)) ** 2 for x, y in zip(xs, scores))
    ss_tot = sum((y - y_prom) ** 2 for y in scores)
    r2 = 1 - ss_res / ss_tot if ss_tot else 0.0
    return pendiente, r2


@router.get("/consistentes", response_model=list[InstrumentoConsistente])
def instrumentos_consistentes(
    db: Session = Depends(get_db_financiera),
    perfil: PerfilInversor = Query("moderado"),
    plazo: PlazoInversion = Query("mediano"),
):
    """Top {CONSISTENCIA_TOP_N} instrumentos cuyo Score viene siendo alto Y estable en las
    últimas {CONSISTENCIA_DIAS} ruedas — a diferencia de "Oportunidades destacadas"/"La
    oportunidad de hoy", que solo miran el Score de hoy, acá importa que se sostenga día tras
    día. Se ordena por promedio menos el mayor salto entre dos ruedas consecutivas (ver
    _salto_maximo): un 89/90/88/91/90 (sin sobresaltos) le gana a un 80/90/80/90/90/90/90/90/
    90/70 (promedio parecido, pero con una caída de 20 puntos en dos ruedas) — el desvío
    estándar de toda la ventana diluye ese tipo de salto puntual entre el resto de valores
    parejos, así que no alcanza como única penalización. Requiere al menos
    {CONSISTENCIA_MINIMO_DIAS} ruedas con Scoring calculado — con menos que eso no hay
    suficiente historial para hablar de "consistencia" todavía."""
    por_ticker = _historial_scoring_reciente(db, CONSISTENCIA_DIAS)
    instrumentos = {
        i.ticker: i
        for i in db.query(Instrumento).filter(Instrumento.ticker.in_(por_ticker.keys())).all()
    }
    pesos_base = pesos_vigentes(db)

    candidatos: list[InstrumentoConsistente] = []
    for ticker, filas_ticker in por_ticker.items():
        if len(filas_ticker) < CONSISTENCIA_MINIMO_DIAS:
            continue
        instrumento = instrumentos.get(ticker)
        if instrumento is None:
            continue
        # compute_score() puede devolver None con 2+ factores sin calcular ese día (ver su
        # docstring) — se descarta ese punto de la serie en vez de romper mean()/pstdev(), igual
        # criterio que el resto del sistema ante datos faltantes: no fabricar, dejar afuera.
        scores = [
            s
            for f in filas_ticker
            if (s := compute_score(f.rendimiento, f.riesgo, f.liquidez, f.estabilidad, perfil, plazo, pesos_base))
            is not None
        ]
        if len(scores) < CONSISTENCIA_MINIMO_DIAS:
            continue
        candidatos.append(
            InstrumentoConsistente(
                ticker=ticker,
                nombre=instrumento.nombre,
                tipo=instrumento.tipo,
                subtipo=instrumento.subtipo,
                scorePromedio=round(statistics.mean(scores), 1),
                desvio=round(statistics.pstdev(scores), 1),
                saltoMaximo=_salto_maximo(scores),
                scores=scores,
            )
        )

    candidatos.sort(key=lambda c: c.scorePromedio - c.saltoMaximo, reverse=True)
    return candidatos[:CONSISTENCIA_TOP_N]


@router.get("/en-alza", response_model=list[InstrumentoEnAlza])
def instrumentos_en_alza(
    db: Session = Depends(get_db_financiera),
    perfil: PerfilInversor = Query("moderado"),
    plazo: PlazoInversion = Query("mediano"),
):
    """Top {CONSISTENCIA_TOP_N} instrumentos cuyo Score viene subiendo rueda a rueda en las
    últimas {CONSISTENCIA_DIAS} — ni el más alto de hoy ("Oportunidades destacadas") ni el más
    estable ("Scores más consistentes"), sino el que está mejorando. Se mide con la pendiente
    de la regresión lineal del Score contra el número de rueda (ver _pendiente_y_ajuste): solo
    entran los que tienen pendiente positiva Y un R² razonable (ver UMBRAL_R2_EN_ALZA) — sin
    esto último, una serie que cae fuerte y después zigzaguea en un piso más bajo (ej. un
    instrumento al que se le cae el factor rendimiento a mitad de ventana) puede dar pendiente
    positiva de pura casualidad de qué día arrancó y terminó la ventana, sin ser una tendencia
    real (caso real detectado: RC2CC, serie 35/35/33/50/45/39/49/49/49/50 — pendiente positiva,
    R² bajo, no es un activo genuinamente en alza)."""
    por_ticker = _historial_scoring_reciente(db, CONSISTENCIA_DIAS)
    instrumentos = {
        i.ticker: i
        for i in db.query(Instrumento).filter(Instrumento.ticker.in_(por_ticker.keys())).all()
    }
    pesos_base = pesos_vigentes(db)

    candidatos: list[InstrumentoEnAlza] = []
    for ticker, filas_ticker in por_ticker.items():
        if len(filas_ticker) < CONSISTENCIA_MINIMO_DIAS:
            continue
        instrumento = instrumentos.get(ticker)
        if instrumento is None:
            continue
        # Ver mismo criterio en instrumentos_consistentes(): None se descarta, no se fabrica.
        scores = [
            s
            for f in filas_ticker
            if (s := compute_score(f.rendimiento, f.riesgo, f.liquidez, f.estabilidad, perfil, plazo, pesos_base))
            is not None
        ]
        if len(scores) < CONSISTENCIA_MINIMO_DIAS:
            continue
        pendiente, r2 = _pendiente_y_ajuste(scores)
        if pendiente <= 0 or r2 < UMBRAL_R2_EN_ALZA:
            continue
        candidatos.append(
            InstrumentoEnAlza(
                ticker=ticker,
                nombre=instrumento.nombre,
                tipo=instrumento.tipo,
                subtipo=instrumento.subtipo,
                pendiente=round(pendiente, 2),
                scores=scores,
            )
        )

    candidatos.sort(key=lambda c: c.pendiente, reverse=True)
    return candidatos[:CONSISTENCIA_TOP_N]


_PLAZO_LABEL = lambda dias: f"{dias} día" if dias == 1 else f"{dias} días"  # noqa: E731


@router.get("/cauciones", response_model=list[InstrumentoListItem])
def listar_cauciones(db: Session = Depends(get_db_financiera)):
    """Cauciones en pesos (ver cauciones.py) disfrazadas de InstrumentoListItem para poder
    reusar InstrumentTable.tsx tal cual — NO son filas de Instrumento (no tienen ticker real ni
    Scoring, `score` siempre viene None acá) y a propósito no las devuelve GET /instrumentos:
    Instrumentos.tsx las pide acá aparte, con su propio botón "Caución", para que Rankings y
    Dashboard (que sí usan GET /instrumentos) nunca las vean.

    `vencimiento` se calcula como hoy + plazo, no es una fecha fija guardada: una caución se
    renueva desde el día que se toma, no vence en una fecha absoluta como un bono."""
    ultima_fecha = db.query(func.max(Caucion.fecha)).scalar()
    if ultima_fecha is None:
        return []
    filas = db.query(Caucion).filter(Caucion.fecha == ultima_fecha).order_by(Caucion.plazo_dias).all()
    hoy = date.today()
    return [
        InstrumentoListItem(
            ticker=f"CAUC{fila.plazo_dias}D",
            nombre=f"Caución a {_PLAZO_LABEL(fila.plazo_dias)}" + (" (estimado)" if fila.estimado else ""),
            tipo="CAUCION",
            subtipo=None,
            moneda="ARS",
            emisor="BYMA",
            vencimiento=hoy + timedelta(days=fila.plazo_dias),
            precio=100.0,
            variacion=0.0,
            volumen=0,
            tir=fila.tna,
            tirSufijo=None,
            riesgo="Bajo",
            liquidez="Alta",
            resumen="Colocación garantizada por BYMA — sin fluctuación de precio de mercado como un bono.",
            score=None,
        )
        for fila in filas
    ]


@router.get("/{ticker}/curva", response_model=Optional[CurvaRendimiento])
def curva_instrumento(ticker: str, db: Session = Depends(get_db_financiera)):
    """Curva de rendimiento del grupo de pares de un instrumento puntual — versión liviana de
    /curvas para InstrumentDetail.tsx, que solo necesita el grupo de la ficha que se está
    viendo, no los otros 4. Trae y agrupa solo los instrumentos con el mismo (tipo, subtipo,
    moneda), no el catálogo activo entero. Null si el grupo no tiene suficientes instrumentos
    para un ajuste, o si este instrumento no tiene TIR/duration calculables."""
    instrumento = db.query(Instrumento).filter(Instrumento.ticker == ticker.upper()).first()
    if instrumento is None:
        raise HTTPException(404, f"No se encontró el instrumento «{ticker}»")
    pares = (
        db.query(Instrumento)
        .filter(
            Instrumento.activo.is_(True),
            Instrumento.tipo == instrumento.tipo,
            Instrumento.subtipo == instrumento.subtipo,
            Instrumento.moneda == instrumento.moneda,
        )
        .all()
    )
    cotizaciones = ultimas_cotizaciones(db, [i.ticker for i in pares])
    clave = (instrumento.tipo, instrumento.subtipo, instrumento.moneda)
    return _construir_curvas(pares, cotizaciones).get(clave)


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
    ticker: str,
    db: Session = Depends(get_db_financiera),
    perfil: PerfilInversor = Query("moderado"),
    plazo: PlazoInversion = Query("mediano"),
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
    pesos_base = pesos_vigentes(db)
    # compute_score() puede devolver None con 2+ factores sin calcular ese día (ver su
    # docstring) — se omite ese punto del gráfico en vez de romper PuntoScore.score (int, no
    # nullable): un hueco puntual en la serie es preferible a inventar un valor o tumbar el
    # endpoint.
    puntos = [
        (f.fecha_calculo, compute_score(f.rendimiento, f.riesgo, f.liquidez, f.estabilidad, perfil, plazo, pesos_base))
        for f in filas
    ]
    return [PuntoScore(fecha=fecha, score=score) for fecha, score in puntos if score is not None]


@router.get("/{ticker}", response_model=InstrumentoOut)
def detalle_instrumento(
    ticker: str,
    db: Session = Depends(get_db_financiera),
    perfil: PerfilInversor = Query("moderado"),
    plazo: PlazoInversion = Query("mediano"),
):
    instrumento = db.query(Instrumento).filter(Instrumento.ticker == ticker.upper()).first()
    if instrumento is None:
        raise HTTPException(404, f"No se encontró el instrumento «{ticker}»")
    rem_inflacion_12m = obtener_rem_inflacion(db) if instrumento.subtipo == "BONCER" else None
    detalle = to_detail(instrumento, perfil, plazo, rem_inflacion_12m, pesos_vigentes(db))
    if detalle is None:
        raise HTTPException(409, f"El instrumento «{ticker}» todavía no tiene una cotización cargada")
    return detalle
