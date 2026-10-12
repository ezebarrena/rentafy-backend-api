"""Notificaciones para los usuarios Pro sobre sus favoritos: vencimientos y pagos próximos, y alertas de rendimiento.

Las reglas son funciones puras (reciben datos, devuelven avisos) para poder probarlas aisladas; `generar` las aplica a los favoritos de cada usuario
Pro y guarda lo nuevo. Cada aviso lleva una `clave` que identifica el evento (ej. "venc:AL30D:2030-07-09:7"): junto con el usuario es única en la base,
así que `generar` puede correr varias veces el mismo día (o con el job y a mano) sin duplicar nada. Corre una vez por día, después de que el backend
importa el catálogo y de que el Servicio de IA calcula el Score (ver scheduler.py). Es información: no son recomendaciones de compra ni de venta.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import referencias, rendimientos
from .models_financiera import Cotizacion, FlujoFondo, Instrumento, Scoring
from .models_no_financiera import Notificacion, Usuario
from .planes import PLAN_PRO, plan_de
from .scoring import compute_score, pesos_vigentes

logger = logging.getLogger("rentafy.notificaciones")

# Cuántos días antes se avisa de un vencimiento o un pago. Dos umbrales: el segundo es más cercano, y 3 días (no 1) para que un lunes se avise el viernes
# (el job no corre fines de semana ni feriados).
DIAS_AVISO = (7, 3)
UMBRAL_VARIACION_DIARIA = 3.0  # %: una suba o baja del día de este tamaño o más
UMBRAL_CAMBIO_TIR = 1.5  # puntos porcentuales contra hace una semana
# La TIR anualizada de un instrumento a punto de vencer es puro ruido (una LECAP a días del vencimiento llega a mostrar TIR de cientos de %): no se
# avisa por TIR a menos de DIAS_MINIMOS_PARA_AVISO_TIR del vencimiento ni con valores fuera de lo razonable. Mismo criterio que las curvas de rendimiento.
DIAS_MINIMOS_PARA_AVISO_TIR = 55
TIR_MAXIMA_CREIBLE = 150.0
UMBRAL_CAMBIO_SCORE = 10  # puntos de Score contra hace una semana
DIAS_COMPARACION = 7
VENTANA_RENDIMIENTO_DIAS = 30
UMBRAL_VS_INFLACION = 0.02  # 2 puntos porcentuales por encima o por debajo de la inflación del mes

TIPOS = ("vencimiento", "pago", "variacion", "tir", "rendimiento", "score")


@dataclass(frozen=True)
class Aviso:
    tipo: str
    ticker: str
    titulo: str
    mensaje: str
    fecha_evento: date | None
    clave: str


def _fecha(f: date) -> str:
    return f.strftime("%d/%m/%Y")


def _num(n: float, d: int = 1) -> str:
    return f"{n:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _en_dias(dias: int) -> str:
    return "hoy" if dias == 0 else "mañana" if dias == 1 else f"en {dias} días"


def _umbral_aplicable(dias: int) -> int | None:
    """El umbral más cercano que ya se cumple (7 o 3); None si falta más que el mayor o ya pasó."""
    if dias < 0:
        return None
    cumplidos = [u for u in DIAS_AVISO if dias <= u]
    return min(cumplidos) if cumplidos else None


# --- Reglas ----------------------------------------------------------------------------------------------------------------------------------
def aviso_vencimiento(ticker: str, nombre: str, vencimiento: date, hoy: date) -> Aviso | None:
    dias = (vencimiento - hoy).days
    umbral = _umbral_aplicable(dias)
    if umbral is None:
        return None
    return Aviso(
        "vencimiento", ticker, f"{ticker} vence {_en_dias(dias)}", f"{nombre} vence el {_fecha(vencimiento)}.", vencimiento, f"venc:{ticker}:{vencimiento}:{umbral}"
    )


def avisos_pagos(ticker: str, nombre: str, moneda: str, vencimiento: date, flujos: list[tuple[date, str, float]], hoy: date) -> list[Aviso]:
    """Un aviso por cada pago (cupón o amortización) que cae dentro de la ventana. El que coincide con el vencimiento ya lo cubre `aviso_vencimiento`."""
    avisos = []
    for fecha, tipo, importe in flujos:
        dias = (fecha - hoy).days
        umbral = _umbral_aplicable(dias)
        if umbral is None or fecha == vencimiento:
            continue
        avisos.append(
            Aviso(
                "pago",
                ticker,
                f"{ticker} paga {_en_dias(dias)}",
                f"{nombre}: {tipo.lower()} el {_fecha(fecha)}, {_num(importe, 2)} {moneda} por unidad.",
                fecha,
                f"pago:{ticker}:{fecha}:{tipo}:{umbral}",
            )
        )
    return avisos


def aviso_variacion(ticker: str, nombre: str, variacion_pct: float | None, fecha: date) -> Aviso | None:
    if variacion_pct is None or abs(variacion_pct) < UMBRAL_VARIACION_DIARIA:
        return None
    verbo = "subió" if variacion_pct > 0 else "bajó"
    return Aviso("variacion", ticker, f"{ticker} {verbo} {_num(abs(variacion_pct))}% en el día", f"{nombre} {verbo} {_num(abs(variacion_pct), 2)}% su precio el {_fecha(fecha)}.", None, f"var:{ticker}:{fecha}")


def aviso_tir(ticker: str, nombre: str, tir: float | None, tir_previa: float | None, fecha: date, dias_al_vencimiento: int) -> Aviso | None:
    if tir is None or tir_previa is None or dias_al_vencimiento < DIAS_MINIMOS_PARA_AVISO_TIR:
        return None
    if abs(tir) > TIR_MAXIMA_CREIBLE or abs(tir_previa) > TIR_MAXIMA_CREIBLE or abs(tir - tir_previa) < UMBRAL_CAMBIO_TIR:
        return None
    sube = tir > tir_previa
    semana = fecha.isocalendar()
    return Aviso(
        "tir",
        ticker,
        f"La TIR de {ticker} {'subió' if sube else 'bajó'} {_num(abs(tir - tir_previa))} puntos en la semana",
        f"{nombre}: de {_num(tir_previa, 2)}% a {_num(tir, 2)}% en los últimos {DIAS_COMPARACION} días.",
        None,
        f"tir:{ticker}:{semana[0]}-{semana[1]}:{'+' if sube else '-'}",
    )


def aviso_rendimiento(ticker: str, nombre: str, retorno: float | None, inflacion: float | None, fecha: date) -> Aviso | None:
    """El rendimiento del último mes (precio más cupones, en pesos) contra la inflación (UVA) del mismo período, ambos como fracción."""
    if retorno is None or inflacion is None or abs(retorno - inflacion) < UMBRAL_VS_INFLACION:
        return None
    arriba = retorno > inflacion
    return Aviso(
        "rendimiento",
        ticker,
        f"{ticker} {'le ganó a' if arriba else 'rindió por debajo de'} la inflación del último mes",
        f"{nombre} rindió {_num(retorno * 100)}% en {VENTANA_RENDIMIENTO_DIAS} días y la inflación fue {_num(inflacion * 100)}%.",
        None,
        f"rend:{ticker}:{fecha.year}-{fecha.month:02d}:{'+' if arriba else '-'}",
    )


def aviso_score(ticker: str, nombre: str, score: int | None, score_previo: int | None, fecha: date) -> Aviso | None:
    if score is None or score_previo is None or abs(score - score_previo) < UMBRAL_CAMBIO_SCORE:
        return None
    sube = score > score_previo
    semana = fecha.isocalendar()
    return Aviso(
        "score",
        ticker,
        f"El Score de {ticker} {'subió' if sube else 'bajó'} {abs(score - score_previo)} puntos en la semana",
        f"{nombre}: de {score_previo} a {score} según tu perfil.",
        None,
        f"score:{ticker}:{semana[0]}-{semana[1]}:{'+' if sube else '-'}",
    )


# --- Generación ------------------------------------------------------------------------------------------------------------------------------
def _usuarios_pro_con_favoritos(db_nofin: Session) -> list[Usuario]:
    return [u for u in db_nofin.query(Usuario).all() if u.favoritos and plan_de(u) == PLAN_PRO]


def _perfil_y_plazo(usuario: Usuario) -> tuple[str, str]:
    perfil = max(usuario.perfiles, key=lambda p: p.actualizado_en).perfil if usuario.perfiles else "moderado"
    plazo = max(usuario.plazos, key=lambda p: p.actualizado_en).plazo if usuario.plazos else "mediano"
    return perfil, plazo


def generar(db_fin: Session, db_nofin: Session, hoy: date | None = None) -> dict:
    """Crea las notificaciones nuevas de todos los usuarios Pro, a partir de sus favoritos. Devuelve cuántas se crearon por tipo. Idempotente."""
    hoy = hoy or date.today()
    usuarios = _usuarios_pro_con_favoritos(db_nofin)
    creadas = {t: 0 for t in TIPOS}
    if not usuarios:
        return creadas
    tickers = sorted({f.instrumento_ticker for u in usuarios for f in u.favoritos})
    instrumentos = {i.ticker: i for i in db_fin.query(Instrumento).filter(Instrumento.ticker.in_(tickers))}

    # Fecha de mercado más reciente con precios: las alertas de precio, TIR y rendimiento se miden hasta ahí.
    fin = db_fin.query(func.max(Cotizacion.fecha)).filter(Cotizacion.precio_stale.is_(False)).scalar() or hoy

    # Datos de mercado, una consulta por tipo para todos los favoritos juntos.
    cots: dict[str, list[Cotizacion]] = {}
    for c in (
        db_fin.query(Cotizacion)
        .filter(Cotizacion.instrumento_ticker.in_(tickers), Cotizacion.precio_stale.is_(False), Cotizacion.fecha >= fin - timedelta(days=DIAS_COMPARACION + 10))
        .order_by(Cotizacion.fecha)
    ):
        cots.setdefault(c.instrumento_ticker, []).append(c)
    flujos: dict[str, list[tuple[date, str, float]]] = {}
    for t, f, tipo, importe in db_fin.query(FlujoFondo.instrumento_ticker, FlujoFondo.fecha, FlujoFondo.tipo, FlujoFondo.importe).filter(
        FlujoFondo.instrumento_ticker.in_(tickers), FlujoFondo.fecha >= hoy, FlujoFondo.fecha <= hoy + timedelta(days=max(DIAS_AVISO))
    ):
        flujos.setdefault(t, []).append((f, tipo, importe))
    scores: dict[str, list[Scoring]] = {}
    for s in (
        db_fin.query(Scoring)
        .filter(Scoring.instrumento_ticker.in_(tickers), Scoring.fecha_calculo >= fin - timedelta(days=DIAS_COMPARACION + 10))
        .order_by(Scoring.fecha_calculo)
    ):
        scores.setdefault(s.instrumento_ticker, []).append(s)

    from .routers.rendimiento import _precios_y_flujos  # import acá: ese módulo es un router y no tiene por qué cargarse antes

    inicio_ventana = rendimientos.inicio_de_ventana(fin, VENTANA_RENDIMIENTO_DIAS)
    precios, pagos = _precios_y_flujos(db_fin, tickers, inicio_ventana - timedelta(days=15), fin)
    uva, mep = referencias.cargar(db_fin, "uva", inicio_ventana), referencias.cargar(db_fin, "mep", inicio_ventana)
    inflacion = referencias.variacion(uva, inicio_ventana, fin)
    pesos_base = pesos_vigentes(db_fin)

    # Avisos que no dependen del usuario (se calculan una vez por instrumento).
    por_ticker: dict[str, list[Aviso]] = {}
    for ticker, inst in instrumentos.items():
        avisos = [aviso_vencimiento(ticker, inst.nombre, inst.vencimiento, hoy)]
        avisos += avisos_pagos(ticker, inst.nombre, inst.moneda, inst.vencimiento, flujos.get(ticker, []), hoy)
        filas = cots.get(ticker, [])
        if filas and filas[-1].fecha == fin:
            ultima = filas[-1]
            avisos.append(aviso_variacion(ticker, inst.nombre, ultima.variacion, fin))
            previas = [c for c in filas if c.tir is not None and c.fecha <= fin - timedelta(days=DIAS_COMPARACION)]
            avisos.append(aviso_tir(ticker, inst.nombre, ultima.tir, previas[-1].tir if previas else None, fin, (inst.vencimiento - fin).days))
        r = rendimientos.calcular(precios.get(ticker, []), pagos.get(ticker, []), inicio_ventana, fin, mep=mep if inst.moneda == "USD" else None)
        avisos.append(aviso_rendimiento(ticker, inst.nombre, r.retorno if r.estado == "ok" else None, inflacion, fin))
        por_ticker[ticker] = [a for a in avisos if a is not None]

    ahora = datetime.utcnow()
    for usuario in usuarios:
        perfil, plazo = _perfil_y_plazo(usuario)
        existentes = {c for (c,) in db_nofin.query(Notificacion.clave).filter(Notificacion.usuario_id == usuario.id)}
        for favorito in usuario.favoritos:
            ticker = favorito.instrumento_ticker
            inst = instrumentos.get(ticker)
            if inst is None:
                continue
            avisos = list(por_ticker.get(ticker, []))
            # El Score depende del perfil y del horizonte de cada usuario.
            filas = scores.get(ticker, [])
            if filas and filas[-1].fecha_calculo == fin:
                def puntaje(s: Scoring) -> int | None:
                    return compute_score(s.rendimiento, s.riesgo, s.liquidez, s.estabilidad, perfil, plazo, pesos_base)  # type: ignore[arg-type]

                previas = [s for s in filas if s.fecha_calculo <= fin - timedelta(days=DIAS_COMPARACION)]
                aviso = aviso_score(ticker, inst.nombre, puntaje(filas[-1]), puntaje(previas[-1]) if previas else None, fin)
                if aviso is not None:
                    avisos.append(aviso)
            for a in avisos:
                if a.clave in existentes:
                    continue
                existentes.add(a.clave)
                db_nofin.add(
                    Notificacion(usuario_id=usuario.id, tipo=a.tipo, ticker=a.ticker, titulo=a.titulo, mensaje=a.mensaje, fecha_evento=a.fecha_evento, clave=a.clave, creado_en=ahora)
                )
                creadas[a.tipo] += 1
    db_nofin.commit()
    logger.info("Notificaciones generadas: %s", creadas)
    return creadas
