"""RF-01 a RF-06: registro, login, edición de datos personales y perfil inversor."""

import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from sqlalchemy.orm import Session

from .. import codigos, email_templates, emailing
from ..config import GOOGLE_CLIENT_ID
from ..deps import es_admin, get_current_user, get_db_no_financiera
from ..models_no_financiera import PerfilInversorHistorial, PlazoInversionHistorial, Usuario
from ..schemas import (
    CambiarPasswordIn,
    EmailIn,
    MensajeOut,
    RestablecerPasswordIn,
    VerificarEmailIn,
    GoogleLogin,
    PerfilInversorUpdate,
    PlazoInversionUpdate,
    TokenOut,
    UsuarioLogin,
    UsuarioNombreUpdate,
    UsuarioOut,
    UsuarioRegistro,
)
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

# Detalle del 403 de /auth/login para una cuenta sin confirmar: el front lo reconoce por este valor.
EMAIL_NO_VERIFICADO = "EMAIL_NO_VERIFICADO"


def _perfil_vigente(usuario: Usuario) -> str:
    if not usuario.perfiles:
        return "moderado"
    return max(usuario.perfiles, key=lambda p: p.actualizado_en).perfil


def _plazo_vigente(usuario: Usuario) -> str:
    if not usuario.plazos:
        return "mediano"
    return max(usuario.plazos, key=lambda p: p.actualizado_en).plazo


def _perfil_configurado(usuario: Usuario) -> bool:
    """Registro y login con Google crean un perfil y un plazo por defecto (moderado / mediano), así que "tiene un
    perfil" no distingue a quien nunca eligió. Cada vez que el usuario guarda su perfil o su horizonte (el test o
    la selección manual) se AGREGA un registro al historial, aunque el valor sea el mismo: más de uno = ya lo
    configuró. No requiere ninguna columna nueva."""
    return len(usuario.perfiles) > 1 or len(usuario.plazos) > 1


def _mandar_codigo(tareas: BackgroundTasks, db: Session, usuario: Usuario, proposito: str) -> None:
    """Emite un código y lo manda por mail en segundo plano (el envío por SMTP tarda un segundo o dos y no tiene por
    qué demorar la respuesta). Si todavía rige la espera entre reenvíos, no hace nada."""
    codigo = codigos.emitir(db, usuario, proposito)
    if codigo is None:
        return
    armar = email_templates.mail_verificacion if proposito == codigos.VERIFICACION else email_templates.mail_reset
    asunto, html, texto = armar(usuario.nombre, codigo)
    tareas.add_task(emailing.enviar, usuario.email, asunto, html, texto)


def _avisar_cambio_de_password(tareas: BackgroundTasks, usuario: Usuario) -> None:
    asunto, html, texto = email_templates.mail_password_cambiada(usuario.nombre)
    tareas.add_task(emailing.enviar, usuario.email, asunto, html, texto)


def _to_out(usuario: Usuario) -> UsuarioOut:
    return UsuarioOut(
        id=usuario.id,
        nombre=usuario.nombre,
        apellido=usuario.apellido,
        email=usuario.email,
        perfilInversor=_perfil_vigente(usuario),
        plazoInversion=_plazo_vigente(usuario),
        esAdmin=es_admin(usuario),
        perfilConfigurado=_perfil_configurado(usuario),
    )


@router.post("/registro", response_model=UsuarioOut, status_code=status.HTTP_201_CREATED)
def registrar(datos: UsuarioRegistro, tareas: BackgroundTasks, db: Session = Depends(get_db_no_financiera)):
    """Crea la cuenta y manda por mail un código para confirmar el correo: hasta confirmarlo, la cuenta no puede iniciar
    sesión. Si ese correo ya tiene una cuenta SIN confirmar (alguien que no terminó el registro), se pisan sus datos con
    los nuevos y se manda un código nuevo, en vez de dejarla trabada con un 409."""
    existente = db.query(Usuario).filter(Usuario.email == datos.email).first()
    if existente is not None:
        if not codigos.pendiente(db, existente, codigos.VERIFICACION):
            raise HTTPException(status.HTTP_409_CONFLICT, "Ya existe una cuenta con ese correo electrónico")
        existente.nombre = datos.nombre
        existente.apellido = datos.apellido
        existente.password_hash = hash_password(datos.password)
        db.commit()
        _mandar_codigo(tareas, db, existente, codigos.VERIFICACION)
        return _to_out(existente)

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
    db.add(PlazoInversionHistorial(usuario_id=usuario.id, plazo="mediano"))
    db.commit()
    db.refresh(usuario)
    _mandar_codigo(tareas, db, usuario, codigos.VERIFICACION)
    return _to_out(usuario)


@router.post("/login", response_model=TokenOut)
def login(datos: UsuarioLogin, db: Session = Depends(get_db_no_financiera)):
    usuario = db.query(Usuario).filter(Usuario.email == datos.email).first()
    if usuario is None or not verify_password(datos.password, usuario.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Correo electrónico o contraseña inválidos")
    # La contraseña es correcta pero el correo todavía no se confirmó: el front pasa a la pantalla del código.
    if codigos.pendiente(db, usuario, codigos.VERIFICACION):
        raise HTTPException(status.HTTP_403_FORBIDDEN, EMAIL_NO_VERIFICADO)
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
            db.add(PlazoInversionHistorial(usuario_id=usuario.id, plazo="mediano"))
        db.commit()
        db.refresh(usuario)

    # Google ya verificó que ese correo es de quien inicia sesión: si la cuenta estaba esperando confirmación, queda confirmada.
    if payload.get("email_verified") and codigos.pendiente(db, usuario, codigos.VERIFICACION):
        codigos.descartar(db, usuario, codigos.VERIFICACION)

    return TokenOut(accessToken=create_access_token(usuario.email))


@router.post("/verificar-email", response_model=TokenOut)
def verificar_email(datos: VerificarEmailIn, db: Session = Depends(get_db_no_financiera)):
    """Confirma el correo con el código recibido y deja la sesión iniciada (el front no tiene que pedir la contraseña de nuevo)."""
    usuario = db.query(Usuario).filter(Usuario.email == datos.email).first()
    if usuario is None or not codigos.verificar(db, usuario, codigos.VERIFICACION, datos.codigo):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El código es incorrecto o venció. Pedí uno nuevo.")
    return TokenOut(accessToken=create_access_token(usuario.email))


@router.post("/reenviar-codigo", response_model=MensajeOut)
def reenviar_codigo(datos: EmailIn, tareas: BackgroundTasks, db: Session = Depends(get_db_no_financiera)):
    """Manda un código nuevo a una cuenta sin confirmar. Responde siempre lo mismo, exista o no la cuenta."""
    usuario = db.query(Usuario).filter(Usuario.email == datos.email).first()
    if usuario is not None and codigos.pendiente(db, usuario, codigos.VERIFICACION):
        _mandar_codigo(tareas, db, usuario, codigos.VERIFICACION)
    return MensajeOut(mensaje="Si hay una cuenta esperando confirmación con ese correo, te mandamos un código nuevo.")


@router.post("/olvide-password", response_model=MensajeOut)
def olvide_password(datos: EmailIn, tareas: BackgroundTasks, db: Session = Depends(get_db_no_financiera)):
    """Manda un código para restablecer la contraseña. Responde siempre lo mismo, exista o no la cuenta, para que nadie
    pueda usar esta pantalla para averiguar qué correos están registrados."""
    usuario = db.query(Usuario).filter(Usuario.email == datos.email).first()
    if usuario is not None:
        _mandar_codigo(tareas, db, usuario, codigos.RESET)
    return MensajeOut(mensaje="Si hay una cuenta con ese correo, te mandamos un código para restablecer la contraseña.")


@router.post("/restablecer-password", response_model=TokenOut)
def restablecer_password(datos: RestablecerPasswordIn, tareas: BackgroundTasks, db: Session = Depends(get_db_no_financiera)):
    """Cambia la contraseña con el código recibido por mail y deja la sesión iniciada. Como el código llegó al correo de la
    cuenta, también deja confirmado ese correo si estaba pendiente."""
    usuario = db.query(Usuario).filter(Usuario.email == datos.email).first()
    if usuario is None or not codigos.verificar(db, usuario, codigos.RESET, datos.codigo):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El código es incorrecto o venció. Pedí uno nuevo.")
    usuario.password_hash = hash_password(datos.password)
    db.commit()
    codigos.descartar(db, usuario, codigos.VERIFICACION)
    _avisar_cambio_de_password(tareas, usuario)
    return TokenOut(accessToken=create_access_token(usuario.email))


@router.put("/me/password", response_model=MensajeOut)
def cambiar_password(
    datos: CambiarPasswordIn,
    tareas: BackgroundTasks,
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db_no_financiera),
):
    """Cambio desde Perfil: pide la contraseña actual. (Una cuenta creada solo con Google no tiene una contraseña que
    conozca: para esa, el camino es «Olvidé mi contraseña», que llega al código por mail.)"""
    if not verify_password(datos.passwordActual, usuario.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "La contraseña actual no es correcta.")
    usuario.password_hash = hash_password(datos.passwordNueva)
    db.commit()
    _avisar_cambio_de_password(tareas, usuario)
    return MensajeOut(mensaje="Listo, cambiamos tu contraseña.")


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


@router.put("/me/plazo-inversion", response_model=UsuarioOut)
def actualizar_plazo_inversion(
    datos: PlazoInversionUpdate, usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db_no_financiera)
):
    db.add(PlazoInversionHistorial(usuario_id=usuario.id, plazo=datos.plazo))
    db.commit()
    db.refresh(usuario)
    return _to_out(usuario)
