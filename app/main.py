import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import CORS_ORIGINS
from .database import BaseFinanciera, BaseNoFinanciera, SessionFinanciera, engine_financiera, engine_no_financiera
from .routers import auth, calendario, instrumentos, mercado, rankings, watchlist
from .scheduler import iniciar_scheduler
from .seed import seed_if_empty

# Se importan explícitamente para que create_all() conozca todas las tablas de cada base.
from . import models_financiera, models_no_financiera  # noqa: F401

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    BaseFinanciera.metadata.create_all(bind=engine_financiera)
    BaseNoFinanciera.metadata.create_all(bind=engine_no_financiera)
    db = SessionFinanciera()
    try:
        seed_if_empty(db)
    finally:
        db.close()
    scheduler = iniciar_scheduler()
    yield
    scheduler.shutdown()


app = FastAPI(
    title="Rentafy API",
    description="Backend principal de Rentafy — capítulo 4 de la tesis, sección Arquitectura del producto.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(instrumentos.router)
app.include_router(rankings.router)
app.include_router(watchlist.router)
app.include_router(calendario.router)
app.include_router(mercado.router)


@app.get("/", tags=["health"])
def health():
    return {"status": "ok", "service": "rentafy-backend-api"}
