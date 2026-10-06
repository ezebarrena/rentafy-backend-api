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
