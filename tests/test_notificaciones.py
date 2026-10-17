"""Tests de notificaciones.py: las reglas (funciones puras) y la generación completa con SQLite en memoria (solo usuarios Pro, sin duplicar)."""

from datetime import date, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import notificaciones as n
from app.database import BaseFinanciera, BaseNoFinanciera
from app.models_financiera import Cotizacion, FlujoFondo, Instrumento, Scoring, SerieReferencia
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


def test_destacado_exige_rendimiento_sobre_la_inflacion_y_buen_score():
    assert n.aviso_destacado("X", "Bono X", 0.05, 0.016, 80, HOY).tipo == "destacado"
    assert n.aviso_destacado("X", "Bono X", 0.04, 0.016, 80, HOY) is None  # no le gana por 3 puntos
    assert n.aviso_destacado("X", "Bono X", 0.05, 0.016, 60, HOY) is None  # rinde bien pero el Score es bajo
    assert n.aviso_destacado("X", "Bono X", None, 0.016, 80, HOY) is None
    a = n.aviso_destacado("X", "Bono X", 0.06, 0.016, 85, HOY)
    assert "6,0%" in a.titulo and "No está en tus favoritos" in a.mensaje and a.clave == "dest:X:2026-10"


def _escenario_destacados():
    """AAA es favorito del usuario Pro; BBB y CCC no. Las dos subieron 6% en el mes (la inflación fue 1,6%), pero solo BBB tiene un Score alto."""
    fin, nofin, pro, casual = _escenario()
    for ticker, score in (("BBB", 90.0), ("CCC", 30.0)):
        fin.add(Instrumento(ticker=ticker, nombre=f"Bono {ticker}", tipo="BONO", moneda="ARS", emisor="X", vencimiento=HOY + timedelta(days=400), riesgo="Bajo", liquidez="Alta", activo=True))
        for k in range(31):
            fin.add(Cotizacion(instrumento_ticker=ticker, fecha=HOY - timedelta(days=30 - k), precio=100.0 + 0.2 * k, variacion=0.2, volumen=1.0, operaciones=1))
        fin.add(Scoring(instrumento_ticker=ticker, modelo_id="m", fecha_calculo=HOY, rendimiento=score, riesgo=score, liquidez=score, estabilidad=score))
    fin.add(SerieReferencia(nombre="uva", fecha=HOY - timedelta(days=35), valor=100.0))
    fin.add(SerieReferencia(nombre="uva", fecha=HOY, valor=101.6))
    fin.commit()
    return fin, nofin, pro, casual


def test_destacados_son_de_instrumentos_fuera_de_favoritos_y_con_buen_score_y_no_se_repiten():
    fin, nofin, pro, casual = _escenario_destacados()
    creadas = n.generar(fin, nofin, HOY)
    assert creadas["destacado"] == 1
    destacadas = nofin.query(Notificacion).filter(Notificacion.tipo == "destacado").all()
    assert [d.ticker for d in destacadas] == ["BBB"] and destacadas[0].usuario_id == pro.id  # CCC tiene Score bajo; AAA es favorito; Casual no recibe
    assert sum(n.generar(fin, nofin, HOY).values()) == 0  # una vez por instrumento y mes
    # si BBB pasa a favoritos deja de ser candidato (y su aviso viejo se sigue viendo)
    nofin.add(Favorito(usuario_id=pro.id, instrumento_ticker="BBB"))
    nofin.commit()
    assert n.generar(fin, nofin, HOY + timedelta(days=40))["destacado"] == 0


def test_un_usuario_pro_sin_favoritos_igual_recibe_destacados():
    fin, nofin, pro, casual = _escenario_destacados()
    nofin.query(Favorito).filter(Favorito.usuario_id == pro.id).delete()
    nofin.commit()
    assert n.generar(fin, nofin, HOY)["destacado"] == 1


def test_los_avisos_de_un_instrumento_dejan_de_verse_si_sale_de_favoritos_pero_los_destacados_se_mantienen():
    from app.routers import notificaciones as router

    fin, nofin, pro, casual = _escenario_destacados()
    n.generar(fin, nofin, HOY)
    visibles = lambda: sorted((x.tipo, x.ticker) for x in router._visibles(nofin, pro))
    assert ("vencimiento", "AAA") in visibles() and ("destacado", "BBB") in visibles()
    nofin.query(Favorito).filter(Favorito.usuario_id == pro.id).delete()
    nofin.commit()
    nofin.refresh(pro)
    assert visibles() == [("destacado", "BBB")]  # los avisos de AAA ya no se ven ni cuentan como no leídos
    assert router._no_leidas(nofin, pro) == 1


def test_un_aviso_solo_se_ve_24_horas_y_las_filas_muy_viejas_se_borran_al_generar():
    from app.routers import notificaciones as router

    fin, nofin, pro, casual = _escenario_destacados()
    n.generar(fin, nofin, HOY)
    ahora = datetime.utcnow()
    todas = nofin.query(Notificacion).filter(Notificacion.usuario_id == pro.id).all()
    assert len(todas) >= 2
    # Recién generados: se ven.
    assert router._visibles(nofin, pro).count() == len(todas)
    # A las 23 h siguen visibles; a las 25 h dejan de verse (pero la fila sigue, para no regenerar el mismo evento).
    for x in todas:
        x.creado_en = ahora - timedelta(hours=23)
    nofin.commit()
    assert router._visibles(nofin, pro).count() == len(todas)
    for x in todas:
        x.creado_en = ahora - timedelta(hours=25)
    nofin.commit()
    assert router._visibles(nofin, pro).count() == 0
    assert nofin.query(Notificacion).filter(Notificacion.usuario_id == pro.id).count() == len(todas)
    # Pasados los días de conservación, la próxima corrida las borra de la base.
    for x in todas:
        x.creado_en = ahora - timedelta(days=n.DIAS_CONSERVACION + 1)
    nofin.commit()
    n.generar(fin, nofin, HOY)
    assert nofin.query(Notificacion).filter(Notificacion.usuario_id == pro.id, Notificacion.creado_en < ahora - timedelta(days=n.DIAS_CONSERVACION)).count() == 0
