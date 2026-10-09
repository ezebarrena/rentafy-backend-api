"""Rendimiento total de un instrumento en una ventana de fechas: variación de precio MÁS lo cobrado en cupones y amortizaciones.

Es la misma medida (y las mismas protecciones) que usa la auditoría del Score en rentafy-servicioIA: un precio que salta más de
UMBRAL_SALTO_DIARIO de un día al otro sin un pago que lo explique se considera un dato dudoso y no se usa, y se exige historia
de precios al comienzo de la ventana. Todo el cálculo es puro (recibe listas, no toca la base) para poder probarlo aislado.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median

from . import referencias

VENTANAS_DIAS = {"7d": 7, "14d": 14, "1m": 30, "3m": 90, "6m": 180, "1y": 365}
# El precio de arranque puede ser de hasta estos días antes del inicio (fines de semana, feriados).
TOLERANCIA_INICIO_DIAS = 5
# Si el último precio es más viejo que esto respecto del fin de la ventana, el instrumento no está cotizando.
MAX_ANTIGUEDAD_FIN_DIAS = 7
UMBRAL_SALTO_DIARIO = 0.20
# Un movimiento de un día muy grande PARA ESE INSTRUMENTO que al día siguiente se revierte (al menos la mitad) es casi seguro
# un error del dato (un precio mal cargado que se corrige), no una caída real: no llega al umbral de arriba pero igual
# distorsiona el rendimiento, sobre todo si cae justo en el inicio o el fin de la ventana. "Muy grande" es relativo: una LECAP
# casi no se mueve de un día al otro (un 3% ya es raro) y un bono en dólares sí. El umbral es MULTIPLO_DE_LO_NORMAL veces el
# movimiento diario típico del instrumento en la ventana, con un piso y un tope.
PISO_SALTO_QUE_SE_REVIERTE = 0.015
TOPE_SALTO_QUE_SE_REVIERTE = 0.08
MULTIPLO_DE_LO_NORMAL = 8.0
FRACCION_QUE_SE_REVIERTE = 0.5
MAX_HUECO_DIAS = 5  # entre dos precios más separados que esto no se evalúa el salto (pudo pasar de todo)
MINIMO_PARA_MEDIANA = 3


@dataclass
class Rendimiento:
    estado: str  # ok | sin_historia | dato_dudoso | sin_dolar
    retorno: float | None = None
    desde: date | None = None
    hasta: date | None = None


def _vigente(precios: list[tuple[date, float]], fecha: date) -> tuple[date, float] | None:
    ultimo = None
    for f, p in precios:
        if f > fecha:
            break
        ultimo = (f, p)
    return ultimo


def calcular(
    precios: list[tuple[date, float]],
    flujos: list[tuple[date, float]],
    inicio: date,
    fin: date,
    mep: list[tuple[date, float]] | None = None,
) -> Rendimiento:
    """`precios`: (fecha, precio) ordenados, sin los marcados como "stale". `flujos`: (fecha, importe por unidad) de cupones y
    amortizaciones. Si se pasa `mep` (serie del dólar), el rendimiento se calcula en PESOS: el precio y cada pago en dólares se
    convierten con el MEP de su fecha. Sin `mep`, el rendimiento queda en la moneda del instrumento."""
    final = _vigente(precios, fin)
    if final is None or (fin - final[0]).days > MAX_ANTIGUEDAD_FIN_DIAS:
        return Rendimiento("sin_historia")
    arranque = _vigente(precios, inicio)
    if arranque is None or (inicio - arranque[0]).days > TOLERANCIA_INICIO_DIAS or arranque[0] >= final[0]:
        return Rendimiento("sin_historia")
    f0, p0 = arranque
    f1, p1 = final

    pagos: dict[date, float] = {}
    for f, importe in flujos:
        if f0 < f <= f1:
            pagos[f] = pagos.get(f, 0.0) + importe

    # Salto diario sospechoso (con el pago del día incluido, para que un cupón no se lea como una suba). Se mira también el
    # precio anterior al arranque: un error justo en el primer día de la ventana se ve en el movimiento que lo precede.
    previos = [(f, p) for f, p in precios if f < f0]
    tramo = ([previos[-1]] if previos else []) + [(f, p) for f, p in precios if f0 <= f <= f1]
    cambios: list[float | None] = []
    for (fa, pa), (fb, pb) in zip(tramo, tramo[1:]):
        ok = (fb - fa).days <= MAX_HUECO_DIAS and pa > 0
        cambios.append((pb + pagos.get(fb, 0.0) - pa) / pa if ok else None)
    validos = [abs(c) for c in cambios if c is not None]
    tipico = median(validos) if validos else 0.0
    umbral_reversion = min(max(PISO_SALTO_QUE_SE_REVIERTE, MULTIPLO_DE_LO_NORMAL * tipico), TOPE_SALTO_QUE_SE_REVIERTE)
    for k, c in enumerate(cambios):
        if c is None:
            continue
        if abs(c) > UMBRAL_SALTO_DIARIO:
            return Rendimiento("dato_dudoso", None, f0, f1)
        siguiente = cambios[k + 1] if k + 1 < len(cambios) else None
        if (
            siguiente is not None
            and abs(c) > umbral_reversion
            and c * siguiente < 0
            and abs(siguiente) >= FRACCION_QUE_SE_REVIERTE * abs(c)
        ):
            return Rendimiento("dato_dudoso", None, f0, f1)

    if mep is None:
        valor_final = p1 + sum(pagos.values())
        return Rendimiento("ok", valor_final / p0 - 1, f0, f1)

    m0, m1 = referencias.valor_en(mep, f0), referencias.valor_en(mep, f1)
    cobrado = []
    for f, importe in pagos.items():
        m = referencias.valor_en(mep, f)
        if m is None:
            return Rendimiento("sin_dolar", None, f0, f1)
        cobrado.append(importe * m)
    if m0 is None or m1 is None:
        return Rendimiento("sin_dolar", None, f0, f1)
    return Rendimiento("ok", (p1 * m1 + sum(cobrado)) / (p0 * m0) - 1, f0, f1)


def mediana(valores: list[float]) -> float | None:
    """Mediana, o None si hay menos de MINIMO_PARA_MEDIANA valores (con tan pocos no representa al tipo)."""
    return float(median(valores)) if len(valores) >= MINIMO_PARA_MEDIANA else None


def inicio_de_ventana(fin: date, dias: int) -> date:
    return fin - timedelta(days=dias)
