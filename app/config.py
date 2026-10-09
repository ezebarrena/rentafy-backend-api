import os

from dotenv import load_dotenv

load_dotenv()

DATABASE_URL_FINANCIERA = os.getenv(
    "DATABASE_URL_FINANCIERA", "postgresql+psycopg2://postgres:postgres@localhost:5432/rentafy_financiera"
)
DATABASE_URL_NO_FINANCIERA = os.getenv(
    "DATABASE_URL_NO_FINANCIERA", "postgresql+psycopg2://postgres:postgres@localhost:5432/rentafy_no_financiera"
)
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-me")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "1440"))
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")

# Emails con rol admin (separados por coma, sin distinguir mayúsculas). Se configuran por entorno y
# no en la base a propósito: no hace falta migrar la tabla de usuarios y nadie puede darse el rol
# desde la app. Hoy el rol habilita las pantallas de testeo interno (Debug Score y Auditoría del
# Score) y sus endpoints. Sin la variable, nadie es admin.
ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}

# rentafy-servicioIA corre en la MISMA máquina que este backend, tanto en desarrollo (tu compu)
# como en producción (la EC2, ver rentafy-servicioIA/deploy/rentafy-servicioia.service: escucha
# en 127.0.0.1, nunca expuesto a internet a propósito) — "localhost" acá siempre significa "este
# mismo servidor", nunca la compu de quien abre el navegador. Solo lo usa /debug (routers/debug.py,
# testeo interno): el resto del backend no depende de este servicio para nada.
IA_SERVICE_URL = os.getenv("IA_SERVICE_URL", "http://localhost:8090")

# Envío de mails (verificación de cuenta, recuperación de contraseña). SMTP genérico: sirve igual para una cuenta
# de Gmail (smtp.gmail.com, puerto 587, con una "contraseña de aplicación") que para Amazon SES u otro servicio:
# cambiar de uno a otro es cambiar estas variables, no código. Sin SMTP_HOST el servicio NO envía nada: escribe el
# mail (con su código) en el log del backend, para poder probar el flujo completo sin una cuenta de correo.
SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
EMAIL_REMITENTE = os.getenv("EMAIL_REMITENTE", SMTP_USER)
EMAIL_REMITENTE_NOMBRE = os.getenv("EMAIL_REMITENTE_NOMBRE", "Rentafy")
# Dirección pública de la app: se usa para los links de los mails.
APP_URL = os.getenv("APP_URL", "https://rentafy-app.com.ar").rstrip("/")
