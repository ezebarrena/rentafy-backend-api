"""Ponderación del Score por perfil de inversor.

Puerto 1:1 de rentafy-frontend/src/data/scoring.ts. El Servicio de IA (fuera del alcance de
este backend, ver README) calcula los cuatro factores de manera agnóstica al usuario; este
módulo aplica la ponderación del perfil solicitante, tal como especifica chapters/chapter04.tex
en "Ponderación de los factores según perfil de inversor".
"""

from .schemas import PerfilInversor, PesosPerfil

# Hipótesis de partida (sujeta a validación por el subproceso de Entrenamiento del Servicio de
# IA, ver chapter04.tex). Corresponde a la entidad PESO_PERFIL del modelo v1.4.0.
PESOS_PERFIL: dict[PerfilInversor, PesosPerfil] = {
    "conservador": PesosPerfil(rendimiento=0.15, riesgo=0.30, liquidez=0.20, estabilidad=0.35),
    "moderado": PesosPerfil(rendimiento=0.25, riesgo=0.25, liquidez=0.25, estabilidad=0.25),
    "agresivo": PesosPerfil(rendimiento=0.55, riesgo=0.10, liquidez=0.20, estabilidad=0.15),
}


def compute_score(
    rendimiento: float | None,
    riesgo: float,
    liquidez: float,
    estabilidad: float | None,
    perfil: PerfilInversor,
) -> int:
    """Aplica la ponderación del perfil sobre los factores ya calculados.

    Instrumentos sin Rendimiento calculable (TAMAR, DUAL, dólar-linked) o sin Estabilidad
    calculable todavía (menos de 20 ruedas de historial de precio, ver Servicio de IA)
    redistribuyen el peso del factor faltante entre los presentes, en la MISMA proporción
    relativa que ya tenían en el perfil — no en partes iguales. Un promedio simple pisaría la
    ponderación del perfil (ej. "conservador" volvería a pesar riesgo y rendimiento por igual),
    que es exactamente lo que no debe pasar: cuanto más factores falten, más se acerca esta
    fórmula a un promedio, pero nunca ignora el perfil mientras quede más de un factor.
    """
    w = PESOS_PERFIL[perfil]
    factores = {
        "rendimiento": (rendimiento, w.rendimiento),
        "riesgo": (riesgo, w.riesgo),
        "liquidez": (liquidez, w.liquidez),
        "estabilidad": (estabilidad, w.estabilidad),
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
