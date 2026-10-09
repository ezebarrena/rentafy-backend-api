"""Manda un mail de Rentafy de verdad (por el SMTP configurado en .env) para ver cómo llega a una bandeja real.
Uso, desde la raíz de rentafy-backend-api:

    python -m scripts.enviar_mail_de_prueba tu@correo.com            # mail de verificación
    python -m scripts.enviar_mail_de_prueba tu@correo.com reset      # recuperar contraseña
    python -m scripts.enviar_mail_de_prueba tu@correo.com aviso      # aviso de contraseña cambiada

Solo manda a la dirección que le pases, con un código de ejemplo (no es un código real de ninguna cuenta).
"""

import sys

from app import email_templates, emailing
from app.config import SMTP_HOST, SMTP_PASSWORD, SMTP_USER

TIPOS = {
    "verificacion": lambda: email_templates.mail_verificacion("Facundo", "482915"),
    "reset": lambda: email_templates.mail_reset("Facundo", "730264"),
    "aviso": lambda: email_templates.mail_password_cambiada("Facundo"),
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(__doc__)
        return 1
    destinatario = sys.argv[1]
    tipo = sys.argv[2] if len(sys.argv) > 2 else "verificacion"
    if tipo not in TIPOS:
        print(f"Tipo desconocido «{tipo}». Opciones: {', '.join(TIPOS)}")
        return 1
    if not (SMTP_HOST and SMTP_USER and SMTP_PASSWORD):
        print("Falta configurar el correo en .env: SMTP_HOST, SMTP_USER y SMTP_PASSWORD (la contraseña de aplicación de Google).")
        return 1
    asunto, html, texto = TIPOS[tipo]()
    if emailing.enviar(destinatario, asunto, html, texto):
        print(f"Enviado a {destinatario}: «{asunto}». Revisá la bandeja de entrada (y spam).")
        return 0
    print("No se pudo enviar: mirá el error de arriba (usuario/contraseña de aplicación, o la verificación en dos pasos de la cuenta).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
