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
    estabilidad: float,
    perfil: PerfilInversor,
) -> int:
    """Aplica la ponderación del perfil sobre los cuatro factores ya calculados.

    Instrumentos sin Rendimiento calculable (TAMAR, DUAL, dólar-linked) redistribuyen el
    score entre los tres factores restantes en partes iguales (ver scoring.ts línea 19-30).
    """
    if rendimiento is None:
        return round((riesgo + liquidez + estabilidad) / 3)

    w = PESOS_PERFIL[perfil]
    score = w.rendimiento * rendimiento + w.riesgo * riesgo + w.liquidez * liquidez + w.estabilidad * estabilidad
    return round(score)


def score_label(score: float) -> str:
    if score >= 90:
        return "Excelente"
    if score >= 75:
        return "Muy bueno"
    if score >= 60:
        return "Bueno"
    if score >= 40:
        return "Moderado"
    return "Débil"
