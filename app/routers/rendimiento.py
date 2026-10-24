"""Rendimiento de los instrumentos: por tipo de activo (home) y de los favoritos del usuario contra la inflación, el dólar y el
plazo fijo. Cálculo en rendimientos.py; series de referencia en referencias.py. Es información histórica, no una proyección."""

from collections import Counter, defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import referencias, rendimientos
from ..cache_ttl import CacheTTL
from ..deps import get_db_financiera, require_pro
from ..models_financiera import Cotizacion, FlujoFondo, Instrumento
from ..models_no_financiera import Usuario

router = APIRouter(tags=["rendimiento"])

_cache = CacheTTL(600)  # por ventana


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
    guardado = _cache.obtener(ventana)
    if guardado is not None:
        return guardado

    fin = _ultima_fecha(db)
    if fin is None:
        return {"ventana": ventana, "dias": dias, "desde": None, "hasta": None, "tipos": []}
    inicio = rendimientos.inicio_de_ventana(fin, dias)
    instrumentos = {i.ticker: i for i in db.query(Instrumento).filter(Instrumento.activo.is_(True))}
    precios, pagos = _precios_y_flujos(db, list(instrumentos), inicio - timedelta(days=15), fin)

    por_grupo: dict[str, list[float]] = defaultdict(list)
    ok_por_grupo: dict[str, list[str]] = defaultdict(list)  # tickers con rendimiento válido: son los que arman la serie del gráfico
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
            ok_por_grupo[grupo].append(ticker)

    # Import acá y no arriba: curvas_macro ya importa de este módulo (sería circular).
    from .curvas_macro import serie_mediana_del_grupo

    def serie(g: str) -> list[dict]:
        """Mediana acumulada del grupo rueda por rueda en la ventana (para el gráfico del home); vacía si el grupo no tiene instrumentos suficientes."""
        tickers = ok_por_grupo.get(g, [])
        if len(tickers) < rendimientos.MINIMO_PARA_MEDIANA:
            return []
        puntos = serie_mediana_del_grupo({t: precios[t] for t in tickers if t in precios}, pagos, inicio, fin)
        return [{"fecha": p["fecha"], "mediana": p["mediana"]} for p in puntos]

    tipos = [
        {
            "grupo": g,
            "mediana": rendimientos.mediana(v),
            "n": len(v),
            "moneda": monedas[g].most_common(1)[0][0],
            "tipo": filtro[g][0],
            "subtipo": filtro[g][1],
            "serie": serie(g),
        }
        for g, v in por_grupo.items()
    ]
    tipos.sort(key=lambda t: (t["mediana"] is None, -(t["mediana"] or 0)))
    resultado = {"ventana": ventana, "dias": dias, "desde": inicio.isoformat(), "hasta": fin.isoformat(), "tipos": tipos}
    _cache.guardar(resultado, ventana)
    return resultado


@router.get("/watchlist/rendimiento")
def rendimiento_favoritos(
    ventana: str = Query("1m"),
    usuario: Usuario = Depends(require_pro),
    db: Session = Depends(get_db_financiera),
):
    """Cuánto habrían rendido, EN PESOS, los favoritos del usuario en la ventana (o desde que cada uno se agregó, con
    ventana=desde), contra la inflación (UVA), el dólar MEP y un plazo fijo del mismo período. Los instrumentos en dólares se
    pasan a pesos con el MEP de cada fecha, para que sean comparables con la inflación."""
    desde_alta = ventana == "desde"
    fin = _ultima_fecha(db)
    # "mes" = lo que va del mes en curso: desde el último día del mes anterior hasta la última rueda.
    dias = None if desde_alta else (fin.day if ventana == "mes" and fin else _dias("1m" if ventana == "mes" else ventana))
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


MESES_GRAFICO = 6


@router.get("/watchlist/rendimiento-mensual")
def rendimiento_favoritos_mensual(
    usuario: Usuario = Depends(require_pro),
    db: Session = Depends(get_db_financiera),
):
    """Mes por mes (los últimos MESES_GRAFICO, el actual hasta la última rueda), cuánto rindieron EN PESOS los favoritos actuales del
    usuario (promedio simple, sin importar cuándo se agregaron) contra la inflación (UVA), el dólar MEP y un plazo fijo del mismo mes."""
    fin = _ultima_fecha(db)
    tickers = [f.instrumento_ticker for f in usuario.favoritos]
    if fin is None or not tickers:
        return {"meses": []}

    # Cada mes va del último día del anterior al último día del mes (o a `fin`, el mes en curso).
    tramos = []
    for k in range(MESES_GRAFICO - 1, -1, -1):
        mes, anio = fin.month - k, fin.year
        while mes < 1:
            mes, anio = mes + 12, anio - 1
        primero = date(anio, mes, 1)
        siguiente = date(anio + (mes == 12), mes % 12 + 1, 1)
        tramos.append((primero, primero - timedelta(days=1), min(siguiente - timedelta(days=1), fin)))

    referencias.asegurar(db)
    arranque = tramos[0][1]
    nombres = {i.ticker: i for i in db.query(Instrumento).filter(Instrumento.ticker.in_(tickers))}
    precios, pagos = _precios_y_flujos(db, tickers, arranque - timedelta(days=15), fin)
    uva, mep, tasa = (referencias.cargar(db, n, arranque) for n in ("uva", "mep", "tasa_pf"))

    meses = []
    for primero, inicio, final in tramos:
        retornos = []
        for ticker, inst in nombres.items():
            r = rendimientos.calcular(
                precios.get(ticker, []), pagos.get(ticker, []), inicio, final, mep=mep if inst.moneda == "USD" else None
            )
            if r.estado == "ok":
                retornos.append(r.retorno)
        meses.append(
            {
                "mes": primero.strftime("%Y-%m"),
                "hasta": final.isoformat(),
                "activos": sum(retornos) / len(retornos) if retornos else None,
                "n": len(retornos),
                "inflacion": referencias.variacion(uva, inicio, final),
                "mep": referencias.variacion(mep, inicio, final),
                "plazoFijo": referencias.rendimiento_plazo_fijo(tasa, inicio, final),
            }
        )
    return {"meses": meses}
