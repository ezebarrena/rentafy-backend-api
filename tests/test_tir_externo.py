"""Tests del relleno de TIR (tir_externo.py:completar_tir_faltante), con una base SQLite en memoria y la fuente externa reemplazada."""

from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import tir_externo
from app.database import BaseFinanciera
from app.models_financiera import Cotizacion, Instrumento


def _sesion():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    BaseFinanciera.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _inst(ticker):
    return Instrumento(
        ticker=ticker, nombre=ticker, tipo="BONO", subtipo="DUAL", moneda="ARS", emisor="X", vencimiento=date.today() + timedelta(days=500),
        riesgo="Medio", liquidez="Alta", activo=True,
    )


def _cot(ticker, dias_atras, tir):
    return Cotizacion(
        instrumento_ticker=ticker, fecha=date.today() - timedelta(days=dias_atras), precio=100.0, variacion=0.0, volumen=1.0, operaciones=1, tir=tir,
    )


def _con_fuente(funcion, cuerpo):
    original, espera = tir_externo.obtener_tir_externo, tir_externo.ESPERA_ENTRE_INTENTOS_SEG
    tir_externo.obtener_tir_externo = funcion
    try:
        cuerpo()
    finally:
        tir_externo.obtener_tir_externo = original
        tir_externo.ESPERA_ENTRE_INTENTOS_SEG = espera


def test_la_fuente_completa_la_tir_de_hoy():
    db = _sesion()
    db.add_all([_inst("A"), _cot("A", 0, None)])
    db.commit()

    def cuerpo():
        r = tir_externo.completar_tir_faltante(db)
        assert r == {"procesados": 1, "completados": 1, "arrastrados": 0, "sinFuente": 0}
        assert db.query(Cotizacion).one().tir == 39.5

    _con_fuente(lambda t: 39.5, cuerpo)


def test_si_la_fuente_falla_se_arrastra_la_ultima_tir_de_los_ultimos_dias():
    db = _sesion()
    db.add_all([_inst("A"), _cot("A", 3, 38.0), _cot("A", 2, 39.0), _cot("A", 0, None)])
    db.commit()

    def cuerpo():
        r = tir_externo.completar_tir_faltante(db)
        assert r["arrastrados"] == 1 and r["sinFuente"] == 0
        assert db.query(Cotizacion).filter(Cotizacion.fecha == date.today()).one().tir == 39.0  # la más reciente, no la más vieja

    _con_fuente(lambda t: None, cuerpo)


def test_si_la_ultima_tir_es_muy_vieja_queda_vacia():
    db = _sesion()
    db.add_all([_inst("A"), _cot("A", tir_externo.DIAS_ARRASTRE_TIR + 3, 38.0), _cot("A", 0, None)])
    db.commit()

    def cuerpo():
        r = tir_externo.completar_tir_faltante(db)
        assert r["arrastrados"] == 0 and r["sinFuente"] == 1
        assert db.query(Cotizacion).filter(Cotizacion.fecha == date.today()).one().tir is None

    _con_fuente(lambda t: None, cuerpo)


def test_una_tir_que_ya_vino_de_la_fuente_propia_no_se_toca():
    db = _sesion()
    db.add_all([_inst("A"), _cot("A", 0, 25.0)])
    db.commit()

    def cuerpo():
        r = tir_externo.completar_tir_faltante(db)
        assert r["procesados"] == 0
        assert db.query(Cotizacion).one().tir == 25.0

    _con_fuente(lambda t: 99.0, cuerpo)


def test_obtener_tir_externo_reintenta_una_vez_antes_de_rendirse():
    llamadas = []

    def fuente(ticker):
        llamadas.append(ticker)
        return None if len(llamadas) == 1 else 12.5

    original, fuentes, espera = tir_externo.obtener_tir_externo, tir_externo.FUENTES, tir_externo.ESPERA_ENTRE_INTENTOS_SEG
    tir_externo.FUENTES, tir_externo.ESPERA_ENTRE_INTENTOS_SEG = [fuente], 0.0
    try:
        assert original("X") == 12.5 and len(llamadas) == 2
    finally:
        tir_externo.FUENTES, tir_externo.ESPERA_ENTRE_INTENTOS_SEG = fuentes, espera
