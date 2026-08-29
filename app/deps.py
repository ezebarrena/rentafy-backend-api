from typing import Generator

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import Usuario
from .security import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(token: str | None = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> Usuario:
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
