"""Rendimiento de los instrumentos: por tipo de activo (home) y de los favoritos del usuario contra la inflación, el dólar y el
plazo fijo. Cálculo en rendimientos.py; series de referencia en referencias.py. Es información histórica, no una proyección."""

import threading
import time
from collections import Counter, defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import referencias, rendimientos
from ..deps import get_current_user, get_db_financiera
from ..models_financiera import Cotizacion, FlujoFondo, Instrumento
from ..models_no_financiera import Usuario

router = APIRouter(tags=["rendimiento"])

CACHE_SEGUNDOS = 600
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def _dias(ventana: str) -> int:
    if ventana not in rendimientos.VENTANAS_DIAS:
        raise HTTPException(400, f"Ventana inválida «{ventana}»: usá {', '.join(rendimientos.VENTANAS_DIAS)}")
    return rendimientos.VENTANAS_DIAS[ventana]


def _ultima_fecha(db: Session) -> date | None:
    return db.query(func.max(Cotizacion.fecha)).filter(Cotizacion.precio_stale.is_(False)).scalar()


def _precios_y_flujos(db: Session, tickers: list[str] | None, desde: date, hasta: date):
    """Precios (sin los "stale") y flujos de los tickers dados (o de todos, si es None) para el rango pedido."""
    consulta = db.query(Cotizacion.instrumento_ticker, Cotizacion.fecha, Cotizacion.precio).filter(
        Cotizacion.precio_stale.is_(False), Cotizacion.precio > 0, Cotizacion.fecha >= desde, Cotizacion.fecha <= hasta
    )
    flujos = db.query(FlujoFondo.instrumento_ticker, FlujoFondo.fecha, FlujoFondo.importe).filter(
        FlujoFondo.fecha >= desde, FlujoFondo.fecha <= hasta
    )
    if tickers is not None:
        consulta = consulta.filter(Cotizacion.instrumento_ticker.in_(tickers))
        flujos = flujos.filter(FlujoFondo.instrumento_ticker.in_(tickers))
    precios: dict[str, list] = defaultdict(list)
    for t, f, p in consulta.order_by(Cotizacion.fecha):
        precios[t].append((f, p))
    pagos: dict[str, list] = defaultdict(list)
    for t, f, i in flujos:
        pagos[t].append((f, i))
    return precios, pagos


@router.get("/rendimiento/tipos")
def rendimiento_por_tipo(ventana: str = Query("14d"), db: Session = Depends(get_db_financiera)):
    """Mediana del rendimiento total de cada tipo de activo en la ventana (cada instrumento en SU moneda: los bonos y las ON en
    dólares se miden en dólares). Solo cuentan instrumentos con historia suficiente y sin datos dudosos."""
    dias = _dias(ventana)
    with _lock:
        guardado = _cache.get(ventana)
        if guardado and time.time() - guardado[0] < CACHE_SEGUNDOS:
            return guardado[1]

    fin = _ultima_fecha(db)
    if fin is None:
        return {"ventana": ventana, "dias": dias, "desde": None, "hasta": None, "tipos": []}
    inicio = rendimientos.inicio_de_ventana(fin, dias)
    instrumentos = {i.ticker: i for i in db.query(Instrumento).filter(Instrumento.activo.is_(True))}
    precios, pagos = _precios_y_flujos(db, list(instrumentos), inicio - timedelta(days=15), fin)

    por_grupo: dict[str, list[float]] = defaultdict(list)
    monedas: dict[str, Counter] = defaultdict(Counter)
    filtro: dict[str, tuple[str, str | None]] = {}  # grupo -> (tipo, subtipo): lo que necesita la lista de instrumentos para filtrar
    for ticker, inst in instrumentos.items():
        r = rendimientos.calcular(precios.get(ticker, []), pagos.get(ticker, []), inicio, fin)
        grupo = inst.subtipo or inst.tipo
        por_grupo[grupo]  # el tipo aparece aunque ninguno de sus instrumentos tenga historia suficiente (n = 0)
        monedas[grupo][inst.moneda] += 1
        filtro[grupo] = (inst.tipo, inst.subtipo)
        if r.estado == "ok":
            por_grupo[grupo].append(r.retorno)

    tipos = [
        {
            "grupo": g,
            "mediana": rendimientos.mediana(v),
            "n": len(v),
            "moneda": monedas[g].most_common(1)[0][0],
            "tipo": filtro[g][0],
            "subtipo": filtro[g][1],
        }
        for g, v in por_grupo.items()
    ]
    tipos.sort(key=lambda t: (t["mediana"] is None, -(t["mediana"] or 0)))
    resultado = {"ventana": ventana, "dias": dias, "desde": inicio.isoformat(), "hasta": fin.isoformat(), "tipos": tipos}
    with _lock:
        _cache[ventana] = (time.time(), resultado)
    return resultado


@router.get("/watchlist/rendimiento")
def rendimiento_favoritos(
    ventana: str = Query("1m"),
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db_financiera),
):
    """Cuánto habrían rendido, EN PESOS, los favoritos del usuario en la ventana (o desde que cada uno se agregó, con
    ventana=desde), contra la inflación (UVA), el dólar MEP y un plazo fijo del mismo período. Los instrumentos en dólares se
    pasan a pesos con el MEP de cada fecha, para que sean comparables con la inflación."""
    desde_alta = ventana == "desde"
    dias = None if desde_alta else _dias(ventana)
    fin = _ultima_fecha(db)
    favoritos = {f.instrumento_ticker: f.creado_en.date() for f in usuario.favoritos}
    vacio = {"ventana": ventana, "desde": None, "hasta": fin.isoformat() if fin else None, "instrumentos": [], "cartera": None, "referencias": []}
    if fin is None or not favoritos:
        return vacio

    referencias.asegurar(db)
    inicios = {t: (alta if desde_alta else rendimientos.inicio_de_ventana(fin, dias)) for t, alta in favoritos.items()}
    primero = min(inicios.values())
    nombres = {i.ticker: i for i in db.query(Instrumento).filter(Instrumento.ticker.in_(list(favoritos)))}
    precios, pagos = _precios_y_flujos(db, list(favoritos), primero - timedelta(days=15), fin)
    uva, mep, tasa = (referencias.cargar(db, n, primero) for n in ("uva", "mep", "tasa_pf"))

    filas, retornos = [], []
    ref_por_instrumento: dict[str, list[float]] = {"inflacion": [], "mep": [], "plazoFijo": []}
    for ticker, inst in nombres.items():
        inicio = inicios[ticker]
        fila = {"ticker": ticker, "nombre": inst.nombre, "moneda": inst.moneda, "agregado": favoritos[ticker].isoformat()}
        if inicio >= fin:
            filas.append({**fila, "estado": "reciente", "retorno": None, "dias": 0})
            continue
        r = rendimientos.calcular(
            precios.get(ticker, []), pagos.get(ticker, []), inicio, fin, mep=mep if inst.moneda == "USD" else None
        )
        inflacion = referencias.variacion(uva, inicio, fin)
        fila.update(estado=r.estado, retorno=r.retorno, dias=(fin - inicio).days, desde=inicio.isoformat(), vsInflacion=None)
        if r.estado == "ok":
            retornos.append(r.retorno)
            if inflacion is not None:
                fila["vsInflacion"] = r.retorno - inflacion
            for clave, valor in (
                ("inflacion", inflacion),
                ("mep", referencias.variacion(mep, inicio, fin)),
                ("plazoFijo", referencias.rendimiento_plazo_fijo(tasa, inicio, fin)),
            ):
                if valor is not None:
                    ref_por_instrumento[clave].append(valor)
        filas.append(fila)

    promedio = lambda v: sum(v) / len(v) if v else None  # noqa: E731 — el promedio simple de los instrumentos con dato
    referencias_out = [
        {"id": "inflacion", "nombre": "Inflación (UVA)", "retorno": promedio(ref_por_instrumento["inflacion"])},
        {"id": "mep", "nombre": "Dólar MEP", "retorno": promedio(ref_por_instrumento["mep"])},
        {"id": "plazoFijo", "nombre": "Plazo fijo", "retorno": promedio(ref_por_instrumento["plazoFijo"])},
    ]
    filas.sort(key=lambda f: (f.get("retorno") is None, -(f.get("retorno") or 0)))
    return {
        "ventana": ventana,
        "desde": None if desde_alta else rendimientos.inicio_de_ventana(fin, dias).isoformat(),
        "hasta": fin.isoformat(),
        "instrumentos": filas,
        "cartera": {"retorno": promedio(retornos), "n": len(retornos)},
        "referencias": referencias_out,
    }
