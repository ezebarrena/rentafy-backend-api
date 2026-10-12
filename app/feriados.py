"""Feriados nacionales de Argentina, para que los jobs diarios no corran los días sin rueda.

Fuente: ArgentinaDatos (https://api.argentinadatos.com/v1/feriados/{año}), la misma API pública que ya usa el catálogo para el
riesgo país y los dólares. Se consulta una vez por año y se guarda en memoria (FERIADOS_VIGENCIA_SEG); si la API no responde se usa
lo último que se bajó o, si nunca se pudo, la lista de respaldo de este archivo. Nunca levanta: ante la duda un día cuenta como de
rueda (se prefiere correr un día de más a perder una corrida).

Los "puentes turísticos" (tipo `puente`) NO se descuentan: son días no laborables pero el mercado opera (en el historial de
cotizaciones hay rueda el 23/3 y el 10/7/2026, y ninguna en los feriados reales). Cualquier otro tipo (inamovible, trasladable…) sí.

Este archivo es idéntico en rentafy-backend-api y en rentafy-servicioIA: si se toca uno, se toca el otro.
"""

import json
import logging
import ssl
import threading
import time
import urllib.request
from datetime import date, timedelta

logger = logging.getLogger("rentafy.feriados")

URL_FERIADOS = "https://api.argentinadatos.com/v1/feriados/{anio}"
TIMEOUT_SEG = 5
FERIADOS_VIGENCIA_SEG = 12 * 3600  # un feriado nuevo (decreto) se levanta a más tardar al día siguiente
REINTENTO_TRAS_FALLA_SEG = 15 * 60

# Respaldo si la API no responde (sin los puentes). Hay que sumar el año siguiente antes de que empiece.
FERIADOS_RESPALDO: dict[int, frozenset[date]] = {
    2026: frozenset(
        date.fromisoformat(f)
        for f in (
            "2026-01-01", "2026-02-16", "2026-02-17", "2026-03-24", "2026-04-02", "2026-04-03", "2026-05-01", "2026-05-25",
            "2026-06-15", "2026-06-20", "2026-07-09", "2026-08-17", "2026-10-12", "2026-11-09", "2026-11-23", "2026-12-08",
            "2026-12-25",
        )
    )
}

_cache: dict[int, tuple[float, frozenset[date]]] = {}  # año -> (válido hasta, feriados), en time.monotonic()
_candado = threading.Lock()


def _contexto_ssl() -> ssl.SSLContext:
    """Los certificados del sistema; si está `certifi` se usan los suyos (el Python de python.org en macOS no trae los del sistema)."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def _descargar(anio: int) -> frozenset[date]:
    pedido = urllib.request.Request(URL_FERIADOS.format(anio=anio), headers={"User-Agent": "rentafy"})
    with urllib.request.urlopen(pedido, timeout=TIMEOUT_SEG, context=_contexto_ssl()) as respuesta:  # noqa: S310 — URL fija https
        datos = json.load(respuesta)
    if not isinstance(datos, list) or not datos:
        raise ValueError("respuesta vacía o con otro formato")
    return frozenset(date.fromisoformat(f["fecha"]) for f in datos if f.get("tipo") != "puente")


def feriados_del_anio(anio: int) -> frozenset[date]:
    ahora = time.monotonic()
    with _candado:
        guardado = _cache.get(anio)
        if guardado and ahora < guardado[0]:
            return guardado[1]
        try:
            feriados = _descargar(anio)
            _cache[anio] = (ahora + FERIADOS_VIGENCIA_SEG, feriados)
        except Exception as exc:  # noqa: BLE001 — la API es externa: cualquier falla cae al último valor conocido
            feriados = guardado[1] if guardado else FERIADOS_RESPALDO.get(anio, frozenset())
            logger.warning("No se pudieron consultar los feriados de %s (%s): se usa %s.", anio, exc,
                           "el último valor guardado" if guardado else "la lista de respaldo")
            _cache[anio] = (ahora + REINTENTO_TRAS_FALLA_SEG, feriados)
        return feriados


def es_feriado(fecha: date) -> bool:
    return fecha in feriados_del_anio(fecha.year)


def es_dia_de_rueda(fecha: date) -> bool:
    """Lunes a viernes que no es feriado nacional."""
    return fecha.weekday() < 5 and not es_feriado(fecha)


def es_primer_dia_de_rueda_de_la_semana(fecha: date) -> bool:
    """Para los jobs semanales (antes "los lunes"): corren el primer día hábil de la semana, o sea el martes si el lunes fue feriado."""
    return es_dia_de_rueda(fecha) and not any(es_dia_de_rueda(fecha - timedelta(days=k)) for k in range(1, fecha.weekday() + 1))
