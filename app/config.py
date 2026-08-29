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
