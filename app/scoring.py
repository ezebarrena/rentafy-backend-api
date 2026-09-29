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

Con 2 o más de los 4 factores sin calcular, compute_score() ya no redistribuye el peso entre
los que quedan — devuelve None (ver MINIMO_FACTORES_AUSENTES_PARA_ANULAR). La redistribución
asume que lo que sobrevive sigue siendo representativo del instrumento; con solo 2 factores
mecánicos (Riesgo, Liquidez) y sin Rendimiento ni Estabilidad, esa suposición deja de valer.
"""

from sqlalchemy.orm import Session

from .models_financiera import Modelo, PesoPerfil
from .schemas import PerfilInversor, PesosPerfil, PlazoInversion

# Hipótesis de partida (sujeta a validación por el subproceso de Entrenamiento del Servicio de
# IA, ver chapter04.tex). Corresponde a la entidad PESO_PERFIL del modelo v1.4.0. Es el punto de
# partida que entrenamiento.py nudgea contra el backtest semanal (ver
# rentafy-servicioIA/app/entrenamiento.py, TASA_APRENDIZAJE_EMPIRICO) y publica en la tabla
# PesoPerfil — pesos_vigentes() de abajo lee ESA tabla; esto de acá es solo el fallback para
# cuando todavía no hay ninguna fila publicada (Servicio de IA nunca corrió sobre esta base).
PESOS_PERFIL: dict[PerfilInversor, PesosPerfil] = {
    "conservador": PesosPerfil(rendimiento=0.15, riesgo=0.30, liquidez=0.20, estabilidad=0.35),
    "moderado": PesosPerfil(rendimiento=0.27, riesgo=0.27, liquidez=0.20, estabilidad=0.26),
    "agresivo": PesosPerfil(rendimiento=0.60, riesgo=0.10, liquidez=0.15, estabilidad=0.15),
}


def pesos_vigentes(db: Session) -> dict[PerfilInversor, PesosPerfil]:
    """Pesos realmente vigentes: los que el Servicio de IA publicó para el Modelo activo,
    después de nudgearlos contra el backtest de auditoria.py (ver su docstring) — no la
    hipótesis de partida estática. Se resuelve UNA vez por request (ver routers/*.py) y se pasa
    a compute_score(), no una consulta por instrumento.

    Cae a PESOS_PERFIL si todavía no hay ninguna fila publicada (entorno nuevo, o el Servicio
    de IA nunca corrió contra esta base) — mismo criterio defensivo que el resto del sistema
    ante datos faltantes: nunca romper, degradar a la hipótesis de partida."""
    filas = db.query(PesoPerfil).join(Modelo, Modelo.id == PesoPerfil.modelo_id).filter(Modelo.activo.is_(True)).all()
    pesos = {
        fila.perfil: PesosPerfil(
            rendimiento=fila.w_rendimiento, riesgo=fila.w_riesgo, liquidez=fila.w_liquidez, estabilidad=fila.w_estabilidad
        )
        for fila in filas
        if fila.perfil in PESOS_PERFIL
    }
    return pesos if len(pesos) == len(PESOS_PERFIL) else PESOS_PERFIL

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


def _pesos_ajustados(perfil: PerfilInversor, plazo: PlazoInversion, pesos_base: dict[PerfilInversor, PesosPerfil]) -> dict[str, float]:
    """Pesos del perfil (ya resueltos por el llamador, ver pesos_vigentes) con el nudge de
    plazo aplicado y renormalizados a suma 1 — el nudge desplaza peso relativo entre factores,
    no debe cambiar cuánto pesa el conjunto."""
    w = pesos_base[perfil]
    ajuste = AJUSTE_PLAZO[plazo]
    base = {
        "rendimiento": max(0.0, w.rendimiento + ajuste["rendimiento"]),
        "riesgo": max(0.0, w.riesgo + ajuste["riesgo"]),
        "liquidez": max(0.0, w.liquidez + ajuste["liquidez"]),
        "estabilidad": max(0.0, w.estabilidad + ajuste["estabilidad"]),
    }
    total = sum(base.values())
    return {k: v / total for k, v in base.items()}


# A partir de cuántos factores AUSENTES el Score deja de calcularse (ver docstring de
# compute_score): con 1 solo factor faltante, los 3 restantes todavía representan una lectura
# razonable del instrumento bajo la ponderación elegida. Con 2 o más, lo que sobrevive suele
# ser lo más MECÁNICO (Riesgo por duration, Liquidez casi siempre calculable) y no lo que mide
# atractivo/estabilidad real — caso real detectado: una ON casi sin operar (BF40D, 17 de 18
# días con precio congelado) quedaba con Rendimiento y Estabilidad en None tras excluir esos
# días congelados de las ventanas móviles (ver rentafy-servicioIA), y el Score REDISTRIBUIDO
# subía 14-21 puntos según el perfil (Riesgo, alto por duration corta, pasaba a pesar más) —
# exactamente lo contrario de lo que debería transmitir un instrumento que en la práctica no se
# puede operar con confianza.
MINIMO_FACTORES_AUSENTES_PARA_ANULAR = 2


def compute_score(
    rendimiento: float | None,
    riesgo: float,
    liquidez: float,
    estabilidad: float | None,
    perfil: PerfilInversor,
    plazo: PlazoInversion = "mediano",
    pesos_base: dict[PerfilInversor, PesosPerfil] | None = None,
) -> int | None:
    """Aplica la ponderación del perfil (matizada por el plazo de inversión, ver AJUSTE_PLAZO)
    sobre los factores ya calculados.

    `pesos_base`: pesos ya resueltos por el llamador vía pesos_vigentes(db) — se recibe
    resuelto, no se consulta la base acá, para no repetir la misma query una vez por
    instrumento en un listado. Por defecto (sin threadear la base, ej. tests o scoring-historico
    de un solo ticker) cae a la hipótesis de partida estática PESOS_PERFIL.

    Instrumentos sin Rendimiento calculable (TAMAR, DUAL, dólar-linked) o sin Estabilidad
    calculable todavía (menos de 20 ruedas de historial de precio, ver Servicio de IA)
    redistribuyen el peso del factor faltante entre los presentes, en la MISMA proporción
    relativa que ya tenían (perfil + plazo) — no en partes iguales. Un promedio simple pisaría
    la ponderación elegida (ej. "conservador" volvería a pesar riesgo y rendimiento por igual),
    que es exactamente lo que no debe pasar: cuanto más factores falten, más se acerca esta
    fórmula a un promedio.

    Con MINIMO_FACTORES_AUSENTES_PARA_ANULAR o más factores faltantes, la redistribución deja
    de ser confiable (ver esa constante) y el Score directamente no se calcula — None, igual
    criterio que ya usan Rendimiento/Estabilidad para "no hay suficiente dato" en vez de
    inventar un número. El llamador decide qué mostrar en ese caso (ver schemas.py, `score:
    Optional[float]`; y `scoring_historico`/`instrumentos_consistentes`/`instrumentos_en_alza`
    en routers/instrumentos.py, que descartan los puntos con None en vez de fallar)."""
    w = _pesos_ajustados(perfil, plazo, pesos_base or PESOS_PERFIL)
    factores = {
        "rendimiento": (rendimiento, w["rendimiento"]),
        "riesgo": (riesgo, w["riesgo"]),
        "liquidez": (liquidez, w["liquidez"]),
        "estabilidad": (estabilidad, w["estabilidad"]),
    }
    presentes = [(valor, peso) for valor, peso in factores.values() if valor is not None]
    if len(factores) - len(presentes) >= MINIMO_FACTORES_AUSENTES_PARA_ANULAR:
        return None
    peso_total = sum(peso for _, peso in presentes)
    score = sum(valor * peso for valor, peso in presentes) / peso_total
    return round(score)
