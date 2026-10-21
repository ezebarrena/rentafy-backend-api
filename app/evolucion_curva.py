"""Evolución de la curva de rendimientos de un grupo: cómo estaba hoy, hace una semana y hace un mes (misma curva logarítmica TIR = a + b·ln(duration) de las
curvas por grupo), y si se "empinó" (los plazos largos subieron más que los cortos) o se "aplanó" (al revés). Es información histórica, no una recomendación.

El corte de "empinó/aplanó" usa dos plazos fijos (los de hoy: el 20 % más corto y el 20 % más largo del grupo) para comparar siempre lo mismo; la pendiente es la
diferencia de TIR entre ambos sobre la curva ajustada de cada fecha."""

import math
from datetime import date, timedelta
from typing import Optional

# Duration mínima (años) para que un punto entre en el ajuste: más corto, la TIR anualizada es ruido (mismo criterio que las curvas por grupo).
DURATION_MINIMA = 0.15
MINIMO_PUNTOS = 3
# Cambio de pendiente (en puntos porcentuales de TIR) por debajo del cual se dice que la curva "casi no cambió de forma".
UMBRAL_FORMA_PP = 0.25
# Días de tolerancia al buscar la fecha de comparación: se toma la rueda más cercana hacia atrás, como mucho este margen antes de la fecha pedida.
TOLERANCIA_DIAS = 5
COMPARACIONES = (("1 semana", 7), ("1 mes", 30))


def ajustar(puntos: list[tuple[float, float]]) -> Optional[tuple[float, float, float]]:
    """(a, b, r²) de TIR = a + b·ln(duration) por mínimos cuadrados; None si no se puede ajustar."""
    validos = [(d, t) for d, t in puntos if d >= DURATION_MINIMA]
    if len(validos) < MINIMO_PUNTOS:
        return None
    xs = [math.log(d) for d, _ in validos]
    ys = [t for _, t in validos]
    n = len(xs)
    xm, ym = sum(xs) / n, sum(ys) / n
    sxx = sum((x - xm) ** 2 for x in xs)
    syy = sum((y - ym) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
    b = sxy / sxx
    return ym - b * xm, b, sxy**2 / (sxx * syy)


def fecha_de_comparacion(fechas: list[date], hoy: date, dias: int) -> Optional[date]:
    """La rueda más cercana hacia atrás a `hoy - dias` (con hasta TOLERANCIA_DIAS de margen); None si no hay datos tan atrás."""
    objetivo = hoy - timedelta(days=dias)
    candidatas = [f for f in fechas if objetivo - timedelta(days=TOLERANCIA_DIAS) <= f <= objetivo]
    return max(candidatas) if candidatas else None


def _percentil(valores: list[float], p: float) -> float:
    v = sorted(valores)
    i = (len(v) - 1) * p
    lo, hi = math.floor(i), math.ceil(i)
    return v[lo] + (v[hi] - v[lo]) * (i - lo)


def _lectura(delta_pendiente: float) -> str:
    if delta_pendiente > UMBRAL_FORMA_PP:
        return "empinó"
    if delta_pendiente < -UMBRAL_FORMA_PP:
        return "aplanó"
    return "sin cambios"


def evolucion_de_grupo(por_fecha: dict[date, list[tuple[float, float]]], hoy: date, fechas_disponibles: list[date]) -> Optional[dict]:
    """Curvas de `hoy` y de las fechas de comparación para un grupo. `por_fecha` trae, por rueda, los (duration, TIR) de los instrumentos del grupo.
    None si hoy no se puede ajustar (el grupo no tiene instrumentos suficientes)."""
    ajuste_hoy = ajustar(por_fecha.get(hoy, []))
    if ajuste_hoy is None:
        return None
    durations = [d for d, _ in por_fecha[hoy] if d >= DURATION_MINIMA]
    corto, largo = _percentil(durations, 0.2), _percentil(durations, 0.8)
    if largo <= corto:
        return None
    d_min, d_max = min(durations), max(durations)

    def describir(etiqueta: str, fecha: date, ajuste: tuple[float, float, float], n: int) -> dict:
        a, b, r2 = ajuste
        f = lambda d: a + b * math.log(d)  # noqa: E731
        muestras = [d_min * (d_max / d_min) ** (i / 24) for i in range(25)]
        return {
            "etiqueta": etiqueta,
            "fecha": fecha.isoformat(),
            "n": n,
            "r2": round(r2, 3),
            "tirCorto": round(f(corto), 2),
            "tirLargo": round(f(largo), 2),
            "pendiente": round(f(largo) - f(corto), 2),
            "curva": [{"duration": round(d, 3), "tir": round(f(d), 2)} for d in muestras],
        }

    curvas = [describir("Hoy", hoy, ajuste_hoy, len(por_fecha[hoy]))]
    comparaciones = []
    for nombre, dias in COMPARACIONES:
        fecha = fecha_de_comparacion(fechas_disponibles, hoy, dias)
        ajuste = ajustar(por_fecha.get(fecha, [])) if fecha else None
        if fecha is None or ajuste is None:
            continue
        previa = describir(f"Hace {nombre}", fecha, ajuste, len(por_fecha[fecha]))
        curvas.append(previa)
        delta_pend = curvas[0]["pendiente"] - previa["pendiente"]
        nivel_hoy = (curvas[0]["tirCorto"] + curvas[0]["tirLargo"]) / 2
        nivel_previo = (previa["tirCorto"] + previa["tirLargo"]) / 2
        comparaciones.append(
            {
                "contra": previa["etiqueta"],
                "fecha": previa["fecha"],
                "cambioPendiente": round(delta_pend, 2),
                "cambioNivel": round(nivel_hoy - nivel_previo, 2),
                "cambioCorto": round(curvas[0]["tirCorto"] - previa["tirCorto"], 2),
                "cambioLargo": round(curvas[0]["tirLargo"] - previa["tirLargo"], 2),
                "lectura": _lectura(delta_pend),
            }
        )
    return {"durationCorta": round(corto, 2), "durationLarga": round(largo, 2), "curvas": curvas, "comparaciones": comparaciones}
