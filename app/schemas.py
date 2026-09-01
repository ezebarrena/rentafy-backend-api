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
    estabilidad: Optional[float]
    # Justificación en texto de cada factor (ver rentafy-servicioIA/app/perfiles/
    # justificaciones.py), para el detalle debajo de cada mini-tarjeta de factor.
    rendimientoDetalle: Optional[str] = None
    riesgoDetalle: str = ""
    liquidezDetalle: str = ""
    estabilidadDetalle: str = ""
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
    tirNominalEstimada: Optional[float] = Field(
        default=None,
        description="Solo para BONCER: TIR real + inflación esperada REM (BCRA) a 12 meses, compuesta. Estimación, no un dato de mercado.",
    )
    tna: Optional[float]
    duration: Optional[float]
    plazoResidual: Optional[float] = None
    paridad: Optional[float] = None
    riesgo: NivelRiesgo
    liquidez: NivelLiquidez
    precioStale: bool = False
    factores: Optional[FactoresScore] = Field(
        default=None, description="Null cuando el instrumento todavía no tiene Scoring calculado (RNF-29)"
    )
    flujos: list[FlujoFondo]
    resumen: str = ""
    score: Optional[float] = Field(default=None, description="Score ya ponderado según el perfil solicitado")


class InstrumentoOpcion(BaseModel):
    """Versión mínima para selectores (Comparador, Calculadora): sin cotización ni score,
    para poder listar el catálogo completo sin paginar."""

    ticker: str
    nombre: str
    tipo: TipoInstrumento
    subtipo: Optional[str] = None
    moneda: Moneda


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
    volumen: float = 0
    tir: Optional[float]
    tirSufijo: Optional[str] = None
    riesgo: NivelRiesgo
    liquidez: NivelLiquidez
    resumen: str = ""
    score: Optional[float] = None


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
    enVivo: bool = False
    detalle: Optional[str] = None  # dato secundario chico, ej. el valor del mes anterior para Inflación


class PuntoHistorico(BaseModel):
    """Un cierre diario real (RF-07). Sin OHLC: la fuente solo se consulta una vez por día
    (18hs, ver scheduler.py), así que ese único valor es el cierre, no hay apertura/máximo/
    mínimo intradiario."""

    fecha: date
    precio: float
    volumen: float
    operaciones: int


class PuntoScore(BaseModel):
    """Score ya ponderado según el perfil solicitado (mismo compute_score() que el valor
    vigente), para un día con Scoring calculado. No es un dato nuevo a almacenar: Scoring ya
    tiene una fila por instrumento y día desde que corre el Servicio de IA."""

    fecha: date
    score: int


class PuntoCurva(BaseModel):
    ticker: str
    nombre: str
    duration: float
    tir: float


class CurvaRendimiento(BaseModel):
    """Curva de rendimiento (TIR contra duration) de un grupo de instrumentos homogéneo —
    mismo tipo, subtipo y moneda, ajustada con una regresión log-lineal (TIR = a + b*ln(duration)).

    Para ON esto agrupa TODOS los emisores en una sola curva: mezcla riesgo de crédito
    distinto (YPF con Cresud, por ejemplo) como si fuera comparable. Es una simplificación
    deliberada, no un descuido — separar por emisor queda para una iteración futura (ver
    también rendimiento.py del Servicio de IA, mismo tipo de limitación por falta de rating
    crediticio integrado)."""

    tipo: TipoInstrumento
    subtipo: Optional[str] = None
    moneda: Moneda
    label: str
    puntos: list[PuntoCurva]
    a: float
    b: float
    r2: float
