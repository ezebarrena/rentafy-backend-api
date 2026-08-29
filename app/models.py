"""Modelo entidad-relación de Rentafy (chapters/chapter04.tex, sección "Modelo de datos").

Se omite la entidad EJECUCION_PIPELINE: no existe todavía un pipeline de ETL real que
registrar (ver README, sección "Alcance de este backend").
"""

from datetime import datetime, date

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class Usuario(Base):
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(120))
    apellido: Mapped[str] = mapped_column(String(120), default="")
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    sso_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    perfiles: Mapped[list["PerfilInversorHistorial"]] = relationship(
        back_populates="usuario", cascade="all, delete-orphan"
    )
    favoritos: Mapped[list["Favorito"]] = relationship(back_populates="usuario", cascade="all, delete-orphan")


class PerfilInversorHistorial(Base):
    """PERFIL_INVERSOR. Se guarda un registro por cambio, el vigente es el de fecha más reciente."""

    __tablename__ = "perfiles_inversor"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    perfil: Mapped[str] = mapped_column(String(20))  # conservador | moderado | agresivo
    actualizado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    usuario: Mapped["Usuario"] = relationship(back_populates="perfiles")


class Favorito(Base):
    __tablename__ = "favoritos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    instrumento_ticker: Mapped[str] = mapped_column(ForeignKey("instrumentos.ticker"))
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    usuario: Mapped["Usuario"] = relationship(back_populates="favoritos")
    instrumento: Mapped["Instrumento"] = relationship(back_populates="favoritos")


class Instrumento(Base):
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

    cotizaciones: Mapped[list["Cotizacion"]] = relationship(
        back_populates="instrumento", cascade="all, delete-orphan"
    )
    flujos: Mapped[list["FlujoFondo"]] = relationship(back_populates="instrumento", cascade="all, delete-orphan")
    scores: Mapped[list["Scoring"]] = relationship(back_populates="instrumento", cascade="all, delete-orphan")
    favoritos: Mapped[list["Favorito"]] = relationship(back_populates="instrumento")


class Cotizacion(Base):
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


class FlujoFondo(Base):
    __tablename__ = "flujos_fondos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrumento_ticker: Mapped[str] = mapped_column(ForeignKey("instrumentos.ticker"))
    fecha: Mapped[date] = mapped_column(Date)
    tipo: Mapped[str] = mapped_column(String(30))  # Cupón | Amortización | Cupón y amortización
    importe: Mapped[float] = mapped_column(Float)

    instrumento: Mapped["Instrumento"] = relationship(back_populates="flujos")


class Modelo(Base):
    """Versión del modelo de scoring (coeficientes + ponderación). Ver PESO_PERFIL."""

    __tablename__ = "modelos"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)  # ej. "v1.4.0"
    publicado_en: Mapped[date] = mapped_column(Date)
    activo: Mapped[bool] = mapped_column(Boolean, default=True)

    scores: Mapped[list["Scoring"]] = relationship(back_populates="modelo")
    pesos: Mapped[list["PesoPerfil"]] = relationship(back_populates="modelo", cascade="all, delete-orphan")


class Scoring(Base):
    """Los cuatro factores calculados por el Servicio de IA, sin combinar en un único valor."""

    __tablename__ = "scoring"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrumento_ticker: Mapped[str] = mapped_column(ForeignKey("instrumentos.ticker"))
    modelo_id: Mapped[str] = mapped_column(ForeignKey("modelos.id"))
    fecha_calculo: Mapped[date] = mapped_column(Date)
    rendimiento: Mapped[float | None] = mapped_column(Float, nullable=True)
    riesgo: Mapped[float] = mapped_column(Float)
    liquidez: Mapped[float] = mapped_column(Float)
    estabilidad: Mapped[float] = mapped_column(Float)

    instrumento: Mapped["Instrumento"] = relationship(back_populates="scores")
    modelo: Mapped["Modelo"] = relationship(back_populates="scores")


class PesoPerfil(Base):
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


class FuenteDatos(Base):
    __tablename__ = "fuentes_datos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(String(100))
    ultima_actualizacion: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
