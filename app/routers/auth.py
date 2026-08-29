"""RF-01 a RF-06: registro, login, edición de datos personales y perfil inversor."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..deps import get_current_user, get_db_no_financiera
from ..models_no_financiera import PerfilInversorHistorial, Usuario
from ..schemas import PerfilInversorUpdate, TokenOut, UsuarioLogin, UsuarioOut, UsuarioRegistro
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


@router.get("/me", response_model=UsuarioOut)
def me(usuario: Usuario = Depends(get_current_user)):
    return _to_out(usuario)


@router.put("/me/perfil-inversor", response_model=UsuarioOut)
def actualizar_perfil_inversor(
    datos: PerfilInversorUpdate, usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db_no_financiera)
):
    db.add(PerfilInversorHistorial(usuario_id=usuario.id, perfil=datos.perfil))
    db.commit()
    db.refresh(usuario)
    return _to_out(usuario)
