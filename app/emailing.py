"""Envío de mails por SMTP (ver SMTP_* en config.py). Nunca levanta: un mail que no sale no puede tumbar el registro ni la
recuperación de contraseña; se registra el error y el usuario puede pedir el código de nuevo.

Sin SMTP_HOST, SMTP_USER y SMTP_PASSWORD (desarrollo) no se envía nada: el mail completo, con su código, se escribe en el log del backend."""

import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

from .config import EMAIL_REMITENTE, EMAIL_REMITENTE_NOMBRE, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USER
from .email_templates import CID_LOGO

logger = logging.getLogger("rentafy.emailing")
_LOGO = Path(__file__).parent / "assets" / "logo-rentafy-mail.png"
TIMEOUT_SEGUNDOS = 15


def construir_mensaje(destinatario: str, asunto: str, html: str, texto: str) -> EmailMessage:
    """Mensaje multipart: texto plano + HTML con el logo incrustado (cid)."""
    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = formataddr((EMAIL_REMITENTE_NOMBRE, EMAIL_REMITENTE or "no-reply@rentafy.local"))
    msg["To"] = destinatario
    msg.set_content(texto)
    msg.add_alternative(html, subtype="html")
    if _LOGO.exists():
        msg.get_payload()[1].add_related(_LOGO.read_bytes(), maintype="image", subtype="png", cid=f"<{CID_LOGO}>")
    return msg


def enviar(destinatario: str, asunto: str, html: str, texto: str) -> bool:
    """True si el servidor SMTP aceptó el mail. En modo desarrollo (sin SMTP_HOST) devuelve False y deja el mail en el log."""
    if not (SMTP_HOST and SMTP_USER and SMTP_PASSWORD):
        # Faltan datos del servidor de correo (lo normal en desarrollo, o mientras falta cargar la contraseña): no se
        # intenta enviar; el mail y su código quedan en el log para poder probar igual.
        logger.warning("[MAIL SIN ENVIAR — no hay SMTP configurado] Para: %s | Asunto: %s\n%s", destinatario, asunto, texto)
        return False
    try:
        msg = construir_mensaje(destinatario, asunto, html, texto)
        if SMTP_PORT == 465:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT_SEGUNDOS) as servidor:
                servidor.login(SMTP_USER, SMTP_PASSWORD)
                servidor.send_message(msg)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT_SEGUNDOS) as servidor:
                servidor.starttls()
                servidor.login(SMTP_USER, SMTP_PASSWORD)
                servidor.send_message(msg)
        return True
    except Exception:  # noqa: BLE001 — cualquier falla de red/credenciales se registra; el flujo del usuario sigue
        logger.exception("No se pudo enviar el mail «%s» a %s", asunto, destinatario)
        return False
