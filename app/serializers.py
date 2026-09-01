"""Arma las respuestas Instrumento a partir de las filas de Instrumento/Cotizacion/Scoring,
combinando la cotización y el scoring más recientes de cada instrumento.

El score y los factores son opcionales (RNF-29, "tratamiento de datos faltantes"): un
instrumento recién importado desde una fuente de mercado (ver ingest.py) tiene cotización
pero todavía no tiene Scoring calculado, dado que ese cálculo es responsabilidad de un
componente separado (el Servicio de IA, fuera del alcance de este backend)."""

from sqlalchemy import desc
from sqlalchemy.orm import Session

from .models_financiera import Cotizacion, Instrumento, Scoring
from .schemas import FactoresScore, FlujoFondo, InstrumentoListItem, InstrumentoOut, PerfilInversor
from .scoring import compute_score


def _ultima_cotizacion(instrumento: Instrumento):
    if not instrumento.cotizaciones:
        return None
    return max(instrumento.cotizaciones, key=lambda c: c.fecha)


def _ultimo_scoring(instrumento: Instrumento):
    if not instrumento.scores:
        return None
    return max(instrumento.scores, key=lambda s: s.fecha_calculo)


def ultimas_cotizaciones(db: Session, tickers: list[str]) -> dict[str, Cotizacion]:
    """Última Cotizacion de cada ticker en UNA sola query (DISTINCT ON de Postgres, requiere
    que el ORDER BY empiece por la misma columna del DISTINCT ON). Para un listado de N
    instrumentos, esto reemplaza las N consultas que dispara acceder a
    `instrumento.cotizaciones` por cada uno (esa relación carga el historial completo del
    ticker solo para quedarse con el más reciente) — el mismo resultado, sin el N+1."""
    if not tickers:
        return {}
    filas = (
        db.query(Cotizacion)
        .filter(Cotizacion.instrumento_ticker.in_(tickers))
        .order_by(Cotizacion.instrumento_ticker, desc(Cotizacion.fecha))
        .distinct(Cotizacion.instrumento_ticker)
        .all()
    )
    return {c.instrumento_ticker: c for c in filas}


def ultimos_scoring(db: Session, tickers: list[str]) -> dict[str, Scoring]:
    """Ídem `ultimas_cotizaciones` pero para Scoring."""
    if not tickers:
        return {}
    filas = (
        db.query(Scoring)
        .filter(Scoring.instrumento_ticker.in_(tickers))
        .order_by(Scoring.instrumento_ticker, desc(Scoring.fecha_calculo))
        .distinct(Scoring.instrumento_ticker)
        .all()
    )
    return {s.instrumento_ticker: s for s in filas}


def _score(sc, perfil: PerfilInversor) -> float | None:
    if sc is None:
        return None
    return compute_score(sc.rendimiento, sc.riesgo, sc.liquidez, sc.estabilidad, perfil)


def to_list_item(
    instrumento: Instrumento,
    perfil: PerfilInversor,
    cot: Cotizacion | None = None,
    sc: Scoring | None = None,
) -> InstrumentoListItem | None:
    """`cot`/`sc`: si el llamador ya los resolvió en batch (ver `ultimas_cotizaciones`/
    `ultimos_scoring`, para listados de muchos instrumentos), se usan tal cual — si no, cae
    al lookup vía relationship (instrumento por instrumento, para el caso de un solo
    instrumento donde el batch no aporta nada)."""
    if cot is None:
        cot = _ultima_cotizacion(instrumento)
    if cot is None:
        return None
    if sc is None:
        sc = _ultimo_scoring(instrumento)
    return InstrumentoListItem(
        ticker=instrumento.ticker,
        nombre=instrumento.nombre,
        tipo=instrumento.tipo,
        subtipo=instrumento.subtipo,
        moneda=instrumento.moneda,
        emisor=instrumento.emisor,
        vencimiento=instrumento.vencimiento,
        precio=cot.precio,
        variacion=cot.variacion,
        volumen=cot.volumen,
        tir=cot.tir,
        tirSufijo=cot.tir_sufijo,
        riesgo=instrumento.riesgo,
        liquidez=instrumento.liquidez,
        resumen=instrumento.resumen,
        score=_score(sc, perfil),
    )


def _tir_nominal_estimada(instrumento: Instrumento, tir: float | None, rem_inflacion_12m: float | None) -> float | None:
    """Ver bcra.py: compuesta, no aditiva — (1+real)*(1+inflación esperada)-1. Solo tiene
    sentido para BONCER, que cotiza en tasa real porque el capital ya se ajusta por CER
    (ver ingest.py:tir_sufijo)."""
    if instrumento.subtipo != "BONCER" or tir is None or rem_inflacion_12m is None:
        return None
    return round((1 + tir / 100) * (1 + rem_inflacion_12m / 100) * 100 - 100, 2)


def to_detail(
    instrumento: Instrumento, perfil: PerfilInversor, rem_inflacion_12m: float | None = None
) -> InstrumentoOut | None:
    cot = _ultima_cotizacion(instrumento)
    if cot is None:
        return None
    sc = _ultimo_scoring(instrumento)
    return InstrumentoOut(
        ticker=instrumento.ticker,
        nombre=instrumento.nombre,
        tipo=instrumento.tipo,
        subtipo=instrumento.subtipo,
        moneda=instrumento.moneda,
        emisor=instrumento.emisor,
        legislacion=instrumento.legislacion,
        parLegislacion=instrumento.par_legislacion,
        vencimiento=instrumento.vencimiento,
        precio=cot.precio,
        variacion=cot.variacion,
        volumen=cot.volumen,
        operaciones=cot.operaciones,
        tir=cot.tir,
        tirSufijo=cot.tir_sufijo,
        tirNominalEstimada=_tir_nominal_estimada(instrumento, cot.tir, rem_inflacion_12m),
        tna=cot.tna,
        duration=cot.duration,
        plazoResidual=cot.plazo_residual,
        paridad=cot.paridad,
        riesgo=instrumento.riesgo,
        liquidez=instrumento.liquidez,
        precioStale=cot.precio_stale,
        factores=(
            FactoresScore(
                rendimiento=sc.rendimiento,
                riesgo=sc.riesgo,
                liquidez=sc.liquidez,
                estabilidad=sc.estabilidad,
                rendimientoDetalle=sc.rendimiento_detalle,
                riesgoDetalle=sc.riesgo_detalle,
                liquidezDetalle=sc.liquidez_detalle,
                estabilidadDetalle=sc.estabilidad_detalle,
                fechaCalculo=sc.fecha_calculo,
                modeloId=sc.modelo_id,
            )
            if sc is not None
            else None
        ),
        flujos=[FlujoFondo(fecha=f.fecha, tipo=f.tipo, importe=f.importe) for f in instrumento.flujos],
        resumen=instrumento.resumen,
        score=_score(sc, perfil),
    )
