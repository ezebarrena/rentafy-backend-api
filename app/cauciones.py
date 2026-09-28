"""Cauciones en pesos: a diferencia de un bono, no tienen ticker fijo ni vencimiento fijo — son
un plazo (1, 7, 14, 30 días) que se renueva desde HOY cada vez que se toma o coloca una caución
nueva. Por eso no viven en Instrumento/Cotizacion (pensadas para algo con ticker+vencimiento
propios), sino en su propia tabla (ver models_financiera.py, Caucion).

Fuente: Rava Bursátil (rava.com/perfil/CAUCION%20{N}D) — BYMA es la fuente "oficial" pero su
market data en vivo es comercial, no pública (su página de Cauciones es solo descriptiva).
compararfondos.com.ar, Data912 y ArgentinaDatos no publican cauciones (verificado a mano antes
de escribir esto: 404 en los tres para cualquier variante de "caucion"/"repos").

Rava solo cotiza 4 plazos con mercado real y estandarizado: 1D, 7D, 14D y 30D — se probaron
21D, 28D, 60D, 90D y 120D a mano y ninguno existe como ticker ("Especie no encontrada"). El
rango "de 1 a 120 días hábiles" que publicita BYMA es lo que dos partes PODRÍAN pactar
bilateralmente, no lo que cotiza con liquidez real todos los días. Para 21D (interpolación dentro
del rango real 14D-30D) y 60D (extrapolación más allá del último punto real, más incierta que
interpolar) se arma el punto a partir de esos 4 reales — ver `_interpolar_lineal`.
"""

import re
from datetime import date

import requests
from sqlalchemy.orm import Session

from .models_financiera import Caucion

RAVA_URL = "https://www.rava.com/perfil/CAUCION%20{plazo}D"
PLAZOS_REALES = [1, 7, 14, 30]
PLAZOS_CALCULADOS = [21, 60]

_PRECIO_RE = re.compile(r'<div class="p2-price">\s*([\d.,]+)\s*</div>')


def _obtener_tasa_rava(plazo_dias: int) -> float | None:
    """None si Rava no cotiza ese plazo (ticker inexistente, ver docstring del módulo) o si
    cualquier otro paso falla — un plazo sin mercado real o la fuente caída no debe tumbar el
    resto del import."""
    try:
        respuesta = requests.get(
            RAVA_URL.format(plazo=plazo_dias),
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        if respuesta.status_code != 200:
            return None
        match = _PRECIO_RE.search(respuesta.text)
        if match is None:
            return None
        # Rava usa coma decimal ("20,10"), estilo argentino — sin esto psycopg2 recibiría un
        # string, no un número.
        return float(match.group(1).replace(".", "").replace(",", "."))
    except requests.RequestException:
        return None


def _interpolar_lineal(plazos_reales: dict[int, float], plazo_objetivo: int) -> float | None:
    """Interpola (objetivo entre dos puntos reales) o extrapola (objetivo más allá del último
    punto real) linealmente sobre tasa vs. plazo en días — mismo espíritu que la curva
    TIR-vs-duration de los bonos, pero con una recta entre los 2 puntos reales más cercanos en
    vez de una regresión sobre muchos: acá solo hay 4 puntos reales, no alcanza para más que eso.
    None si hay menos de 2 tasas reales disponibles ese día (no hay con qué trazar una recta)."""
    dias = sorted(plazos_reales)
    if len(dias) < 2:
        return None

    if plazo_objetivo >= dias[-1]:
        x0, x1 = dias[-2], dias[-1]  # extrapolación con la pendiente del último tramo real
    elif plazo_objetivo <= dias[0]:
        x0, x1 = dias[0], dias[1]  # extrapolación con la pendiente del primer tramo real
    else:
        x0 = max(d for d in dias if d <= plazo_objetivo)
        x1 = min(d for d in dias if d >= plazo_objetivo)
        if x0 == x1:
            return plazos_reales[x0]

    y0, y1 = plazos_reales[x0], plazos_reales[x1]
    pendiente = (y1 - y0) / (x1 - x0)
    return round(y0 + pendiente * (plazo_objetivo - x0), 2)


def importar_cauciones(db: Session) -> dict:
    """RF-07 (cadencia diaria): trae las 4 tasas reales de Rava y calcula 21D/60D a partir de
    ellas. Idempotente por (fecha, plazo_dias) — correr esto dos veces el mismo día actualiza
    en vez de duplicar, mismo criterio que ingest.py usa para Cotizacion."""
    hoy = date.today()
    tasas_reales: dict[int, float] = {}
    for plazo in PLAZOS_REALES:
        tasa = _obtener_tasa_rava(plazo)
        if tasa is not None:
            tasas_reales[plazo] = tasa

    filas_existentes = {fila.plazo_dias: fila for fila in db.query(Caucion).filter(Caucion.fecha == hoy).all()}

    def _guardar(plazo: int, tna: float, estimado: bool) -> None:
        fila = filas_existentes.get(plazo)
        if fila is None:
            fila = Caucion(fecha=hoy, plazo_dias=plazo)
            db.add(fila)
        fila.tna = tna
        fila.estimado = estimado

    for plazo, tasa in tasas_reales.items():
        _guardar(plazo, tasa, estimado=False)

    calculados = 0
    for plazo in PLAZOS_CALCULADOS:
        tasa = _interpolar_lineal(tasas_reales, plazo)
        if tasa is None:
            continue
        _guardar(plazo, tasa, estimado=True)
        calculados += 1

    db.commit()
    return {
        "fecha": hoy.isoformat(),
        "plazosReales": sorted(tasas_reales.keys()),
        "plazosCalculados": calculados,
        "guardados": len(tasas_reales) + calculados,
    }
