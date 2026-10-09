"""Tests de los códigos por mail y de las plantillas, con una base SQLite en memoria (no tocan la base real)."""

from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import codigos, email_templates, emailing
from app.models_no_financiera import CodigoEmail, Usuario


def _db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Usuario.__table__.create(engine)
    CodigoEmail.__table__.create(engine)
    db = sessionmaker(bind=engine)()
    u = Usuario(nombre="Facundo", apellido="C", email="f@example.com", password_hash="x")
    db.add(u)
    db.commit()
    return db, u


T0 = datetime(2026, 10, 8, 12, 0, 0)


def test_codigo_correcto_sirve_una_sola_vez():
    db, u = _db()
    c = codigos.emitir(db, u, codigos.VERIFICACION, T0)
    assert len(c) == 6 and c.isdigit()
    assert codigos.pendiente(db, u, codigos.VERIFICACION)
    assert codigos.verificar(db, u, codigos.VERIFICACION, c, T0 + timedelta(minutes=1))
    assert not codigos.pendiente(db, u, codigos.VERIFICACION)  # se consumió
    assert not codigos.verificar(db, u, codigos.VERIFICACION, c, T0 + timedelta(minutes=2))  # no sirve de nuevo


def test_el_codigo_no_se_guarda_en_claro_y_se_acepta_con_espacios():
    db, u = _db()
    c = codigos.emitir(db, u, codigos.VERIFICACION, T0)
    assert c not in db.query(CodigoEmail).one().codigo_hash
    assert codigos.verificar(db, u, codigos.VERIFICACION, f" {c[:3]} {c[3:]} ", T0)


def test_un_codigo_de_verificacion_no_sirve_para_restablecer_la_contrasena():
    db, u = _db()
    c = codigos.emitir(db, u, codigos.VERIFICACION, T0)
    assert not codigos.verificar(db, u, codigos.RESET, c, T0)  # no hay código de reset emitido
    r = codigos.emitir(db, u, codigos.RESET, T0)
    assert not codigos.verificar(db, u, codigos.RESET, c if c != r else "000000", T0)


def test_vence_a_los_15_minutos():
    db, u = _db()
    c = codigos.emitir(db, u, codigos.VERIFICACION, T0)
    assert not codigos.verificar(db, u, codigos.VERIFICACION, c, T0 + codigos.VIGENCIA + timedelta(seconds=1))
    assert not codigos.pendiente(db, u, codigos.VERIFICACION)  # y se descarta


def test_cinco_intentos_fallidos_descartan_el_codigo_aunque_despues_se_acierte():
    db, u = _db()
    c = codigos.emitir(db, u, codigos.VERIFICACION, T0)
    mal = "000000" if c != "000000" else "111111"
    for _ in range(codigos.MAX_INTENTOS):
        assert not codigos.verificar(db, u, codigos.VERIFICACION, mal, T0)
    assert not codigos.verificar(db, u, codigos.VERIFICACION, c, T0)  # ya no sirve ni el correcto


def test_espera_entre_reenvios_y_el_codigo_nuevo_reemplaza_al_anterior():
    db, u = _db()
    primero = codigos.emitir(db, u, codigos.VERIFICACION, T0)
    assert codigos.emitir(db, u, codigos.VERIFICACION, T0 + timedelta(seconds=30)) is None  # muy pronto
    segundo = codigos.emitir(db, u, codigos.VERIFICACION, T0 + codigos.ESPERA_REENVIO)
    assert segundo is not None
    if primero != segundo:
        assert not codigos.verificar(db, u, codigos.VERIFICACION, primero, T0 + codigos.ESPERA_REENVIO)
    assert db.query(CodigoEmail).count() == 1  # una fila por usuario y propósito


def test_los_mails_traen_el_codigo_el_logo_y_el_nombre_escapado():
    for armar in (email_templates.mail_verificacion, email_templates.mail_reset):
        asunto, html, texto = armar("<b>Ana</b>", "123456")
        assert "123456" in asunto and "123456" in texto
        assert "1 2 3 4 5 6" in html and f"cid:{email_templates.CID_LOGO}" in html
        assert "&lt;b&gt;Ana" in html and "<b>Ana</b>" not in html  # el nombre no puede inyectar HTML
        assert "15 minutos" in html and "15 minutos" in texto
    asunto, html, texto = email_templates.mail_password_cambiada("Ana")
    assert "contraseña" in asunto and "Abrir Rentafy" in html


def test_el_mensaje_tiene_texto_html_y_el_logo_incrustado():
    asunto, html, texto = email_templates.mail_verificacion("Ana", "654321")
    msg = emailing.construir_mensaje("ana@example.com", asunto, html, texto)
    tipos = [p.get_content_type() for p in msg.walk()]
    assert "text/plain" in tipos and "text/html" in tipos and "image/png" in tipos
    assert msg["To"] == "ana@example.com" and "Rentafy" in msg["From"]


def test_sin_smtp_no_envia_ni_levanta():
    emailing.SMTP_HOST = ""  # aunque el .env local tenga SMTP, este test no debe mandar un mail de verdad
    assert emailing.enviar("ana@example.com", "asunto", "<p>hola</p>", "hola") is False
