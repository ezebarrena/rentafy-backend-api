"""Base financiera (chapter04.tex, "Bases de datos: PostgreSQL"): catálogo de instrumentos,
cotizaciones, flujos de fondos y resultados de scoring.

Se omite la entidad EJECUCION_PIPELINE: no existe todavía un pipeline de ETL real que
registrar (ver README, sección "Alcance de este backend").
"""

from datetime import datetime, date

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import BaseFinanciera


class Instrumento(BaseFinanciera):
    __tablename__ = "instrumentos"

    ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    nombre: Mapped[str] = mapped_column(String(200))
    tipo: Mapped[str] = mapped_column(String(20))  # BONO | LETRA | LECAP | BONCAP | ON
    subtipo: Mapped[str | None] = mapped_column(String(50), nullable=True)
    moneda: Mapped[str] = mapped_column(String(3))  # ARS | USD
    emisor: Mapped[str] = mapped_column(String(200))
    legislacion: Mapped[str | None] = mapped_column(String(30), nullable=True)
    par_legislacion: Mapped[str | None] = mapped_column(String(20), nullable=True)
    vencimiento: Mapped[date] = mapped_column(Date)
    riesgo: Mapped[str] = mapped_column(String(10))  # Bajo | Medio | Alto
    liquidez: Mapped[str] = mapped_column(String(10))  # Alta | Media | Baja
    resumen: Mapped[str] = mapped_column(String(500), default="")
    # False cuando el instrumento deja de aparecer varias corridas seguidas de la importación
    # real (vencido, delisted, etc. — ver ingest.py:_marcar_ausentes_como_inactivos). Los
    # listados principales (GET /instrumentos, /rankings) lo excluyen por defecto en vez de
    # seguir mostrando un precio cada vez más viejo sin avisar; el detalle sigue siendo
    # accesible (ej. para quien lo tenga en watchlist).
    activo: Mapped[bool] = mapped_column(Boolean, default=True)

    cotizaciones: Mapped[list["Cotizacion"]] = relationship(
        back_populates="instrumento", cascade="all, delete-orphan"
    )
    flujos: Mapped[list["FlujoFondo"]] = relationship(back_populates="instrumento", cascade="all, delete-orphan")
    scores: Mapped[list["Scoring"]] = relationship(back_populates="instrumento", cascade="all, delete-orphan")
    # Nota: no hay relationship() hacia Favorito — esa entidad vive en la base no financiera
    # (ver models_no_financiera.py) y no puede haber una FK real entre dos bases distintas.


class Cotizacion(BaseFinanciera):
    """Variables de mercado (crudas) y calculadas, por instrumento y fecha."""

    __tablename__ = "cotizaciones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrumento_ticker: Mapped[str] = mapped_column(ForeignKey("instrumentos.ticker"))
    fecha: Mapped[date] = mapped_column(Date)
    precio: Mapped[float] = mapped_column(Float)
    variacion: Mapped[float] = mapped_column(Float)
    volumen: Mapped[float] = mapped_column(Float)
    operaciones: Mapped[int] = mapped_column(Integer)
    tir: Mapped[float | None] = mapped_column(Float, nullable=True)
    tir_sufijo: Mapped[str | None] = mapped_column(String(20), nullable=True)
    tna: Mapped[float | None] = mapped_column(Float, nullable=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    plazo_residual: Mapped[float | None] = mapped_column(Float, nullable=True)
    paridad: Mapped[float | None] = mapped_column(Float, nullable=True)
    precio_stale: Mapped[bool] = mapped_column(Boolean, default=False)

    instrumento: Mapped["Instrumento"] = relationship(back_populates="cotizaciones")


class FlujoFondo(BaseFinanciera):
    __tablename__ = "flujos_fondos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrumento_ticker: Mapped[str] = mapped_column(ForeignKey("instrumentos.ticker"))
    fecha: Mapped[date] = mapped_column(Date)
    tipo: Mapped[str] = mapped_column(String(30))  # Cupón | Amortización | Cupón y amortización
    importe: Mapped[float] = mapped_column(Float)

    instrumento: Mapped["Instrumento"] = relationship(back_populates="flujos")


class Modelo(BaseFinanciera):
    """Versión del modelo de scoring (coeficientes + ponderación). Ver PESO_PERFIL."""

    __tablename__ = "modelos"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)  # ej. "v1.4.0", "placeholder-aleatorio"
    publicado_en: Mapped[date] = mapped_column(Date)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)

    scores: Mapped[list["Scoring"]] = relationship(back_populates="modelo")
    pesos: Mapped[list["PesoPerfil"]] = relationship(back_populates="modelo", cascade="all, delete-orphan")


class Scoring(BaseFinanciera):
    """Los cuatro factores calculados por el Servicio de IA, sin combinar en un único valor."""

    __tablename__ = "scoring"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrumento_ticker: Mapped[str] = mapped_column(ForeignKey("instrumentos.ticker"))
    modelo_id: Mapped[str] = mapped_column(ForeignKey("modelos.id"))
    fecha_calculo: Mapped[date] = mapped_column(Date)
    rendimiento: Mapped[float | None] = mapped_column(Float, nullable=True)
    riesgo: Mapped[float] = mapped_column(Float)
    liquidez: Mapped[float] = mapped_column(Float)
    # Nullable: requiere 20 ruedas de historial de precio (ver Servicio de IA,
    # app/factores/estabilidad.py) — va a faltar mientras la base acumula ese historial.
    estabilidad: Mapped[float | None] = mapped_column(Float, nullable=True)

    instrumento: Mapped["Instrumento"] = relationship(back_populates="scores")
    modelo: Mapped["Modelo"] = relationship(back_populates="scores")


class PesoPerfil(BaseFinanciera):
    """Ponderación de cada factor por perfil de inversor, versionada junto al MODELO."""

    __tablename__ = "pesos_perfil"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    modelo_id: Mapped[str] = mapped_column(ForeignKey("modelos.id"))
    perfil: Mapped[str] = mapped_column(String(20))  # conservador | moderado | agresivo
    w_rendimiento: Mapped[float] = mapped_column(Float)
    w_riesgo: Mapped[float] = mapped_column(Float)
    w_liquidez: Mapped[float] = mapped_column(Float)
    w_estabilidad: Mapped[float] = mapped_column(Float)

    modelo: Mapped["Modelo"] = relationship(back_populates="pesos")


class FuenteDatos(BaseFinanciera):
    __tablename__ = "fuentes_datos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(100))
    ultima_actualizacion: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class IndicadorMacro(BaseFinanciera):
    """Cache de indicadores macro externos de valor único (ej. inflación esperada REM del
    BCRA), actualizado por el job diario (ver financial_utils.py) para no pegarle a la API
    externa en cada request de detalle de instrumento. Una fila por indicador, sobrescrita en
    cada actualización — no se versiona histórico acá, solo el último valor vigente."""

    __tablename__ = "indicadores_macro"

    nombre: Mapped[str] = mapped_column(String(50), primary_key=True)
    valor: Mapped[float] = mapped_column(Float)
    fecha: Mapped[date] = mapped_column(Date)


class IndicadorMercadoCache(BaseFinanciera):
    """Cache de las tarjetas de indicadores del Dashboard que antes se resolvían en vivo en
    cada GET /mercado/indicadores (Riesgo País, Dólar CCL/MEP — ver financial_utils.py):
    ya vienen formateadas para mostrar directamente (a diferencia de IndicadorMacro, que
    guarda un escalar crudo para usar en cálculos). Actualizado por el job diario de las
    18:05; el endpoint solo lee esta tabla."""

    __tablename__ = "indicadores_mercado_cache"

    label: Mapped[str] = mapped_column(String(50), primary_key=True)
    valor: Mapped[str] = mapped_column(String(50))
    variacion: Mapped[str] = mapped_column(String(50))
    tendencia: Mapped[str] = mapped_column(String(10))  # positiva | negativa | neutral
    detalle: Mapped[str | None] = mapped_column(String(50), nullable=True)
    fecha: Mapped[date] = mapped_column(Date)
