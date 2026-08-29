"""Schemas Pydantic — espejo de rentafy-frontend/src/data/types.ts, para que la forma de la
respuesta del backend coincida exactamente con lo que el frontend ya espera."""

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, EmailStr, Field

TipoInstrumento = Literal["BONO", "LETRA", "LECAP", "BONCAP", "ON"]
Moneda = Literal["ARS", "USD"]
NivelRiesgo = Literal["Bajo", "Medio", "Alto"]
NivelLiquidez = Literal["Alta", "Media", "Baja"]
PerfilInversor = Literal["conservador", "moderado", "agresivo"]


class FactoresScore(BaseModel):
    rendimiento: Optional[float]
    riesgo: float
    liquidez: float
    estabilidad: float
    fechaCalculo: date
    modeloId: str


class FlujoFondo(BaseModel):
    fecha: date
    tipo: str
    importe: float


class InstrumentoOut(BaseModel):
    ticker: str
    nombre: str
    tipo: TipoInstrumento
    subtipo: Optional[str] = None
    moneda: Moneda
    emisor: str
    legislacion: Optional[str] = None
    parLegislacion: Optional[str] = None
    vencimiento: date
    precio: float
    variacion: float
    volumen: float
    operaciones: int
    tir: Optional[float]
    tirSufijo: Optional[str] = None
    tna: Optional[float]
    duration: Optional[float]
    plazoResidual: Optional[float] = None
    paridad: Optional[float] = None
    riesgo: NivelRiesgo
    liquidez: NivelLiquidez
    precioStale: bool = False
    factores: FactoresScore
    flujos: list[FlujoFondo]
    resumen: str
    score: Optional[float] = Field(default=None, description="Score ya ponderado según el perfil solicitado")


class InstrumentoListItem(BaseModel):
    """Versión liviana usada en listados/rankings (sin flujos)."""

    ticker: str
    nombre: str
    tipo: TipoInstrumento
    subtipo: Optional[str] = None
    moneda: Moneda
    emisor: str
    vencimiento: date
    precio: float
    variacion: float
    tir: Optional[float]
    tirSufijo: Optional[str] = None
    riesgo: NivelRiesgo
    liquidez: NivelLiquidez
    score: float


class PaginatedInstrumentos(BaseModel):
    items: list[InstrumentoListItem]
    total: int
    page: int
    pageSize: int


class PesosPerfil(BaseModel):
    rendimiento: float
    riesgo: float
    liquidez: float
    estabilidad: float


class ScoreRentafyPesosOut(BaseModel):
    modeloId: str
    pesos: dict[PerfilInversor, PesosPerfil]


# --- Auth / usuario ---


class UsuarioRegistro(BaseModel):
    nombre: str
    apellido: str = ""
    email: EmailStr
    password: str = Field(min_length=6)


class UsuarioLogin(BaseModel):
    email: EmailStr
    password: str


class TokenOut(BaseModel):
    accessToken: str
    tokenType: str = "bearer"


class UsuarioOut(BaseModel):
    id: int
    nombre: str
    apellido: str
    email: EmailStr
    perfilInversor: PerfilInversor


class PerfilInversorUpdate(BaseModel):
    perfil: PerfilInversor


# --- Watchlist / calendario / mercado ---


class FavoritoOut(BaseModel):
    ticker: str
    agregadoEn: datetime


class EventoCalendario(BaseModel):
    fecha: date
    ticker: str
    tipo: str
    importe: float
    emisor: str


class IndicadorMercado(BaseModel):
    label: str
    valor: str
    variacion: str
    tendencia: Literal["positiva", "negativa", "neutral"]
