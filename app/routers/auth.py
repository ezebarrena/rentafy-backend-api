"""RF-01 a RF-06: registro, login, edición de datos personales y perfil inversor."""

import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from sqlalchemy.orm import Session

from ..config import GOOGLE_CLIENT_ID
from ..deps import get_current_user, get_db_no_financiera
from ..models_no_financiera import PerfilInversorHistorial, Usuario
from ..schemas import (
    GoogleLogin,
    PerfilInversorUpdate,
    TokenOut,
    UsuarioLogin,
    UsuarioNombreUpdate,
    UsuarioOut,
    UsuarioRegistro,
)
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def _perfil_vigente(usuario: Usuario) -> str:
    if not usuario.perfiles:
        return "moderado"
    return max(usuario.perfiles, key=lambda p: p.actualizado_en).perfil


def _to_out(usuario: Usuario) -> UsuarioOut:
    return UsuarioOut(
        id=usuario.id,
        nombre=usuario.nombre,
        apellido=usuario.apellido,
        email=usuario.email,
        perfilInversor=_perfil_vigente(usuario),
    )


@router.post("/registro", response_model=UsuarioOut, status_code=status.HTTP_201_CREATED)
def registrar(datos: UsuarioRegistro, db: Session = Depends(get_db_no_financiera)):
    if db.query(Usuario).filter(Usuario.email == datos.email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya existe una cuenta con ese correo electrónico")

    usuario = Usuario(
        nombre=datos.nombre,
        apellido=datos.apellido,
        email=datos.email,
        password_hash=hash_password(datos.password),
    )
    db.add(usuario)
    db.commit()
    db.refresh(usuario)

    db.add(PerfilInversorHistorial(usuario_id=usuario.id, perfil="moderado"))
    db.commit()
    db.refresh(usuario)
    return _to_out(usuario)


@router.post("/login", response_model=TokenOut)
def login(datos: UsuarioLogin, db: Session = Depends(get_db_no_financiera)):
    usuario = db.query(Usuario).filter(Usuario.email == datos.email).first()
    if usuario is None or not verify_password(datos.password, usuario.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Correo electrónico o contraseña inválidos")
    return TokenOut(accessToken=create_access_token(usuario.email))


@router.post("/google", response_model=TokenOut)
def login_google(datos: GoogleLogin, db: Session = Depends(get_db_no_financiera)):
    """Login/registro con Google (RF-01/RF-02 vía SSO): el frontend manda el ID token que
    entrega Google Identity Services, acá se valida su firma contra la API de Google antes de
    confiar en el email/nombre que trae adentro."""
    try:
        payload = google_id_token.verify_oauth2_token(datos.idToken, google_requests.Request(), GOOGLE_CLIENT_ID)
    except ValueError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token de Google inválido") from exc

    usuario = db.query(Usuario).filter(Usuario.sso_id == payload["sub"]).first()
    if usuario is None:
        usuario = db.query(Usuario).filter(Usuario.email == payload["email"]).first()
        if usuario is not None:
            usuario.sso_id = payload["sub"]  # cuenta ya existía por email/password: se vincula con Google
        else:
            usuario = Usuario(
                nombre=payload.get("given_name", ""),
                apellido=payload.get("family_name", ""),
                email=payload["email"],
                # password_hash es NOT NULL pero esta cuenta solo loguea vía Google; el hash de
                # un valor al azar la deja simplemente inutilizable para /auth/login.
                password_hash=hash_password(secrets.token_urlsafe(32)),
                sso_id=payload["sub"],
            )
            db.add(usuario)
            db.commit()
            db.refresh(usuario)
            db.add(PerfilInversorHistorial(usuario_id=usuario.id, perfil="moderado"))
        db.commit()
        db.refresh(usuario)

    return TokenOut(accessToken=create_access_token(usuario.email))


@router.get("/me", response_model=UsuarioOut)
def me(usuario: Usuario = Depends(get_current_user)):
    return _to_out(usuario)


@router.put("/me/nombre", response_model=UsuarioOut)
def actualizar_nombre(
    datos: UsuarioNombreUpdate, usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db_no_financiera)
):
    """Editar nombre/apellido (RF-04) — el email no es editable acá: identifica la cuenta y
    está atado al login, cambiarlo requeriría reverificarlo."""
    usuario.nombre = datos.nombre
    usuario.apellido = datos.apellido
    db.commit()
    db.refresh(usuario)
    return _to_out(usuario)


@router.put("/me/perfil-inversor", response_model=UsuarioOut)
def actualizar_perfil_inversor(
    datos: PerfilInversorUpdate, usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db_no_financiera)
):
    db.add(PerfilInversorHistorial(usuario_id=usuario.id, perfil=datos.perfil))
    db.commit()
    db.refresh(usuario)
    return _to_out(usuario)
