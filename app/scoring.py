"""Ponderación del Score por perfil de inversor (y, desde esta iteración, por plazo de
inversión).

Puerto 1:1 de rentafy-frontend/src/data/scoring.ts. El Servicio de IA (fuera del alcance de
este backend, ver README) calcula los cuatro factores de manera agnóstica al usuario; este
módulo aplica la ponderación del perfil solicitante, tal como especifica chapters/chapter04.tex
en "Ponderación de los factores según perfil de inversor".

Plazo de inversión (corto/mediano/largo, ver PlazoInversion en schemas.py): no reemplaza la
ponderación por perfil, la matiza — mismo principio de "ajuste, no reemplazo" que paridad/RSI
en el factor Rendimiento del Servicio de IA. Un horizonte corto no puede darse el lujo de
esperar a que un instrumento volátil rinda lo esperado, así que le corre peso a
riesgo/estabilidad; un horizonte largo puede tolerar más vaivén a cambio de mejor rendimiento
esperado, así que hace lo inverso. Cortes de años (ver PlazoInversion): corto ≤1 año,
mediano 1-3 años, largo >3 años.

Nota: estos cortes son hoy puramente descriptivos (se muestran en la UI de Perfil) — el
ajuste de pesos de abajo se aplica según el plazo que el usuario ELIGE, no según una
comparación automática entre el vencimiento de cada instrumento y esos cortes. Esa
comparación (penalizar un descalce plazo-vencimiento) quedó fuera de esta iteración a
propósito, ver análisis de factibilidad.
"""

from .schemas import PerfilInversor, PesosPerfil, PlazoInversion

# Hipótesis de partida (sujeta a validación por el subproceso de Entrenamiento del Servicio de
# IA, ver chapter04.tex). Corresponde a la entidad PESO_PERFIL del modelo v1.4.0.
PESOS_PERFIL: dict[PerfilInversor, PesosPerfil] = {
    "conservador": PesosPerfil(rendimiento=0.15, riesgo=0.30, liquidez=0.20, estabilidad=0.35),
    "moderado": PesosPerfil(rendimiento=0.27, riesgo=0.27, liquidez=0.20, estabilidad=0.26),
    "agresivo": PesosPerfil(rendimiento=0.60, riesgo=0.10, liquidez=0.15, estabilidad=0.15),
}

# Nudge fijo (puntos de peso, no puntos de Score) sobre los pesos del perfil elegido, antes de
# la redistribución por factores faltantes de más abajo. "mediano" no ajusta nada — es
# exactamente el comportamiento de antes de que existiera el plazo, para no romper a nadie que
# todavía no lo haya elegido explícitamente.
#
# Liquidez solo baja en "largo", nunca sube en "corto": conservador ya arranca en su techo
# deseado de 20% (ver PESOS_PERFIL), así que un nudge positivo en corto lo pasaría de ese
# techo para el único perfil donde liquidez es un valor tope, no un promedio a ajustar.
AJUSTE_PLAZO: dict[PlazoInversion, dict[str, float]] = {
    "corto": {"rendimiento": -0.05, "riesgo": 0.03, "liquidez": 0.0, "estabilidad": 0.02},
    "mediano": {"rendimiento": 0.0, "riesgo": 0.0, "liquidez": 0.0, "estabilidad": 0.0},
    "largo": {"rendimiento": 0.06, "riesgo": -0.03, "liquidez": -0.01, "estabilidad": -0.02},
}


def _pesos_ajustados(perfil: PerfilInversor, plazo: PlazoInversion) -> dict[str, float]:
    """Pesos del perfil con el nudge de plazo aplicado y renormalizados a suma 1 — el nudge
    desplaza peso relativo entre factores, no debe cambiar cuánto pesa el conjunto."""
    w = PESOS_PERFIL[perfil]
    ajuste = AJUSTE_PLAZO[plazo]
    base = {
        "rendimiento": max(0.0, w.rendimiento + ajuste["rendimiento"]),
        "riesgo": max(0.0, w.riesgo + ajuste["riesgo"]),
        "liquidez": max(0.0, w.liquidez + ajuste["liquidez"]),
        "estabilidad": max(0.0, w.estabilidad + ajuste["estabilidad"]),
    }
    total = sum(base.values())
    return {k: v / total for k, v in base.items()}


def compute_score(
    rendimiento: float | None,
    riesgo: float,
    liquidez: float,
    estabilidad: float | None,
    perfil: PerfilInversor,
    plazo: PlazoInversion = "mediano",
) -> int:
    """Aplica la ponderación del perfil (matizada por el plazo de inversión, ver AJUSTE_PLAZO)
    sobre los factores ya calculados.

    Instrumentos sin Rendimiento calculable (TAMAR, DUAL, dólar-linked) o sin Estabilidad
    calculable todavía (menos de 20 ruedas de historial de precio, ver Servicio de IA)
    redistribuyen el peso del factor faltante entre los presentes, en la MISMA proporción
    relativa que ya tenían (perfil + plazo) — no en partes iguales. Un promedio simple pisaría
    la ponderación elegida (ej. "conservador" volvería a pesar riesgo y rendimiento por igual),
    que es exactamente lo que no debe pasar: cuanto más factores falten, más se acerca esta
    fórmula a un promedio, pero nunca ignora la ponderación mientras quede más de un factor.
    """
    w = _pesos_ajustados(perfil, plazo)
    factores = {
        "rendimiento": (rendimiento, w["rendimiento"]),
        "riesgo": (riesgo, w["riesgo"]),
        "liquidez": (liquidez, w["liquidez"]),
        "estabilidad": (estabilidad, w["estabilidad"]),
    }
    presentes = [(valor, peso) for valor, peso in factores.values() if valor is not None]
    peso_total = sum(peso for _, peso in presentes)
    score = sum(valor * peso for valor, peso in presentes) / peso_total
    return round(score)


def score_label(score: float) -> str:
    """Misma escala de 4 tramos que el frontend (90/75/50, ver ScoreBadge.tsx)."""
    if score >= 90:
        return "Excelente"
    if score >= 75:
        return "Muy bueno"
    if score >= 50:
        return "Bueno"
    return "Regular"
