"""Datos de las pestañas "Dólar de equilibrio", "Sensibilidad al riesgo país" y "Riesgo país" de Rendimientos (ver curvas_macro.py). Solo lectura."""

import math
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import curvas_macro, evolucion_curva, rendimientos
from ..deps import get_db_financiera
from ..deps import require_pro
from ..financial_utils import NOMBRE_REM_INFLACION
from ..models_financiera import Cotizacion, IndicadorMacro, Instrumento, SerieReferencia
from ..schemas import (
    DolarEquilibrio,
    InflacionEquilibrio,
    InflacionOut,
    PuntoDolarEquilibrio,
    PuntoDolares,
    PuntoInflacion,
    PuntoInflacionEquilibrio,
    PuntoRiesgoPais,
    SensibilidadBono,
    SensibilidadRiesgoPais,
)
from ..serializers import ultimas_cotizaciones
from .rendimiento import _precios_y_flujos, _ultima_fecha
from .instrumentos import _ajustar_curva, _curva_label, _curva_orden_key, _puntos_curva_unica, _ultima_inflacion_mensual

router = APIRouter(prefix="/curvas-macro", tags=["curvas-macro"], dependencies=[Depends(require_pro)])

DOLAR_EQUILIBRIO_PLAZO_MAX_DIAS = 5 * 365


@router.get("/riesgo-pais", response_model=list[PuntoRiesgoPais])
def riesgo_pais(desde: Optional[date] = Query(None, description="Por defecto, desde septiembre de 2021"), ):
    """Serie diaria de riesgo país (puntos básicos). La fuente es ArgentinaDatos, con un caché de 6 horas."""
    inicio = desde or date(2021, 9, 1)
    return [PuntoRiesgoPais(fecha=f, valor=v) for f, v in curvas_macro.serie_riesgo_pais() if f >= inicio]


@router.get("/inflacion", response_model=InflacionOut)
def inflacion(desde: Optional[date] = Query(None, description="Por defecto, desde enero de 2021"), db: Session = Depends(get_db_financiera)):
    """IPC mensual e interanual por mes (ArgentinaDatos, caché de 12 horas) y la inflación esperada a 12 meses (REM) que ya guardamos."""
    inicio = desde or date(2021, 1, 1)
    filas = curvas_macro.unir_inflacion(curvas_macro.serie_inflacion("inflacion"), curvas_macro.serie_inflacion("inflacionInteranual"), inicio)
    rem = db.query(IndicadorMacro).filter(IndicadorMacro.nombre == NOMBRE_REM_INFLACION).first()
    return InflacionOut(
        serie=[PuntoInflacion(**f) for f in filas],
        inflacionEsperadaAnual=rem.valor if rem else None,
        inflacionEsperadaFecha=rem.fecha if rem else None,
    )


@router.get("/inflacion-equilibrio", response_model=InflacionEquilibrio)
def inflacion_equilibrio(db: Session = Depends(get_db_financiera)):
    """Para cada bono CER (BONCER), la inflación que lo iguala con una tasa fija en pesos del mismo plazo. La tasa fija se estima con la curva
    logarítmica de los instrumentos a tasa fija (LECAP, BONCAP, bonos en pesos) y solo se calcula dentro del rango de plazos que esa curva cubre:
    más allá sería extrapolar."""
    instrumentos = db.query(Instrumento).filter(Instrumento.activo.is_(True), Instrumento.moneda == "ARS").all()
    cotizaciones = ultimas_cotizaciones(db, [i.ticker for i in instrumentos])
    puntos = _puntos_curva_unica(instrumentos, cotizaciones, "ARS")
    fijas = [(float(p.plazoDias), p.tir) for p in puntos if p.familia == "fija"]
    cer = [p for p in puntos if p.familia == "cer"]
    ajuste = _ajustar_curva(fijas) if len(fijas) >= 4 else None
    bonos = []
    if ajuste is not None:
        a, b, r2 = ajuste
        minimo, maximo = min(x for x, _ in fijas), max(x for x, _ in fijas)
        for p in cer:
            if not minimo <= p.plazoDias <= maximo:
                continue
            tasa_fija = a + b * math.log(p.plazoDias)
            anual, mensual = curvas_macro.inflacion_de_equilibrio(p.tir, tasa_fija)
            if math.isnan(mensual):
                continue
            bonos.append(
                PuntoInflacionEquilibrio(
                    ticker=p.ticker, nombre=p.nombre, plazoDias=p.plazoDias, tirReal=p.tir, tasaFija=round(tasa_fija, 2),
                    inflacionEquilibrioAnual=round(anual, 2), inflacionEquilibrioMensual=round(mensual, 2),
                )
            )
    ipc = _ultima_inflacion_mensual()
    rem = db.query(IndicadorMacro).filter(IndicadorMacro.nombre == NOMBRE_REM_INFLACION).first()
    return InflacionEquilibrio(
        bonos=sorted(bonos, key=lambda x: x.plazoDias),
        inflacionMensualUltima=ipc["valor"] if ipc else None,
        inflacionMensualMes=ipc["mes_label"] if ipc else None,
        inflacionEsperadaAnual=rem.valor if rem else None,
        instrumentosTasaFija=len(fijas),
        r2=ajuste[2] if ajuste else None,
    )


@router.get("/dolares", response_model=list[PuntoDolares])
def dolares(desde: Optional[date] = Query(None, description="Por defecto, desde septiembre de 2021")):
    """Serie diaria del dólar MEP y del CCL (venta), una fila por fecha. La fuente es ArgentinaDatos, con un caché de 3 horas."""
    inicio = desde or date(2021, 9, 1)
    filas = curvas_macro.unir_dolares(curvas_macro.serie_dolar("mep"), curvas_macro.serie_dolar("ccl"), inicio, curvas_macro.serie_dolar("oficial"))
    return [PuntoDolares(**f) for f in filas]


@router.get("/dolar-equilibrio", response_model=DolarEquilibrio)
def dolar_equilibrio(db: Session = Depends(get_db_financiera)):
    """Para los instrumentos en pesos con TIR en pesos nominales (tasa fija, TAMAR y DUAL): cuánto tendría que subir el dólar hasta el
    vencimiento para igualar al bono. Los BONCER y dólar linked quedan afuera: su TIR no está en pesos nominales. El dólar de referencia es el MEP."""
    instrumentos = db.query(Instrumento).filter(Instrumento.activo.is_(True), Instrumento.moneda == "ARS").all()
    cotizaciones = ultimas_cotizaciones(db, [i.ticker for i in instrumentos])
    serie_mep, serie_ccl = curvas_macro.serie_dolar("mep"), curvas_macro.serie_dolar("ccl")
    mep = serie_mep[-1] if serie_mep else None
    if mep is None:  # la fuente en vivo no respondió: se cae al último MEP que guardó el job diario
        guardado = db.query(SerieReferencia.fecha, SerieReferencia.valor).filter(SerieReferencia.nombre == "mep").order_by(SerieReferencia.fecha.desc()).first()
        mep = (guardado.fecha, guardado.valor) if guardado else None
    ccl = serie_ccl[-1] if serie_ccl else None
    filas = []
    for p in _puntos_curva_unica(instrumentos, cotizaciones, "ARS"):
        if p.baseTir != "nominal" or p.plazoDias > DOLAR_EQUILIBRIO_PLAZO_MAX_DIAS:
            continue
        suba = curvas_macro.suba_del_dolar_para_igualar(p.tir, p.plazoDias)
        filas.append(
            PuntoDolarEquilibrio(
                ticker=p.ticker,
                nombre=p.nombre,
                familia=p.familia,
                plazoDias=p.plazoDias,
                tir=p.tir,
                subaDelDolar=round(suba, 2),
            )
        )
    return DolarEquilibrio(
        instrumentos=filas,
        dolarMep=mep[1] if mep else None,
        dolarMepFecha=mep[0] if mep else None,
        dolarCcl=ccl[1] if ccl else None,
        dolarCclFecha=ccl[0] if ccl else None,
    )


@router.get("/sensibilidad-riesgo-pais", response_model=SensibilidadRiesgoPais)
def sensibilidad_riesgo_pais(db: Session = Depends(get_db_financiera)):
    """Cuánto se movió el precio de cada bono soberano en dólares con cada +100 pb de riesgo país en los últimos dos años. Solo entran los
    bonos con suficiente historia (los más nuevos quedan afuera)."""
    hoy = date.today()
    inicio = curvas_macro.desde_ventana(hoy)
    riesgo = {f: v for f, v in curvas_macro.serie_riesgo_pais() if f >= inicio - timedelta(days=10)}
    bonos = db.query(Instrumento).filter(Instrumento.activo.is_(True), Instrumento.tipo == "BONO", Instrumento.subtipo == "Bono USD").all()
    por_ticker: dict[str, list[tuple[date, float]]] = defaultdict(list)
    filas = (
        db.query(Cotizacion.instrumento_ticker, Cotizacion.fecha, Cotizacion.precio)
        .filter(
            Cotizacion.instrumento_ticker.in_([b.ticker for b in bonos]),
            Cotizacion.fecha >= inicio,
            Cotizacion.precio_stale.is_(False),
        )
        .all()
    )
    for ticker, fecha, precio in filas:
        por_ticker[ticker].append((fecha, precio))
    ultimas = ultimas_cotizaciones(db, [b.ticker for b in bonos])

    resultado = []
    for bono in bonos:
        calculo = curvas_macro.sensibilidad_al_riesgo_pais(por_ticker.get(bono.ticker, []), riesgo)
        if calculo is None:
            continue
        cot = ultimas.get(bono.ticker)
        resultado.append(
            SensibilidadBono(
                ticker=bono.ticker,
                nombre=bono.nombre,
                duration=round(cot.duration, 2) if cot and cot.duration is not None else None,
                tir=round(cot.tir, 2) if cot and cot.tir is not None else None,
                porCada100pb=round(calculo["porCada100pb"], 2),
                r2=round(calculo["r2"], 3),
                observaciones=calculo["observaciones"],
            )
        )
    resultado.sort(key=lambda b: b.porCada100pb)  # los más expuestos (caída más grande) primero
    return SensibilidadRiesgoPais(bonos=resultado, desde=inicio, hasta=hoy)


# --- Rendimiento de un grupo de instrumentos (pestaña "Instrumentos" de Analítica) ---------------------------------------------------------------
VENTANAS_GRUPO = {"7d": 7, "14d": 14, "1m": 30, "3m": 90}
MAX_GANADORES = 10
_cache_grupo: dict[tuple[str, str], tuple[float, dict]] = {}
_lock_grupo = threading.Lock()
CACHE_GRUPO_SEGUNDOS = 600


def serie_mediana_del_grupo(precios: dict, pagos: dict, inicio: date, fin: date) -> list[dict]:
    """Rendimiento total mediano acumulado del grupo desde `inicio`, rueda por rueda: en cada fecha, la mediana del rendimiento de cada instrumento entre
    `inicio` y esa fecha (solo cuentan los que tienen historia y datos sin dudas; con menos de 3 no se informa). Arranca en 0 % el día de inicio."""
    fechas = sorted({f for serie in precios.values() for f, _ in serie if inicio < f <= fin})
    puntos = [{"fecha": inicio.isoformat(), "mediana": 0.0, "n": len(precios)}]
    for f in fechas:
        valores = []
        for ticker, serie in precios.items():
            r = rendimientos.calcular(serie, pagos.get(ticker, []), inicio, f)
            if r.estado == "ok":
                valores.append(r.retorno)
        m = rendimientos.mediana(valores)
        if m is not None:
            puntos.append({"fecha": f.isoformat(), "mediana": m, "n": len(valores)})
    return puntos


@router.get("/grupo-rendimiento")
def grupo_rendimiento(
    grupo: str = Query(..., description="Subtipo del instrumento si lo tiene, si no el tipo (ej. «Bono USD», «BONCER», «ON»)"),
    ventana: str = Query("1m"),
    db: Session = Depends(get_db_financiera),
):
    """Cómo viene rindiendo un grupo de instrumentos: la mediana de su rendimiento total acumulado en la ventana (serie para el gráfico), y los que más y los que
    menos rindieron (hasta MAX_GANADORES de cada lado, sin repetir un instrumento en las dos listas). Cada instrumento se mide en su moneda. Es información histórica."""
    if ventana not in VENTANAS_GRUPO:
        raise HTTPException(400, f"Ventana inválida «{ventana}»: usá {', '.join(VENTANAS_GRUPO)}")
    clave = (grupo, ventana)
    with _lock_grupo:
        guardado = _cache_grupo.get(clave)
        if guardado and time.time() - guardado[0] < CACHE_GRUPO_SEGUNDOS:
            return guardado[1]
    instrumentos = {i.ticker: i for i in db.query(Instrumento).filter(Instrumento.activo.is_(True)) if (i.subtipo or i.tipo) == grupo}
    if not instrumentos:
        raise HTTPException(404, f"No hay instrumentos del grupo «{grupo}»")
    fin = _ultima_fecha(db)
    inicio = rendimientos.inicio_de_ventana(fin, VENTANAS_GRUPO[ventana]) if fin else None
    vacio = {"grupo": grupo, "ventana": ventana, "desde": inicio.isoformat() if inicio else None, "hasta": fin.isoformat() if fin else None, "n": 0, "serie": [], "ganadores": [], "perdedores": []}
    if fin is None:
        return vacio
    precios, pagos = _precios_y_flujos(db, list(instrumentos), inicio - timedelta(days=15), fin)
    precios = {t: precios[t] for t in instrumentos if t in precios}

    filas = []
    for ticker, inst in instrumentos.items():
        r = rendimientos.calcular(precios.get(ticker, []), pagos.get(ticker, []), inicio, fin)
        if r.estado == "ok":
            filas.append({"ticker": ticker, "nombre": inst.nombre, "moneda": inst.moneda, "retorno": r.retorno})
    filas.sort(key=lambda x: x["retorno"], reverse=True)
    k = min(MAX_GANADORES, len(filas) // 2)
    resultado = {
        **vacio,
        "n": len(filas),
        "serie": serie_mediana_del_grupo({t: precios[t] for t in (f["ticker"] for f in filas)}, pagos, inicio, fin) if len(filas) >= rendimientos.MINIMO_PARA_MEDIANA else [],
        "ganadores": filas[:k],
        "perdedores": list(reversed(filas[len(filas) - k:])) if k else [],
    }
    with _lock_grupo:
        _cache_grupo[clave] = (time.time(), resultado)
    return resultado


# Una rueda "completa" tiene TIR para al menos esta cantidad de instrumentos; antes de la carga masiva de TIR externa había ruedas con unos pocos datos sueltos.
RUEDA_COMPLETA_MIN_TIR = 30
_cache_evolucion: dict = {"hasta": 0.0, "datos": None}


@router.get("/evolucion-curva")
def evolucion_de_la_curva(db: Session = Depends(get_db_financiera)):
    """Cómo estaba la curva de cada grupo (TIR contra duration) hoy, hace una semana y hace un mes, y si se empinó o se aplanó (ver evolucion_curva.py). Solo
    con las ruedas que tienen TIR cargada para suficientes instrumentos; si no hay datos tan atrás, esa comparación no aparece."""
    with _lock_grupo:
        if _cache_evolucion["datos"] is not None and time.time() < _cache_evolucion["hasta"]:
            return _cache_evolucion["datos"]
    filas = (
        db.query(Cotizacion.fecha, func.count(Cotizacion.tir))
        .filter(Cotizacion.fecha >= date.today() - timedelta(days=75))
        .group_by(Cotizacion.fecha)
        .all()
    )
    fechas = sorted(f for f, n in filas if n >= RUEDA_COMPLETA_MIN_TIR)
    vacio = {"hoy": None, "primeraRueda": fechas[0].isoformat() if fechas else None, "grupos": []}
    if not fechas:
        return vacio
    hoy = fechas[-1]
    necesarias = {hoy} | {f for f in (evolucion_curva.fecha_de_comparacion(fechas, hoy, d) for _, d in evolucion_curva.COMPARACIONES) if f}
    instrumentos = {i.ticker: i for i in db.query(Instrumento).filter(Instrumento.activo.is_(True))}
    por_grupo: dict[tuple, dict[date, list[tuple[float, float]]]] = defaultdict(lambda: defaultdict(list))
    cotizaciones = db.query(Cotizacion).filter(Cotizacion.fecha.in_(necesarias), Cotizacion.tir.isnot(None), Cotizacion.precio_stale.is_(False)).all()
    for c in cotizaciones:
        inst = instrumentos.get(c.instrumento_ticker)
        if inst is None:
            continue
        duration = c.duration if c.duration is not None else c.plazo_residual
        if duration is None or duration <= 0:
            continue
        por_grupo[(inst.tipo, inst.subtipo, inst.moneda)][c.fecha].append((duration, c.tir))
    grupos = []
    for (tipo, subtipo, moneda), por_fecha in por_grupo.items():
        res = evolucion_curva.evolucion_de_grupo(por_fecha, hoy, fechas)
        if res is not None:
            grupos.append({"grupo": _curva_label(tipo, subtipo, moneda), "tipo": tipo, "subtipo": subtipo, "moneda": moneda, **res})
    grupos.sort(key=lambda g: _curva_orden_key(g["grupo"]))
    resultado = {"hoy": hoy.isoformat(), "primeraRueda": fechas[0].isoformat(), "grupos": grupos}
    with _lock_grupo:
        _cache_evolucion["datos"], _cache_evolucion["hasta"] = resultado, time.time() + 1800
    return resultado
