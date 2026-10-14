"""RF-01 a RF-06: registro, login, edición de datos personales y perfil inversor."""

import secrets
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from sqlalchemy.orm import Session

from .. import codigos, email_templates, emailing
from ..config import GOOGLE_CLIENT_ID, PLAN_SWITCH_HABILITADO, VERSION_TERMINOS
from ..deps import es_admin, get_current_user, get_db_no_financiera
from ..models_no_financiera import (
    AvatarUsuario,
    CodigoEmail,
    ConsentimientoUsuario,
    Notificacion,
    PerfilInversorHistorial,
    PlanUsuario,
    PlazoInversionHistorial,
    Usuario,
)
from ..planes import LIMITE_FAVORITOS, PLAN_CASUAL, plan_de
from ..schemas import (
    CONFIRMACION_ELIMINAR_CUENTA,
    CambiarPasswordIn,
    EliminarCuentaIn,
    EmailIn,
    MensajeOut,
    RestablecerPasswordIn,
    VerificarEmailIn,
    GoogleLogin,
    PerfilInversorUpdate,
    MAX_AVATAR,
    MAX_NOMBRE,
    PlanCambiadoOut,
    PlanUpdate,
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
        plan=plan_de(usuario),
        avatar=usuario.avatar_registro.avatar if usuario.avatar_registro is not None else None,
        perfilConfigurado=_perfil_configurado(usuario),
    )


# Detalle del 400 de /auth/google cuando se intenta crear una cuenta nueva sin haber aceptado los términos: el front lo reconoce por este valor.
CONSENTIMIENTO_REQUERIDO = "CONSENTIMIENTO_REQUERIDO"


def _registrar_consentimiento(db: Session, usuario: Usuario) -> None:
    """Deja asentado que el usuario aceptó la versión vigente de la Política de Privacidad y los Términos (y cuándo)."""
    if usuario.consentimiento is None:
        db.add(ConsentimientoUsuario(usuario_id=usuario.id, version=VERSION_TERMINOS, aceptado_en=datetime.utcnow()))
    else:
        usuario.consentimiento.version = VERSION_TERMINOS
        usuario.consentimiento.aceptado_en = datetime.utcnow()


@router.post("/registro", response_model=UsuarioOut, status_code=status.HTTP_201_CREATED)
def registrar(datos: UsuarioRegistro, tareas: BackgroundTasks, db: Session = Depends(get_db_no_financiera)):
    """Crea la cuenta y manda por mail un código para confirmar el correo: hasta confirmarlo, la cuenta no puede iniciar
    sesión. Si ese correo ya tiene una cuenta SIN confirmar (alguien que no terminó el registro), se pisan sus datos con
    los nuevos y se manda un código nuevo, en vez de dejarla trabada con un 409."""
    if not datos.aceptaTerminos:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Para crear tu cuenta tenés que aceptar la Política de Privacidad y los Términos.")
    existente = db.query(Usuario).filter(Usuario.email == datos.email).first()
    if existente is not None:
        if not codigos.pendiente(db, existente, codigos.VERIFICACION):
            raise HTTPException(status.HTTP_409_CONFLICT, "Ya existe una cuenta con ese correo electrónico")
        existente.nombre = datos.nombre
        existente.apellido = datos.apellido
        existente.password_hash = hash_password(datos.password)
        _registrar_consentimiento(db, existente)
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
    _registrar_consentimiento(db, usuario)
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
            if not datos.aceptaTerminos:  # cuenta nueva: hace falta que haya aceptado la Política de Privacidad y los Términos
                raise HTTPException(status.HTTP_400_BAD_REQUEST, CONSENTIMIENTO_REQUERIDO)
            usuario = Usuario(
                nombre=payload.get("given_name", "")[:MAX_NOMBRE],  # Google no respeta nuestro tope de 20 caracteres
                apellido=payload.get("family_name", "")[:MAX_NOMBRE],
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
            _registrar_consentimiento(db, usuario)
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


@router.delete("/me", response_model=MensajeOut)
def eliminar_cuenta(
    datos: EliminarCuentaIn,
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db_no_financiera),
):
    """Elimina la cuenta y TODOS los datos del usuario (Ley 25.326, derecho de supresión): perfil, historial de perfil y horizonte, favoritos, plan, avatar, notificaciones
    y códigos pendientes. Rentafy no conserva nada de eso. Pide escribir la palabra de confirmación para que no se dispare por un clic o una llamada suelta; el token
    queda inválido porque el usuario ya no existe."""
    if datos.confirmacion.strip().upper() != CONFIRMACION_ELIMINAR_CUENTA:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Para eliminar tu cuenta escribí {CONFIRMACION_ELIMINAR_CUENTA}.")
    # Las tablas con ON DELETE CASCADE las borraría la base, pero se borran explícitamente para no depender de que el motor lo aplique (SQLite, por ejemplo, no lo hace sin pedirlo).
    db.query(Notificacion).filter(Notificacion.usuario_id == usuario.id).delete(synchronize_session=False)
    db.query(CodigoEmail).filter(CodigoEmail.usuario_id == usuario.id).delete(synchronize_session=False)
    db.query(ConsentimientoUsuario).filter(ConsentimientoUsuario.usuario_id == usuario.id).delete(synchronize_session=False)
    db.delete(usuario)  # perfiles, plazos, favoritos, plan y avatar salen por el cascade del modelo
    db.commit()
    return MensajeOut(mensaje="Eliminamos tu cuenta y todos tus datos.")


@router.get("/me", response_model=UsuarioOut)
def me(usuario: Usuario = Depends(get_current_user)):
    return _to_out(usuario)


@router.put("/me/nombre", response_model=UsuarioOut)
def actualizar_nombre(
    datos: UsuarioNombreUpdate, usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db_no_financiera)
):
    """Editar nombre/apellido (RF-04) y el avatar (nombre imaginario) — el email no es editable acá: identifica la cuenta y
    está atado al login, cambiarlo requeriría reverificarlo."""
    usuario.nombre = datos.nombre
    usuario.apellido = datos.apellido
    if "avatar" in datos.model_fields_set:  # un cliente que no lo manda no lo pisa
        avatar = (datos.avatar or "").strip()
        if len(avatar) > MAX_AVATAR:
            raise HTTPException(422, f"El avatar puede tener hasta {MAX_AVATAR} caracteres")
        if any(c in "<>&\"" or not c.isprintable() for c in avatar):
            raise HTTPException(422, "El avatar no puede tener símbolos como < > & \" ni caracteres de control")
        registro = usuario.avatar_registro
        if not avatar:
            if registro is not None:
                db.delete(registro)
        elif registro is None:
            db.add(AvatarUsuario(usuario_id=usuario.id, avatar=avatar))
        else:
            registro.avatar = avatar
            registro.actualizado_en = datetime.utcnow()
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


@router.put("/me/plan", response_model=PlanCambiadoOut)
def actualizar_plan(
    datos: PlanUpdate, usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db_no_financiera)
):
    """Switch de prueba Casual/Pro (todavía no hay cobro; se apaga con PLAN_SWITCH_HABILITADO=false). Al pasar a Casual, los favoritos que superen
    el tope del plan se borran: se conservan los más antiguos. Vale para todos, también para los admin (tienen plan como cualquiera y pueden cambiarlo)."""
    if not PLAN_SWITCH_HABILITADO:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "El cambio de plan no está habilitado")
    registro = usuario.plan_registro
    if registro is None:
        db.add(PlanUsuario(usuario_id=usuario.id, plan=datos.plan))
    else:
        registro.plan = datos.plan
        registro.actualizado_en = datetime.utcnow()
    quitados = 0
    if datos.plan == PLAN_CASUAL:
        ordenados = sorted(usuario.favoritos, key=lambda f: (f.creado_en, f.id))
        for sobrante in ordenados[LIMITE_FAVORITOS[PLAN_CASUAL]:]:
            db.delete(sobrante)
            quitados += 1
    db.commit()
    db.refresh(usuario)
    return PlanCambiadoOut(**_to_out(usuario).model_dump(), favoritosQuitados=quitados)


@router.put("/me/plazo-inversion", response_model=UsuarioOut)
def actualizar_plazo_inversion(
    datos: PlazoInversionUpdate, usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db_no_financiera)
):
    db.add(PlazoInversionHistorial(usuario_id=usuario.id, plazo=datos.plazo))
    db.commit()
    db.refresh(usuario)
    return _to_out(usuario)
