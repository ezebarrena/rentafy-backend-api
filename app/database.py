"""Dos motores y dos bases declarativas separadas (chapter04.tex, "Bases de datos:
PostgreSQL"): BaseFinanciera para el catálogo/cotizaciones/scoring, BaseNoFinanciera para
usuarios/perfiles/favoritos. No hay foreign keys ni relationship() entre ambas — cualquier
referencia cruzada (ej. Favorito -> Instrumento) se resuelve por valor en la capa de
aplicación, tal como la tesis resuelve PERFIL_INVERSOR <-> PESO_PERFIL."""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import DATABASE_URL_FINANCIERA, DATABASE_URL_NO_FINANCIERA

engine_financiera = create_engine(DATABASE_URL_FINANCIERA)
SessionFinanciera = sessionmaker(autocommit=False, autoflush=False, bind=engine_financiera)


class BaseFinanciera(DeclarativeBase):
    pass


engine_no_financiera = create_engine(DATABASE_URL_NO_FINANCIERA)
SessionNoFinanciera = sessionmaker(autocommit=False, autoflush=False, bind=engine_no_financiera)


class BaseNoFinanciera(DeclarativeBase):
    pass
