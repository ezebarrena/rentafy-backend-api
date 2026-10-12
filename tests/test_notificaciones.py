"""Tests de notificaciones.py: las reglas (funciones puras) y la generación completa con SQLite en memoria (solo usuarios Pro, sin duplicar)."""

from datetime import date, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import notificaciones as n
from app.database import BaseFinanciera, BaseNoFinanciera
from app.models_financiera import Cotizacion, FlujoFondo, Instrumento
from app.models_no_financiera import Favorito, Notificacion, PlanUsuario, Usuario

HOY = date(2026, 10, 12)


def test_vencimiento_avisa_a_los_7_y_a_los_3_dias_y_solo_con_el_umbral_mas_cercano():
    assert n.aviso_vencimiento("X", "Bono X", HOY + timedelta(days=10), HOY) is None  # falta mucho
    a7 = n.aviso_vencimiento("X", "Bono X", HOY + timedelta(days=6), HOY)
    assert a7 is not None and a7.clave.endswith(":7") and "6 días" in a7.titulo
    a3 = n.aviso_vencimiento("X", "Bono X", HOY + timedelta(days=2), HOY)
    assert a3 is not None and a3.clave.endswith(":3")  # un favorito recién agregado a 2 días recibe un solo aviso, el más cercano
    assert n.aviso_vencimiento("X", "Bono X", HOY, HOY).titulo == "X vence hoy"
    assert n.aviso_vencimiento("X", "Bono X", HOY - timedelta(days=1), HOY) is None  # ya venció


def test_pagos_dentro_de_la_ventana_y_sin_repetir_el_del_vencimiento():
    venc = HOY + timedelta(days=300)
    flujos = [(HOY + timedelta(days=5), "Cupón", 2.5), (HOY + timedelta(days=40), "Cupón", 2.5), (venc, "Amortización", 100.0)]
    avisos = n.avisos_pagos("X", "Bono X", "USD", venc, flujos, HOY)
    assert len(avisos) == 1 and avisos[0].fecha_evento == HOY + timedelta(days=5) and "2,50 USD" in avisos[0].mensaje
    # el pago que coincide con el vencimiento lo cubre el aviso de vencimiento
    assert n.avisos_pagos("X", "Bono X", "USD", HOY + timedelta(days=5), [(HOY + timedelta(days=5), "Cupón y amortización", 102.5)], HOY) == []


def test_variacion_diaria_con_signo_y_umbral():
    assert n.aviso_variacion("X", "Bono X", 2.9, HOY) is None
    assert n.aviso_variacion("X", "Bono X", None, HOY) is None
    assert "subió 3,5%" in n.aviso_variacion("X", "Bono X", 3.5, HOY).titulo
    assert "bajó 4,0%" in n.aviso_variacion("X", "Bono X", -4.0, HOY).titulo


def test_tir_ignora_ruido_de_instrumentos_por_vencer_y_valores_absurdos():
    assert n.aviso_tir("X", "Bono X", 12.0, 10.0, HOY, 400) is not None
    assert n.aviso_tir("X", "Bono X", 11.0, 10.0, HOY, 400) is None  # cambio chico
    assert n.aviso_tir("X", "LECAP", 24.0, 212.0, HOY, 400) is None  # TIR previa absurda
    assert n.aviso_tir("X", "LECAP", 12.0, 10.0, HOY, 20) is None  # a días del vencimiento


def test_rendimiento_contra_inflacion_y_score():
    assert n.aviso_rendimiento("X", "Bono X", 0.01, 0.018, HOY) is None  # dentro de ±2 pp
    assert "por debajo de la inflación" in n.aviso_rendimiento("X", "Bono X", -0.003, 0.018, HOY).titulo
    assert "le ganó a la inflación" in n.aviso_rendimiento("X", "Bono X", 0.05, 0.018, HOY).titulo
    assert n.aviso_score("X", "Bono X", 70, 65, HOY) is None
    assert "bajó 12 puntos" in n.aviso_score("X", "Bono X", 58, 70, HOY).titulo


# --- generación completa -----------------------------------------------------------------------------------------------------------------
def _sesion(base):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _escenario():
    fin, nofin = _sesion(BaseFinanciera), _sesion(BaseNoFinanciera)
    fin.add(Instrumento(ticker="AAA", nombre="Bono AAA", tipo="BONO", moneda="ARS", emisor="X", vencimiento=HOY + timedelta(days=5), riesgo="Bajo", liquidez="Alta", activo=True))
    fin.add(Cotizacion(instrumento_ticker="AAA", fecha=HOY, precio=100.0, variacion=4.0, volumen=1.0, operaciones=1))
    fin.add(FlujoFondo(instrumento_ticker="AAA", fecha=HOY + timedelta(days=2), tipo="Cupón", importe=3.0))
    pro = Usuario(nombre="P", apellido="P", email="pro@x.com", password_hash="x")
    casual = Usuario(nombre="C", apellido="C", email="casual@x.com", password_hash="x")
    nofin.add_all([pro, casual])
    nofin.commit()
    nofin.add_all([PlanUsuario(usuario_id=pro.id, plan="pro"), PlanUsuario(usuario_id=casual.id, plan="casual"), Favorito(usuario_id=pro.id, instrumento_ticker="AAA"), Favorito(usuario_id=casual.id, instrumento_ticker="AAA")])
    nofin.commit()
    fin.commit()
    return fin, nofin, pro, casual


def test_genera_solo_para_usuarios_pro_y_no_duplica_al_correr_dos_veces():
    fin, nofin, pro, casual = _escenario()
    primera = n.generar(fin, nofin, HOY)
    assert primera["vencimiento"] == 1 and primera["pago"] == 1 and primera["variacion"] == 1
    assert nofin.query(Notificacion).filter(Notificacion.usuario_id == pro.id).count() == 3
    assert nofin.query(Notificacion).filter(Notificacion.usuario_id == casual.id).count() == 0  # Casual no recibe notificaciones
    assert sum(n.generar(fin, nofin, HOY).values()) == 0  # idempotente
