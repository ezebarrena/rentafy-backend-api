"""Base no financiera (chapter04.tex, "Bases de datos: PostgreSQL"): usuarios, historial de
perfil inversor y favoritos.

`Favorito.instrumento_ticker` referencia a INSTRUMENTO, que vive en la base financiera —
por eso no es una ForeignKey real ni tiene relationship(): se resuelve por coincidencia de
valor en la capa de aplicación (ver routers/watchlist.py), exactamente como la tesis resuelve
la relación entre PERFIL_INVERSOR (no financiera) y PESO_PERFIL (financiera)."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import BaseNoFinanciera


class Usuario(BaseNoFinanciera):
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


class PerfilInversorHistorial(BaseNoFinanciera):
    """PERFIL_INVERSOR. Se guarda un registro por cambio, el vigente es el de fecha más reciente."""

    __tablename__ = "perfiles_inversor"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    perfil: Mapped[str] = mapped_column(String(20))  # conservador | moderado | agresivo
    actualizado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    usuario: Mapped["Usuario"] = relationship(back_populates="perfiles")


class Favorito(BaseNoFinanciera):
    __tablename__ = "favoritos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    # Sin ForeignKey: INSTRUMENTO vive en la base financiera (ver docstring del módulo).
    instrumento_ticker: Mapped[str] = mapped_column(String(20))
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    usuario: Mapped["Usuario"] = relationship(back_populates="favoritos")
