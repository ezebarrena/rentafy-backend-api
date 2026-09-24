"""Relleno de TIR para instrumentos donde compararfondos.com.ar no la reporta (ver ingest.py,
`tir=bond.get("tir")` — viene directo de la fuente, nunca calculada acá). No todos esos casos
son iguales: algunos (DUAL/TAMAR, tasa flotante) no tienen una TIR "verdadera" calculable con lo
que tenemos (ni cotización ni FlujoFondo alcanzan); para esos, la única opción real es tomarla
de un tercero que ya la calcule con datos que nosotros no tenemos (ej. tipo de cambio oficial
proyectado para Dólar Linked).

`FUENTES` es una lista simple de funciones `ticker -> TIR (%) | None`, probadas en orden hasta
que una devuelva un valor — agregar una segunda fuente el día de mañana es escribir una función
con esa misma firma y sumarla a la lista, nada más (no hace falta ningún registro ni config
nueva). No están "priorizadas" por calidad todavía porque hoy solo hay una.

Nunca pisa una TIR que ya vino de la fuente propia (ver `completar_tir_faltante`, filtra
`Cotizacion.tir.is_(None)`) — esto es estrictamente un relleno de huecos, no una fuente de
verdad alternativa para lo que compararfondos.com.ar ya reporta.
"""

import json
import logging
import re
from datetime import date

import requests
from sqlalchemy.orm import Session

from .models_financiera import Cotizacion, Instrumento

logger = logging.getLogger("rentafy.tir_externo")

BONISTAS_URL = "https://bonistas.com/bono-cotizacion-rendimiento-precio-hoy/{ticker}"
_NEXT_DATA_RE = re.compile(r'__NEXT_DATA__" type="application/json">(.*?)</script>')


def _obtener_tir_bonistas(ticker: str) -> float | None:
    """bonistas.com no tiene una API pública, pero su página es Next.js: el HTML trae un
    `<script id="__NEXT_DATA__">` con el estado completo de la página en JSON, incluida la TIR
    ya calculada — no hace falta parsear el DOM renderizado (fragil ante cualquier cambio de
    estilos), solo extraer y decodificar ese bloque. `tir_t0` (liquidación T+0, la que la
    página muestra como principal) no siempre está publicada para todos los bonos —
    `tir` (liquidación 24hs) es el fallback cuando falta. None si el ticker no existe en
    bonistas.com (redirige a la home, no da 404) o si cualquier otro paso falla — un tercero
    caído o con un ticker que no tiene no debe romper la corrida del resto del catálogo."""
    try:
        respuesta = requests.get(
            BONISTAS_URL.format(ticker=ticker),
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
            allow_redirects=False,
        )
        if respuesta.status_code != 200:
            return None

        match = _NEXT_DATA_RE.search(respuesta.text)
        if match is None:
            return None

        bono = json.loads(match.group(1))["props"]["pageProps"]["bondData"]["bond"]
        tir_fraccion = bono.get("tir_t0")
        if tir_fraccion is None:
            tir_fraccion = bono.get("tir")
        if tir_fraccion is None:
            return None

        # bonistas.com expresa la TIR como fracción (0.082 = 8.2%); nuestra convención (ver
        # Cotizacion.tir en toda la base, ej. TO26 ~29.70) es el número en puntos porcentuales.
        return round(float(tir_fraccion) * 100, 2)
    except (requests.RequestException, KeyError, ValueError, json.JSONDecodeError) as exc:
        logger.warning("bonistas.com: no se pudo obtener TIR de %s (%s)", ticker, exc)
        return None


# Orden de intento — ver docstring del módulo sobre cómo sumar una fuente nueva acá.
FUENTES = [_obtener_tir_bonistas]


def obtener_tir_externo(ticker: str) -> float | None:
    for fuente in FUENTES:
        tir = fuente(ticker)
        if tir is not None:
            return tir
    return None


def completar_tir_faltante(db: Session) -> dict:
    """Recorre las cotizaciones de HOY sin TIR (de instrumentos activos) e intenta completarla
    con `obtener_tir_externo`. Pensado para correr DESPUÉS del import diario de
    compararfondos.com.ar y ANTES del Motor de Scoring (ver scheduler.py) — así, cuando el
    scoring corre, ya encuentra la TIR rellena en los casos que tienen fuente externa."""
    hoy = date.today()
    filas = (
        db.query(Cotizacion)
        .join(Instrumento, Instrumento.ticker == Cotizacion.instrumento_ticker)
        .filter(Cotizacion.fecha == hoy, Cotizacion.tir.is_(None), Instrumento.activo.is_(True))
        .all()
    )

    completados = 0
    for fila in filas:
        tir = obtener_tir_externo(fila.instrumento_ticker)
        if tir is not None:
            fila.tir = tir
            completados += 1

    db.commit()
    return {"procesados": len(filas), "completados": completados, "sinFuente": len(filas) - completados}
