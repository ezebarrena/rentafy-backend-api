from typing import Generator

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from .config import ADMIN_EMAILS
from .database import SessionFinanciera, SessionNoFinanciera
from .models_no_financiera import Usuario
from .security import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)


def get_db_financiera() -> Generator[Session, None, None]:
    db = SessionFinanciera()
    try:
        yield db
    finally:
        db.close()


def get_db_no_financiera() -> Generator[Session, None, None]:
    db = SessionNoFinanciera()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    token: str | None = Depends(oauth2_scheme), db: Session = Depends(get_db_no_financiera)
) -> Usuario:
    credenciales_invalidas = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Credenciales inválidas o sesión expirada",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if token is None:
        raise credenciales_invalidas
    email = decode_access_token(token)
    if email is None:
        raise credenciales_invalidas
    usuario = db.query(Usuario).filter(Usuario.email == email).first()
    if usuario is None:
        raise credenciales_invalidas
    return usuario


def es_admin(usuario: Usuario) -> bool:
    return usuario.email.strip().lower() in ADMIN_EMAILS


def require_admin(usuario: Usuario = Depends(get_current_user)) -> Usuario:
    """Solo admins (ver ADMIN_EMAILS en config.py). 401 si no hay sesión, 403 si hay sesión pero
    no es admin: ocultar el menú en el front no alcanza, estos endpoints son de testeo interno."""
    if not es_admin(usuario):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Requiere rol de administrador")
    return usuario


def solicitante_es_admin(
    token: str | None = Depends(oauth2_scheme), db: Session = Depends(get_db_no_financiera)
) -> bool:
    """True solo si la request trae la sesión válida de un admin. A diferencia de require_admin, NUNCA levanta:
    sirve para endpoints públicos que muestran algo más (o algo distinto) a un admin sin pedirle login al resto."""
    if token is None:
        return False
    email = decode_access_token(token)
    if email is None:
        return False
    usuario = db.query(Usuario).filter(Usuario.email == email).first()
    return usuario is not None and es_admin(usuario)
