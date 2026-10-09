"""Series de referencia para comparar rendimientos: inflación (vía UVA), dólar MEP y tasa de plazo fijo.

Fuente: ArgentinaDatos (https://argentinadatos.com, API pública, la misma familia de fuentes que ya usa el catálogo). Se
descargan una vez por día (scheduler.py) y se guardan en `series_referencia`, así ninguna pantalla depende de que la API
externa esté disponible en ese momento. Si la tabla está vacía (primer uso), la primera consulta las descarga.

- uva: valor diario del UVA, que se ajusta por inflación (CER): su variación entre dos fechas es la inflación del período.
- mep: dólar bolsa, cotización de venta, en pesos.
- tasa_pf: TNA (%) de depósitos a plazo fijo a 30 días. Rendimiento del período ≈ TNA × días / 365.
"""

import logging
from datetime import date, timedelta

import requests
from sqlalchemy.orm import Session

from .models_financiera import SerieReferencia

logger = logging.getLogger("rentafy.referencias")

BASE = "https://api.argentinadatos.com/v1"
# nombre -> (ruta, campo con el valor)
FUENTES = {
    "uva": ("/finanzas/indices/uva", "valor"),
    "mep": ("/cotizaciones/dolares/bolsa", "venta"),
    "tasa_pf": ("/finanzas/tasas/depositos30Dias", "valor"),
}
DIAS_HISTORIA = 500
TIMEOUT_SEGUNDOS = 25


def _descargar(nombre: str) -> list[tuple[date, float]]:
    ruta, campo = FUENTES[nombre]
    respuesta = requests.get(BASE + ruta, timeout=TIMEOUT_SEGUNDOS)
    respuesta.raise_for_status()
    desde = date.today() - timedelta(days=DIAS_HISTORIA)
    filas = []
    for fila in respuesta.json():
        try:
            f = date.fromisoformat(fila["fecha"])
            v = float(fila[campo])
        except (KeyError, TypeError, ValueError):
            continue
        if f >= desde and v > 0:
            filas.append((f, v))
    return sorted(filas)


def actualizar(db: Session) -> dict[str, int]:
    """Descarga las tres series y guarda las fechas nuevas (idempotente). Una serie que falla no impide actualizar las
    otras: devuelve cuántas filas nuevas entraron por serie (0 si falló o no había nada nuevo)."""
    nuevas: dict[str, int] = {}
    for nombre in FUENTES:
        try:
            filas = _descargar(nombre)
        except Exception as exc:  # noqa: BLE001 — se registra y se sigue con las demás
            logger.warning("No se pudo descargar la serie «%s»: %s", nombre, exc)
            nuevas[nombre] = 0
            continue
        existentes = {f for (f,) in db.query(SerieReferencia.fecha).filter(SerieReferencia.nombre == nombre)}
        a_guardar = [SerieReferencia(nombre=nombre, fecha=f, valor=v) for f, v in filas if f not in existentes]
        db.add_all(a_guardar)
        db.commit()
        nuevas[nombre] = len(a_guardar)
    # Se descartan las fechas que quedaron más viejas que la historia que se conserva.
    db.query(SerieReferencia).filter(SerieReferencia.fecha < date.today() - timedelta(days=DIAS_HISTORIA)).delete()
    db.commit()
    return nuevas


def asegurar(db: Session) -> None:
    """Si todavía no hay ninguna serie guardada (primer uso), las descarga ahora. No hace nada en el uso normal."""
    if db.query(SerieReferencia).first() is None:
        actualizar(db)


def cargar(db: Session, nombre: str, desde: date) -> list[tuple[date, float]]:
    """Serie ordenada por fecha, desde `desde` menos unos días (para poder tomar el último valor anterior al inicio)."""
    filas = (
        db.query(SerieReferencia.fecha, SerieReferencia.valor)
        .filter(SerieReferencia.nombre == nombre, SerieReferencia.fecha >= desde - timedelta(days=10))
        .order_by(SerieReferencia.fecha)
        .all()
    )
    return [(f, v) for f, v in filas]


def valor_en(serie: list[tuple[date, float]], fecha: date) -> float | None:
    """Último valor de la serie con fecha <= `fecha` (el valor vigente ese día), o None si la serie empieza después."""
    vigente = None
    for f, v in serie:
        if f > fecha:
            break
        vigente = v
    return vigente


def variacion(serie: list[tuple[date, float]], inicio: date, fin: date) -> float | None:
    """Variación porcentual (como fracción) entre el valor vigente al inicio y al fin. Sirve para el UVA (inflación) y el MEP."""
    a, b = valor_en(serie, inicio), valor_en(serie, fin)
    if a is None or b is None or a <= 0:
        return None
    return b / a - 1


def rendimiento_plazo_fijo(serie_tna: list[tuple[date, float]], inicio: date, fin: date) -> float | None:
    """Lo que habría rendido un plazo fijo en el período: TNA promedio × días / 365 (interés simple, suficiente para
    ventanas de hasta unos meses)."""
    dias = (fin - inicio).days
    tramo = [v for f, v in serie_tna if inicio - timedelta(days=10) <= f <= fin]
    if dias <= 0 or not tramo:
        return None
    return (sum(tramo) / len(tramo)) / 100.0 * dias / 365.0
