"""Tests de los planes Casual/Pro (planes.py, deps.require_pro, PUT /auth/me/plan y el tope de favoritos), con SQLite en memoria."""

from datetime import date, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import deps, planes
from app.database import BaseFinanciera, BaseNoFinanciera
from app.models_financiera import Instrumento
from app.models_no_financiera import Favorito, PlanUsuario, Usuario
from app.planes import LIMITE_FAVORITOS, plan_de
from app.routers import auth, watchlist
from app.schemas import PlanUpdate


def _sesion(base):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _usuario(db, email="a@x.com", plan="casual", favoritos=0):
    u = Usuario(nombre="A", apellido="B", email=email, password_hash="x")
    db.add(u)
    db.commit()
    if plan:
        db.add(PlanUsuario(usuario_id=u.id, plan=plan))
    base = datetime(2026, 1, 1)
    for i in range(favoritos):
        db.add(Favorito(usuario_id=u.id, instrumento_ticker=f"T{i:02d}", creado_en=base + timedelta(minutes=i)))
    db.commit()
    db.refresh(u)
    return u


def _financiera(tickers):
    db = _sesion(BaseFinanciera)
    for t in tickers:
        db.add(Instrumento(ticker=t, nombre=t, tipo="LECAP", moneda="ARS", emisor="X", vencimiento=date.today() + timedelta(days=100), riesgo="Bajo", liquidez="Alta", activo=True))
    db.commit()
    return db


def _con_admins(emails, cuerpo):
    original = deps.ADMIN_EMAILS
    deps.ADMIN_EMAILS = set(emails)
    try:
        cuerpo()
    finally:
        deps.ADMIN_EMAILS = original


def test_sin_fila_el_plan_es_el_por_defecto_de_la_configuracion_tambien_para_los_admin():
    db = _sesion(BaseNoFinanciera)
    sin_fila = _usuario(db, "a@x.com", plan=None)
    pro, casual = _usuario(db, "b@x.com", plan="pro"), _usuario(db, "d@x.com", plan="casual")
    admin = _usuario(db, "c@x.com", plan=None)
    assert plan_de(pro) == "pro" and plan_de(casual) == "casual"
    original = planes.PLAN_POR_DEFECTO
    try:
        for defecto in ("pro", "casual"):
            planes.PLAN_POR_DEFECTO = defecto
            assert plan_de(sin_fila) == defecto and plan_de(admin) == defecto  # los admin no tienen trato especial
    finally:
        planes.PLAN_POR_DEFECTO = original

    def cuerpo():  # un admin puede cambiar de plan como cualquiera
        auth.actualizar_plan(PlanUpdate(plan="casual"), admin, db)
        assert plan_de(admin) == "casual"
        try:
            deps.require_pro(admin)
            assert False, "un admin Casual tampoco entra a las funciones Pro"
        except HTTPException as e:
            assert e.status_code == 403
        auth.actualizar_plan(PlanUpdate(plan="pro"), admin, db)
        assert plan_de(admin) == "pro" and deps.require_pro(admin) is admin

    _con_admins({"c@x.com"}, cuerpo)


def test_require_pro_deja_pasar_a_pro_y_rechaza_a_casual_con_el_codigo_del_front():
    db = _sesion(BaseNoFinanciera)
    casual, pro = _usuario(db, "a@x.com"), _usuario(db, "b@x.com", plan="pro")
    assert deps.require_pro(pro) is pro
    try:
        deps.require_pro(casual)
        assert False, "tenía que rechazar a un Casual"
    except HTTPException as e:
        assert e.status_code == 403 and e.detail == deps.PLAN_PRO_REQUERIDO


def test_pasar_a_pro_y_volver_a_casual_recorta_a_los_5_favoritos_mas_antiguos():
    db = _sesion(BaseNoFinanciera)
    u = _usuario(db, favoritos=8, plan=None)
    r = auth.actualizar_plan(PlanUpdate(plan="pro"), u, db)
    assert r.plan == "pro" and r.favoritosQuitados == 0 and db.query(Favorito).count() == 8
    r = auth.actualizar_plan(PlanUpdate(plan="casual"), u, db)
    assert r.plan == "casual" and r.favoritosQuitados == 3
    assert sorted(f.instrumento_ticker for f in db.query(Favorito)) == ["T00", "T01", "T02", "T03", "T04"]


def test_el_switch_se_puede_apagar():
    db = _sesion(BaseNoFinanciera)
    u = _usuario(db)
    original = auth.PLAN_SWITCH_HABILITADO
    auth.PLAN_SWITCH_HABILITADO = False
    try:
        auth.actualizar_plan(PlanUpdate(plan="pro"), u, db)
        assert False, "tenía que rechazarlo"
    except HTTPException as e:
        assert e.status_code == 403
    finally:
        auth.PLAN_SWITCH_HABILITADO = original
    assert plan_de(u) == "casual"


def test_tope_de_favoritos_por_plan():
    tickers = [f"T{i:02d}" for i in range(40)]
    fin = _financiera(tickers + ["NUEVO"])
    db = _sesion(BaseNoFinanciera)
    casual = _usuario(db, "a@x.com", favoritos=LIMITE_FAVORITOS["casual"])
    pro = _usuario(db, "b@x.com", plan="pro", favoritos=LIMITE_FAVORITOS["casual"])
    pro_lleno = _usuario(db, "c@x.com", plan="pro", favoritos=LIMITE_FAVORITOS["pro"])

    def intentar(usuario, ticker="NUEVO"):
        try:
            watchlist.agregar_a_watchlist(ticker, usuario, fin, db)
            return None
        except HTTPException as e:
            return e

    e = intentar(casual)
    assert e is not None and e.status_code == 409 and "5" in e.detail and "Pro" in e.detail
    assert intentar(pro) is None  # un Pro con 5 todavía puede sumar
    e = intentar(pro_lleno)
    assert e is not None and e.status_code == 409 and "30" in e.detail
    assert intentar(casual, "T00") is None  # volver a agregar uno que ya tenía es idempotente aunque esté en el tope
