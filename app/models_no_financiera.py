"""Base no financiera (chapter04.tex, "Bases de datos: PostgreSQL"): usuarios, historial de
perfil inversor y favoritos.

`Favorito.instrumento_ticker` referencia a INSTRUMENTO, que vive en la base financiera —
por eso no es una ForeignKey real ni tiene relationship(): se resuelve por coincidencia de
valor en la capa de aplicación (ver routers/watchlist.py), exactamente como la tesis resuelve
la relación entre PERFIL_INVERSOR (no financiera) y PESO_PERFIL (financiera)."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
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
    plazos: Mapped[list["PlazoInversionHistorial"]] = relationship(
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


class PlazoInversionHistorial(BaseNoFinanciera):
    """Horizonte de inversión del usuario (corto/mediano/largo) — mismo patrón que
    PerfilInversorHistorial: un registro por cambio, el vigente es el de fecha más reciente."""

    __tablename__ = "plazos_inversion"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    plazo: Mapped[str] = mapped_column(String(20))  # corto | mediano | largo
    actualizado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    usuario: Mapped["Usuario"] = relationship(back_populates="plazos")


class Favorito(BaseNoFinanciera):
    __tablename__ = "favoritos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id"))
    # Sin ForeignKey: INSTRUMENTO vive en la base financiera (ver docstring del módulo).
    instrumento_ticker: Mapped[str] = mapped_column(String(20))
    creado_en: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    usuario: Mapped["Usuario"] = relationship(back_populates="favoritos")


class CodigoEmail(BaseNoFinanciera):
    """Código de un solo uso que se manda por mail. Una fila por usuario y propósito:

    - `verificacion`: confirma que el mail de una cuenta recién creada es del usuario. Mientras la fila exista, la
      cuenta no puede iniciar sesión con contraseña. Las cuentas anteriores a esta tabla no tienen fila, o sea que
      siguen siendo válidas sin tener que verificar nada (y por eso no hace falta ninguna columna nueva en `usuarios`).
    - `reset`: recuperar la contraseña.

    El código nunca se guarda en claro: se guarda su HMAC (ver codigos.py)."""

    __tablename__ = "codigos_email"
    __table_args__ = (UniqueConstraint("usuario_id", "proposito", name="uq_codigo_usuario_proposito"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    usuario_id: Mapped[int] = mapped_column(ForeignKey("usuarios.id", ondelete="CASCADE"))
    proposito: Mapped[str] = mapped_column(String(20))
    codigo_hash: Mapped[str] = mapped_column(String(64))
    expira_en: Mapped[datetime] = mapped_column(DateTime)
    intentos: Mapped[int] = mapped_column(Integer, default=0)
    enviado_en: Mapped[datetime] = mapped_column(DateTime)
