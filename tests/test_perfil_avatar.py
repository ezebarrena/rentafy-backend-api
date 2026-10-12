"""Tests del nombre, apellido y avatar del perfil (límites de largo y PUT /auth/me/nombre), con SQLite en memoria."""

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import BaseNoFinanciera
from app.models_no_financiera import Usuario
from app.routers import auth
from app.schemas import UsuarioNombreUpdate, UsuarioRegistro


def _sesion():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    BaseNoFinanciera.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    u = Usuario(nombre="Facundo", apellido="Camilotto", email="a@x.com", password_hash="x")
    db.add(u)
    db.commit()
    return db, u


def _invalido(**campos):
    try:
        UsuarioNombreUpdate(**campos)
        return False
    except ValidationError:
        return True


def test_nombre_y_apellido_hasta_20_caracteres():
    assert not _invalido(nombre="a" * 20, apellido="b" * 20)
    assert _invalido(nombre="a" * 21, apellido="")
    assert _invalido(nombre="ok", apellido="b" * 21)
    assert _invalido(nombre="", apellido="")
    try:
        UsuarioRegistro(nombre="a" * 21, email="a@x.com", password="Abcdef12")
        assert False
    except ValidationError:
        pass


def test_avatar_se_guarda_se_cambia_y_se_borra():
    db, u = _sesion()
    assert auth._to_out(u).avatar is None
    r = auth.actualizar_nombre(UsuarioNombreUpdate(nombre="Facundo", apellido="Camilotto", avatar="  Fede_12  "), u, db)
    assert r.avatar == "Fede_12"
    assert auth.actualizar_nombre(UsuarioNombreUpdate(nombre="Facundo", apellido="Camilotto", avatar="Otro"), u, db).avatar == "Otro"
    assert auth.actualizar_nombre(UsuarioNombreUpdate(nombre="Facundo", apellido="Camilotto"), u, db).avatar == "Otro"  # sin el campo, no lo pisa
    assert auth.actualizar_nombre(UsuarioNombreUpdate(nombre="Facundo", apellido="Camilotto", avatar="   "), u, db).avatar is None  # vacío lo borra


def test_avatar_de_mas_de_10_caracteres_se_rechaza():
    db, u = _sesion()
    assert auth.actualizar_nombre(UsuarioNombreUpdate(nombre="F", avatar="a" * 10), u, db).avatar == "a" * 10
    try:
        auth.actualizar_nombre(UsuarioNombreUpdate(nombre="F", avatar="a" * 11), u, db)
        assert False, "tenía que rechazarlo"
    except HTTPException as e:
        assert e.status_code == 422
    assert auth._to_out(u).avatar == "a" * 10  # no quedó pisado


def _registro_invalido(**campos):
    base = dict(nombre="María José", apellido="O'Brien", email="a@x.com", password="Abcdef12")
    base.update(campos)
    try:
        UsuarioRegistro(**base)
        return False
    except ValidationError:
        return True


def test_registro_acepta_datos_normales():
    assert not _registro_invalido()
    assert not _registro_invalido(apellido="")
    assert not _registro_invalido(nombre="Jean-Luc", apellido="De la Fuente")


def test_registro_rechaza_nombres_raros():
    assert _registro_invalido(nombre="Fa2cundo")
    assert _registro_invalido(nombre="<script>")
    assert _registro_invalido(apellido="a@b.com")
    assert _registro_invalido(nombre="   ")
    assert _registro_invalido(nombre="___")
    assert UsuarioRegistro(nombre="  Ana   María ", email="a@x.com", password="Abcdef12").nombre == "Ana María"


def test_registro_limita_el_correo():
    assert _registro_invalido(email="no-es-un-mail")
    assert _registro_invalido(email="a" * 95 + "@x.com")


def test_password_nueva_exige_8_caracteres_mayuscula_minuscula_y_numero():
    assert _registro_invalido(password="Abc123")  # corta
    assert _registro_invalido(password="abcdefg1")  # sin mayúscula
    assert _registro_invalido(password="ABCDEFG1")  # sin minúscula
    assert _registro_invalido(password="Abcdefgh")  # sin número
    assert _registro_invalido(password="Abcdef1" + "a" * 70)  # más de 72
    assert not _registro_invalido(password="Abcdefg1")


def test_cambiar_y_restablecer_password_aplican_la_misma_regla():
    from app.schemas import CambiarPasswordIn, RestablecerPasswordIn

    for clase, campos in (
        (CambiarPasswordIn, dict(passwordActual="x", passwordNueva="abcdefg1")),
        (RestablecerPasswordIn, dict(email="a@x.com", codigo="123456", password="abcdefg1")),
    ):
        try:
            clase(**campos)
            assert False
        except ValidationError:
            pass


def test_avatar_rechaza_simbolos_y_caracteres_de_control():
    db, u = _sesion()
    for malo in ("<b>hi</b>", "a&b", 'a"b', "a\nb"):
        try:
            auth.actualizar_nombre(UsuarioNombreUpdate(nombre="Facundo", apellido="Camilotto", avatar=malo), u, db)
            assert False, malo
        except HTTPException as e:
            assert e.status_code == 422
