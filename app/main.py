from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import CORS_ORIGINS
from .database import Base, SessionLocal, engine
from .routers import auth, calendario, instrumentos, mercado, rankings, watchlist
from .seed import seed_if_empty


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed_if_empty(db)
    finally:
        db.close()
    yield


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
