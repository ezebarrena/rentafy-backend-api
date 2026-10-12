"""Schemas Pydantic — espejo de rentafy-frontend/src/data/types.ts, para que la forma de la
respuesta del backend coincida exactamente con lo que el frontend ya espera."""

import re
from datetime import date, datetime
from typing import Literal, Optional

from pydantic import AfterValidator, BaseModel, EmailStr, Field
from typing_extensions import Annotated

TipoInstrumento = Literal["BONO", "LECAP", "BONCAP", "ON", "CAUCION"]
Moneda = Literal["ARS", "USD"]
NivelRiesgo = Literal["Bajo", "Medio", "Alto"]
NivelLiquidez = Literal["Alta", "Media", "Baja"]
PerfilInversor = Literal["conservador", "moderado", "agresivo"]
# Horizonte de inversión del usuario — corte por años de vencimiento: corto ≤1 año,
# mediano 1-3 años, largo >3 años (ver scoring.py, AJUSTE_PLAZO).
PlazoInversion = Literal["corto", "mediano", "largo"]


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


class VariacionPeriodo(BaseModel):
    """Variación de precio contra la cotización más cercana a `dias` atrás (ver
    serializers.py:_variaciones_periodo). `variacion`/`fechaReferencia` son None cuando el
    instrumento todavía no tiene esa profundidad de historial — la mayoría del catálogo hoy
    no llega a 180/365 días, ver análisis de factibilidad (no es un error, se va llenando con
    el tiempo a medida que se acumula ingesta diaria real)."""

    dias: int
    variacion: Optional[float] = None
    fechaReferencia: Optional[date] = None


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
    fechaCotizacion: Optional[date] = Field(default=None, description="Fecha de la cotización que se está mostrando (la última cargada del instrumento).")
    factores: Optional[FactoresScore] = Field(
        default=None, description="Null cuando el instrumento todavía no tiene Scoring calculado (RNF-29)"
    )
    flujos: list[FlujoFondo]
    resumen: str = ""
    score: Optional[float] = Field(default=None, description="Score ya ponderado según el perfil solicitado")
    variacionesPeriodo: list[VariacionPeriodo] = Field(
        default_factory=list,
        description="Variación de precio a 7/14/30/90/180/365 días — solo en el detalle, no en los listados.",
    )


class InstrumentoOpcion(BaseModel):
    """Versión mínima para selectores (Comparador, Calculadora): sin cotización ni score,
    para poder listar el catálogo completo sin paginar. `vencimiento` se agregó para que la
    Calculadora pueda filtrar vencidos del lado del cliente sin afectar al Comparador, que
    usa este mismo endpoint y sí quiere seguir viéndolos."""

    ticker: str
    nombre: str
    tipo: TipoInstrumento
    subtipo: Optional[str] = None
    moneda: Moneda
    vencimiento: date


class InstrumentoConsistente(BaseModel):
    """Para el bloque "Scores más consistentes" del Dashboard (ver GET /instrumentos/consistentes):
    instrumentos cuyo Score viene siendo alto Y estable en las últimas ruedas, no solo el de
    hoy — eso ya lo cubre "Oportunidades destacadas"/"La oportunidad de hoy"."""

    ticker: str
    nombre: str
    tipo: TipoInstrumento
    subtipo: Optional[str] = None
    scorePromedio: float
    desvio: float
    saltoMaximo: float  # mayor |diferencia| entre dos ruedas consecutivas de la ventana
    scores: list[int]  # últimas ruedas con Scoring calculado, orden cronológico (viejo → nuevo)


class InstrumentoEnAlza(BaseModel):
    """Para el bloque "Scores en alza" del Dashboard (ver GET /instrumentos/en-alza):
    instrumentos cuyo Score viene subiendo rueda a rueda, no el más alto de hoy ni el más
    estable — el que está mejorando."""

    ticker: str
    nombre: str
    tipo: TipoInstrumento
    subtipo: Optional[str] = None
    pendiente: float  # puntos de Score que gana en promedio por rueda (regresión lineal)
    scores: list[int]  # últimas ruedas con Scoring calculado, orden cronológico (viejo → nuevo)


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
    publicadoEn: date | None
    pesos: dict[PerfilInversor, PesosPerfil]


# --- Auth / usuario ---


# Largos máximos de lo que el usuario escribe de sí mismo: nombre y apellido hasta 20 caracteres cada uno y el avatar (nombre imaginario) hasta 10.
MAX_NOMBRE = 20
MAX_AVATAR = 10
MAX_EMAIL = 100
MIN_PASSWORD = 8
MAX_PASSWORD = 72  # bcrypt solo mira los primeros 72 bytes: más largo no suma seguridad y se prestaría a confusión

# Nombre y apellido: solo letras (con acentos), y entre palabras un espacio, apóstrofe, guion o punto (María José, O'Brien, De la Fuente).
_NOMBRE_VALIDO = re.compile(r"^[^\W\d_]+(?:[ '.\-][^\W\d_]+)*\.?$")


def _nombre_de_persona(v: str, *, obligatorio: bool) -> str:
    v = " ".join(v.split())  # sin espacios de más al principio, al final ni repetidos
    if not v:
        if obligatorio:
            raise ValueError("Ingresá tu nombre")
        return v
    if not _NOMBRE_VALIDO.match(v):
        raise ValueError("Solo letras, espacios, guiones y apóstrofes (sin números ni símbolos)")
    return v


def _nombre_obligatorio(v: str) -> str:
    return _nombre_de_persona(v, obligatorio=True)


def _apellido_opcional(v: str) -> str:
    return _nombre_de_persona(v, obligatorio=False)


def _email_acotado(v: str) -> str:
    if len(v) > MAX_EMAIL:
        raise ValueError(f"El correo puede tener hasta {MAX_EMAIL} caracteres")
    return v.strip()  # no se pasa a minúsculas: el login busca el correo tal cual se escribió al registrarse


def validar_password_nueva(v: str) -> str:
    """Contraseña que se elige (registro, restablecer, cambiar): 8 a 72 caracteres con al menos una mayúscula, una minúscula y un número.
    El login no la aplica: las cuentas anteriores pueden tener una más corta y tienen que poder entrar."""
    if len(v) < MIN_PASSWORD:
        raise ValueError(f"La contraseña debe tener al menos {MIN_PASSWORD} caracteres")
    if len(v) > MAX_PASSWORD:
        raise ValueError(f"La contraseña puede tener hasta {MAX_PASSWORD} caracteres")
    if not re.search(r"[A-ZÁÉÍÓÚÑÜ]", v):
        raise ValueError("La contraseña debe tener al menos una letra mayúscula")
    if not re.search(r"[a-záéíóúñü]", v):
        raise ValueError("La contraseña debe tener al menos una letra minúscula")
    if not re.search(r"\d", v):
        raise ValueError("La contraseña debe tener al menos un número")
    return v


NombreObligatorio = Annotated[str, Field(min_length=1, max_length=MAX_NOMBRE), AfterValidator(_nombre_obligatorio)]
ApellidoOpcional = Annotated[str, Field(max_length=MAX_NOMBRE), AfterValidator(_apellido_opcional)]
EmailRegistro = Annotated[EmailStr, AfterValidator(_email_acotado)]
PasswordNueva = Annotated[str, AfterValidator(validar_password_nueva)]


class UsuarioRegistro(BaseModel):
    nombre: NombreObligatorio
    apellido: ApellidoOpcional = ""
    email: EmailRegistro
    password: PasswordNueva


class UsuarioLogin(BaseModel):
    email: EmailStr
    password: str


class EmailIn(BaseModel):
    email: EmailStr


class VerificarEmailIn(BaseModel):
    email: EmailStr
    codigo: str = Field(min_length=1, max_length=20)


class RestablecerPasswordIn(BaseModel):
    email: EmailStr
    codigo: str = Field(min_length=1, max_length=20)
    password: PasswordNueva


class CambiarPasswordIn(BaseModel):
    passwordActual: str
    passwordNueva: PasswordNueva


class MensajeOut(BaseModel):
    mensaje: str


class GoogleLogin(BaseModel):
    """ID token que devuelve Google Identity Services en el frontend tras el login con Google;
    se valida server-side contra la API de Google antes de confiar en su contenido."""

    idToken: str


class TokenOut(BaseModel):
    accessToken: str
    tokenType: str = "bearer"


class UsuarioOut(BaseModel):
    id: int
    nombre: str
    apellido: str
    email: EmailStr
    perfilInversor: PerfilInversor
    plazoInversion: PlazoInversion
    esAdmin: bool = False
    # Plan vigente ("casual" por defecto; los admin figuran como "pro"). Ver planes.py.
    plan: Literal["casual", "pro"] = "casual"
    # Nombre imaginario (hasta 10 caracteres) que se muestra en la barra y en el resto de la app; None si no eligió uno.
    avatar: Optional[str] = None
    # False hasta que el usuario elige (o confirma) su perfil y su horizonte: el front le muestra el test de perfil
    # inversor la primera vez. Ver _perfil_configurado en routers/auth.py.
    perfilConfigurado: bool = False


class PlanUpdate(BaseModel):
    plan: Literal["casual", "pro"]


class PlanCambiadoOut(UsuarioOut):
    # Favoritos que se quitaron al pasar a Casual (se conservan los 5 más antiguos).
    favoritosQuitados: int = 0


class PerfilInversorUpdate(BaseModel):
    perfil: PerfilInversor


class PlazoInversionUpdate(BaseModel):
    plazo: PlazoInversion


class UsuarioNombreUpdate(BaseModel):
    nombre: NombreObligatorio
    apellido: ApellidoOpcional = ""
    # Si no viene, no se toca el avatar actual; vacío (o solo espacios) lo borra y la app vuelve a mostrar el nombre.
    avatar: Optional[str] = Field(default=None, max_length=MAX_AVATAR + 20)  # el tope real se valida ya sin espacios (ver routers/auth.py)


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
    moneda: str


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
    # Instrumentos del mismo grupo que quedaron afuera del ajuste por duration muy chica (ver
    # CURVA_DURATION_MINIMA) — se muestran igual, aparte, para que quede claro que no es que
    # falten datos: es que ese tramo es demasiado ruidoso para curvear.
    excluidos: list[PuntoCurva] = []


FamiliaCurvaUnica = Literal["fija", "cer", "tamar", "dolar_linked", "soberano", "on"]
BaseTirCurvaUnica = Literal["nominal", "real", "usd"]


class PuntoCurvaUnica(BaseModel):
    """Un instrumento para la "Curva única" (en pesos o en dólares). `tir` es la TIR tal cual la publica la fuente, en % anual efectivo, y
    `baseTir` dice en qué unidad está: `nominal` (en la moneda del instrumento, lista para graficar), `real` (BONCER: tasa real sobre
    CER) o `usd` (dólar linked: tasa sobre el dólar). En pesos, el front pasa las dos últimas a pesos nominales con el escenario elegido
    (ver curvaUnica.ts)."""

    ticker: str
    nombre: str
    familia: FamiliaCurvaUnica
    subtipo: Optional[str] = None
    plazoDias: int
    tir: float
    baseTir: BaseTirCurvaUnica


class CurvaUnica(BaseModel):
    instrumentos: list[PuntoCurvaUnica]
    # Rango de plazo (en días) con el que se armó la lista: más corto es ruido y más largo estira el eje.
    plazoMinDias: int
    plazoMaxDias: int
    # Inflación esperada a 12 meses (REM, BCRA), en % anual, y el día en que se guardó: de ahí sale el escenario por defecto de la curva en
    # pesos. None si todavía no hay dato.
    inflacionEsperadaAnual: Optional[float] = None
    inflacionEsperadaFecha: Optional[date] = None
    # Último dato mensual de inflación (IPC, ArgentinaDatos, el mismo del Dashboard) y su mes ("sep. 2026"): es el escenario por defecto de la
    # inflación mensual. None si la fuente no respondió (el front cae a la mensualización del REM).
    inflacionMensualUltima: Optional[float] = None
    inflacionMensualMes: Optional[str] = None


class NotificacionOut(BaseModel):
    id: int
    tipo: Literal["vencimiento", "pago", "variacion", "tir", "rendimiento", "score"]
    ticker: str
    titulo: str
    mensaje: str
    # Fecha del vencimiento o del pago, si el aviso es de ese tipo.
    fechaEvento: Optional[date] = None
    # Cuándo se generó, en UTC (con "Z"): el front la muestra en hora local.
    creadaEn: str
    leida: bool


class NotificacionesPagina(BaseModel):
    items: list[NotificacionOut]
    total: int
    noLeidas: int


class ContadorNotificaciones(BaseModel):
    noLeidas: int


class PuntoRiesgoPais(BaseModel):
    fecha: date
    valor: float


class PuntoDolarEquilibrio(BaseModel):
    ticker: str
    nombre: str
    familia: FamiliaCurvaUnica
    plazoDias: int
    tir: float
    # Suba (%) del dólar hasta el vencimiento que iguala al bono con comprar dólares.
    subaDelDolar: float


class DolarEquilibrio(BaseModel):
    instrumentos: list[PuntoDolarEquilibrio]
    # Dólar MEP y CCL de referencia (venta) y su fecha: el front multiplica cada uno por (1 + suba) para dar el dólar de equilibrio.
    dolarMep: Optional[float] = None
    dolarMepFecha: Optional[date] = None
    dolarCcl: Optional[float] = None
    dolarCclFecha: Optional[date] = None


class PuntoInflacion(BaseModel):
    fecha: date
    mensual: Optional[float] = None
    interanual: Optional[float] = None


class InflacionOut(BaseModel):
    serie: list[PuntoInflacion]
    # Inflación esperada a 12 meses (REM, BCRA), en % anual, y el día en que se guardó.
    inflacionEsperadaAnual: Optional[float] = None
    inflacionEsperadaFecha: Optional[date] = None


class PuntoInflacionEquilibrio(BaseModel):
    ticker: str
    nombre: str
    plazoDias: int
    tirReal: float
    # Tasa fija en pesos del mismo plazo según la curva de tasa fija, y la inflación que iguala al CER con ella.
    tasaFija: float
    inflacionEquilibrioAnual: float
    inflacionEquilibrioMensual: float


class InflacionEquilibrio(BaseModel):
    bonos: list[PuntoInflacionEquilibrio]
    inflacionMensualUltima: Optional[float] = None
    inflacionMensualMes: Optional[str] = None
    inflacionEsperadaAnual: Optional[float] = None
    # Cuántos instrumentos de tasa fija se usaron para la curva y su R².
    instrumentosTasaFija: int = 0
    r2: Optional[float] = None


class PuntoDolares(BaseModel):
    fecha: date
    mep: Optional[float] = None
    ccl: Optional[float] = None


class SensibilidadBono(BaseModel):
    ticker: str
    nombre: str
    duration: Optional[float] = None
    tir: Optional[float] = None
    # Variación aproximada del precio (%) por cada +100 pb de riesgo país (negativa si el precio baja), R² y observaciones usadas.
    porCada100pb: float
    r2: float
    observaciones: int


class SensibilidadRiesgoPais(BaseModel):
    bonos: list[SensibilidadBono]
    desde: date
    hasta: date
