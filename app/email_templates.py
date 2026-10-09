"""Mails de Rentafy (verificación de cuenta, recuperación de contraseña, aviso de cambio de contraseña).

HTML pensado para clientes de correo, no para navegadores: maquetado con tablas, estilos en línea y sin fuentes web ni
SVG (Gmail y Outlook los ignoran). El logo va incrustado en el mismo mail (cid:logo-rentafy, ver emailing.py), así se ve
sin depender de que el cliente cargue imágenes externas. Cada mail trae también su versión en texto plano.

Colores: los de la app (turquesa de marca #06b6d4, su variante oscura #0e7490) y el azul marino del logo (#082b5c).
"""

from datetime import datetime, timezone
from html import escape
from zoneinfo import ZoneInfo

from .codigos import VIGENCIA_MINUTOS
from .config import APP_URL

CID_LOGO = "logo-rentafy"
AZUL = "#082b5c"
TURQUESA = "#06b6d4"
TURQUESA_OSCURO = "#0e7490"
FUENTE = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _saludo(nombre: str) -> str:
    nombre = (nombre or "").strip()
    return f"Hola {nombre}," if nombre else "Hola,"


def _boton(texto: str, url: str) -> str:
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin:28px 0 0 0;"><tr>'
        f'<td style="background:{TURQUESA};border-radius:999px;">'
        f'<a href="{escape(url)}" style="display:inline-block;padding:13px 28px;font-family:{FUENTE};font-size:15px;'
        f'font-weight:700;color:#ffffff;text-decoration:none;">{escape(texto)}</a></td></tr></table>'
    )


def _bloque_codigo(codigo: str) -> str:
    espaciado = " ".join(codigo)
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin:26px 0 8px 0;"><tr>'
        f'<td align="center" style="background:#ecfeff;border:1px solid #a5f3fc;border-radius:14px;padding:22px 12px;">'
        f'<div style="font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:36px;line-height:1;'
        f'font-weight:800;letter-spacing:2px;color:{AZUL};">{escape(espaciado)}</div>'
        f'<div style="margin-top:10px;font-family:{FUENTE};font-size:13px;color:{TURQUESA_OSCURO};">'
        f"Vence en {VIGENCIA_MINUTOS} minutos</div></td></tr></table>"
    )


def _envolver(preheader: str, titulo: str, cuerpo: str, pie_seguridad: str) -> str:
    """Marco común de todos los mails: logo, título, cuerpo, aviso de seguridad y pie."""
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light">
<title>{escape(titulo)}</title></head>
<body style="margin:0;padding:0;background:#f4f7f9;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:#f4f7f9;">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f4f7f9;"><tr><td align="center" style="padding:32px 16px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:560px;background:#ffffff;border:1px solid #e2e8f0;border-radius:18px;overflow:hidden;">
    <tr><td style="height:5px;background:{TURQUESA};font-size:0;line-height:0;">&nbsp;</td></tr>
    <tr><td style="padding:34px 36px 8px 36px;"><img src="cid:{CID_LOGO}" alt="Rentafy" width="150" style="display:block;border:0;height:auto;"></td></tr>
    <tr><td style="padding:18px 36px 6px 36px;font-family:{FUENTE};">
      <h1 style="margin:0 0 14px 0;font-size:24px;line-height:1.25;font-weight:800;color:#0f172a;">{escape(titulo)}</h1>
      <div style="font-size:15px;line-height:1.6;color:#475569;">{cuerpo}</div>
    </td></tr>
    <tr><td style="padding:14px 36px 30px 36px;font-family:{FUENTE};font-size:13px;line-height:1.55;color:#64748b;">{pie_seguridad}</td></tr>
    <tr><td style="background:#f8fafc;border-top:1px solid #e2e8f0;padding:18px 36px;font-family:{FUENTE};font-size:12px;line-height:1.6;color:#94a3b8;">
      <strong style="color:#64748b;">Rentafy</strong> · Renta fija argentina, simple.<br>
      Este es un mensaje automático: no hace falta que lo respondas. <a href="{escape(APP_URL)}" style="color:{TURQUESA_OSCURO};text-decoration:none;">{escape(APP_URL.replace("https://", "").replace("http://", ""))}</a>
    </td></tr>
  </table>
</td></tr></table></body></html>"""


def mail_verificacion(nombre: str, codigo: str) -> tuple[str, str, str]:
    """(asunto, html, texto) del mail que confirma el correo de una cuenta nueva."""
    asunto = f"Tu código de verificación de Rentafy: {codigo}"
    cuerpo = (
        f"{escape(_saludo(nombre))}<br>Gracias por crear tu cuenta en Rentafy. Ingresá este código para confirmar tu correo "
        f"y empezar a explorar oportunidades de renta fija.{_bloque_codigo(codigo)}"
    )
    pie = "Si no creaste una cuenta en Rentafy, podés ignorar este mensaje: sin este código nadie puede usar tu correo."
    texto = (
        f"{_saludo(nombre)}\n\nGracias por crear tu cuenta en Rentafy. Tu código de verificación es:\n\n    {codigo}\n\n"
        f"Vence en {VIGENCIA_MINUTOS} minutos.\n\n{pie}\n\n— Rentafy · {APP_URL}\n"
    )
    return asunto, _envolver(f"Tu código es {codigo}. Vence en {VIGENCIA_MINUTOS} minutos.", "Confirmá tu correo", cuerpo, pie), texto


def mail_reset(nombre: str, codigo: str) -> tuple[str, str, str]:
    """(asunto, html, texto) del mail con el código para restablecer la contraseña."""
    asunto = f"Código para restablecer tu contraseña de Rentafy: {codigo}"
    cuerpo = (
        f"{escape(_saludo(nombre))}<br>Recibimos un pedido para restablecer la contraseña de tu cuenta. Ingresá este código "
        f"para elegir una nueva.{_bloque_codigo(codigo)}"
    )
    pie = "Si no lo pediste, ignorá este mensaje: tu contraseña no cambia mientras nadie ingrese este código."
    texto = (
        f"{_saludo(nombre)}\n\nRecibimos un pedido para restablecer la contraseña de tu cuenta. Tu código es:\n\n    {codigo}\n\n"
        f"Vence en {VIGENCIA_MINUTOS} minutos.\n\n{pie}\n\n— Rentafy · {APP_URL}\n"
    )
    return asunto, _envolver(f"Tu código es {codigo}. Vence en {VIGENCIA_MINUTOS} minutos.", "Restablecé tu contraseña", cuerpo, pie), texto


def mail_password_cambiada(nombre: str, cuando: datetime | None = None) -> tuple[str, str, str]:
    """(asunto, html, texto) del aviso de que la contraseña se cambió (por si no fue el dueño de la cuenta)."""
    asunto = "Cambiamos la contraseña de tu cuenta de Rentafy"
    instante = (cuando or datetime.utcnow()).replace(tzinfo=timezone.utc)  # la base guarda UTC sin zona
    momento = instante.astimezone(ZoneInfo("America/Argentina/Buenos_Aires")).strftime("%d/%m/%Y a las %H:%M (hora de Argentina)")
    cuerpo = (
        f"{escape(_saludo(nombre))}<br>Te avisamos que la contraseña de tu cuenta se cambió el {momento}. Si fuiste vos, no tenés "
        f"que hacer nada.{_boton('Abrir Rentafy', APP_URL)}"
    )
    pie = (
        "¿No reconocés este cambio? Entrá a Rentafy, elegí «Olvidé mi contraseña» y definí una nueva; así recuperás el "
        "control de tu cuenta."
    )
    texto = (
        f"{_saludo(nombre)}\n\nTe avisamos que la contraseña de tu cuenta se cambió el {momento}. Si fuiste vos, no tenés que "
        f"hacer nada.\n\n{pie}\n\n— Rentafy · {APP_URL}\n"
    )
    return asunto, _envolver("La contraseña de tu cuenta se actualizó.", "Tu contraseña se actualizó", cuerpo, pie), texto
