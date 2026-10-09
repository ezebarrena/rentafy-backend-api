"""Genera en preview_emails/ los HTML de los mails de Rentafy para verlos en el navegador (sin enviar nada).
Uso, desde la raíz de rentafy-backend-api:  python -m scripts.previsualizar_emails
En el mail real el logo va incrustado por cid; acá se reemplaza por una imagen en base64 solo para que el navegador lo muestre."""

import base64
from pathlib import Path

from app import email_templates

RAIZ = Path(__file__).resolve().parent.parent
SALIDA = RAIZ / "preview_emails"
LOGO = RAIZ / "app" / "assets" / "logo-rentafy-mail.png"


def main() -> None:
    SALIDA.mkdir(exist_ok=True)
    logo = "data:image/png;base64," + base64.b64encode(LOGO.read_bytes()).decode()
    mails = {
        "1-verificacion-de-cuenta": email_templates.mail_verificacion("Facundo", "482915"),
        "2-restablecer-contrasena": email_templates.mail_reset("Facundo", "730264"),
        "3-aviso-contrasena-cambiada": email_templates.mail_password_cambiada("Facundo"),
    }
    for nombre, (asunto, html, _texto) in mails.items():
        destino = SALIDA / f"{nombre}.html"
        destino.write_text(html.replace(f"cid:{email_templates.CID_LOGO}", logo), encoding="utf-8")
        print(f"{destino.relative_to(RAIZ)}  ←  «{asunto}»")


if __name__ == "__main__":
    main()
