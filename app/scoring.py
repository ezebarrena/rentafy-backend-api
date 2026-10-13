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

Ajuste por horizonte (ver AJUSTE_HORIZONTE_ACTIVO y ajuste_horizonte() más abajo): además del
nudge de pesos, el Score suma una AFINIDAD entre el perfil y el tipo de activo (un conservador
busca LECAP/BONCAP/BONCER, no ONs ni bonos en dólares) y resta un DESCALCE entre el vencimiento y el
horizonte elegido (corto: el activo tiene que rendir dentro de ese período, así que es el más
restrictivo; largo: el menos restrictivo). Es un ajuste acotado que se aplica DESPUÉS de la
ponderación de los cuatro factores, nunca los reemplaza, y se puede apagar con la variable de
entorno SCORE_AJUSTE_HORIZONTE=0 (vuelve al Score de antes, sin tocar la base). Ver
rentafy-servicioIA/scripts/prototipo_horizonte.py para el prototipo con el que se eligieron los valores.

Con 2 o más de los 4 factores sin calcular, compute_score() ya no redistribuye el peso entre
los que quedan — devuelve None (ver MINIMO_FACTORES_AUSENTES_PARA_ANULAR). La redistribución
asume que lo que sobrevive sigue siendo representativo del instrumento; con solo 2 factores
mecánicos (Riesgo, Liquidez) y sin Rendimiento ni Estabilidad, esa suposición deja de valer.
"""

from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from . import config
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


# --- Ajuste por horizonte (afinidad perfil-tipo y calce de plazo). Valores de partida, ver rentafy-servicioIA/scripts/prototipo_horizonte.py ---
AJUSTE_HORIZONTE_ACTIVO = config.SCORE_AJUSTE_HORIZONTE
HORIZONTE_ANIOS: dict[PlazoInversion, float] = {"corto": 0.5, "mediano": 2.0, "largo": 5.0}  # horizonte representativo de cada plazo
TOPE_AFINIDAD = 10.0
MAX_RESTA_AJUSTE, PISO_AJUSTE = 25.0, 15.0
# Años mínimos de vida para que una afinidad POSITIVA rinda completa (por debajo, proporcional): LECAP/BONCER que vencen en semanas no son
# una opción para un horizonte largo aunque sean del tipo "favorito" del perfil.
MIN_ANIOS_AFINIDAD: dict[PlazoInversion, float] = {"corto": 0.0, "mediano": 0.5, "largo": 1.0}
# (puntos por año, tope) de vencer ANTES / DESPUÉS del horizonte. Corto: el más restrictivo (tiene que rendir dentro del período; vencer mucho
# después es riesgo de precio al vender). Largo: el menos restrictivo (no castiga vencer después; solo descuenta lo que vence en menos de 1 año).
DESCALCE: dict[PlazoInversion, dict[str, tuple[float, float]]] = {
    "corto": {"antes": (2.0, 4.0), "despues": (8.0, 20.0)},
    "mediano": {"antes": (4.0, 10.0), "despues": (5.0, 12.0)},
    "largo": {"antes": (0.0, 0.0), "despues": (0.0, 0.0)},
}
DESCUENTO_LARGO_POR_ANIO_QUE_FALTA = 8.0
AFINIDAD: dict[PerfilInversor, dict[PlazoInversion, dict[str, float]]] = {
    "conservador": {
        "corto": {"LECAP": 8, "BONCAP": 8, "BONCER": 8, "TAMAR": 3, "DUAL": 2, "Dólar Linked": -3, "Bono USD": -8, "ON": -8},
        "mediano": {"LECAP": 4, "BONCAP": 4, "BONCER": 8, "TAMAR": 3, "DUAL": 2, "Dólar Linked": -3, "Bono USD": -8, "ON": -8},
        "largo": {"BONCER": 8, "TAMAR": 3, "DUAL": 2, "Dólar Linked": -3, "Bono USD": -6, "ON": -6},
    },
    "moderado": {
        "corto": {"LECAP": 4, "BONCAP": 4, "BONCER": 4, "Bono USD": -3, "ON": -2},
        "mediano": {"BONCER": 2, "Bono USD": 1, "ON": 1},
        "largo": {"BONCER": 1, "Bono USD": 3, "ON": 2},
    },
    "agresivo": {
        "corto": {},
        "mediano": {"Bono USD": 1, "ON": 1},
        "largo": {"Bono USD": 4, "ON": 2},
    },
}


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


@dataclass(frozen=True)
class AjusteHorizonte:
    """Cuánto sumó la afinidad del perfil con el tipo de activo y cuánto restó el descalce de plazo, en puntos de Score
    (`descalce` se guarda positivo: es lo que se resta). `anios` = años desde la fecha del Score hasta el vencimiento."""

    clase: str
    anios: float
    afinidad: float
    descalce: float


def clase_instrumento(tipo: str, subtipo: str | None) -> str:
    """Clave de la tabla de afinidad: el subtipo si existe (BONCER, TAMAR, DUAL, Dólar Linked, Bono USD, Bono ARS), si no el tipo (ON, LECAP, BONCAP)."""
    return subtipo or tipo


def _descalce(anios: float, plazo: PlazoInversion) -> float:
    if plazo == "largo":
        return DESCUENTO_LARGO_POR_ANIO_QUE_FALTA * max(0.0, 1.0 - anios)
    d = anios - HORIZONTE_ANIOS[plazo]
    pendiente, tope = DESCALCE[plazo]["antes" if d < 0 else "despues"]
    return min(tope, abs(d) * pendiente)


def ajuste_horizonte(clase: str, anios: float, perfil: PerfilInversor, plazo: PlazoInversion) -> AjusteHorizonte:
    anios = max(0.0, anios)
    afinidad = max(-TOPE_AFINIDAD, min(TOPE_AFINIDAD, AFINIDAD[perfil][plazo].get(clase, 0.0)))
    minimo = MIN_ANIOS_AFINIDAD[plazo]
    if afinidad > 0 and minimo:
        afinidad *= min(1.0, anios / minimo)  # sin recorrido suficiente, la afinidad positiva se apaga gradualmente
    return AjusteHorizonte(clase=clase, anios=anios, afinidad=afinidad, descalce=_descalce(anios, plazo))


def ajuste_para(instrumento, fecha: date, perfil: PerfilInversor, plazo: PlazoInversion) -> AjusteHorizonte | None:
    """Ajuste de un instrumento (objeto con tipo, subtipo y vencimiento) a la fecha de su Score. None si el interruptor está apagado."""
    if not config.SCORE_AJUSTE_HORIZONTE or instrumento is None or instrumento.vencimiento is None:
        return None
    anios = (instrumento.vencimiento - fecha).days / 365.0
    return ajuste_horizonte(clase_instrumento(instrumento.tipo, instrumento.subtipo), anios, perfil, plazo)


def aplicar_ajuste(base: float, ajuste: AjusteHorizonte | None) -> float:
    """Score base + afinidad − descalce, sin que el ajuste reste más de MAX_RESTA_AJUSTE ni lleve el Score por debajo de PISO_AJUSTE
    (salvo que el base ya estuviera por debajo): un activo poco adecuado queda con Score bajo, pero no en 0–1."""
    if ajuste is None:
        return base
    return min(100.0, max(base + ajuste.afinidad - ajuste.descalce, base - MAX_RESTA_AJUSTE, min(base, PISO_AJUSTE)))


def score_base(
    rendimiento: float | None,
    riesgo: float,
    liquidez: float,
    estabilidad: float | None,
    perfil: PerfilInversor,
    plazo: PlazoInversion = "mediano",
    pesos_base: dict[PerfilInversor, PesosPerfil] | None = None,
) -> float | None:
    """Ponderación de los cuatro factores (sin redondear y sin el ajuste por horizonte). Ver compute_score()."""
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
    return score


def compute_score(
    rendimiento: float | None,
    riesgo: float,
    liquidez: float,
    estabilidad: float | None,
    perfil: PerfilInversor,
    plazo: PlazoInversion = "mediano",
    pesos_base: dict[PerfilInversor, PesosPerfil] | None = None,
    ajuste: AjusteHorizonte | None = None,
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
    en routers/instrumentos.py, que descartan los puntos con None en vez de fallar).

    `ajuste`: afinidad y descalce del instrumento (ver ajuste_para()); sin él, el Score es solo la ponderación de los factores.
    """
    base = score_base(rendimiento, riesgo, liquidez, estabilidad, perfil, plazo, pesos_base)
    if base is None:
        return None
    return round(aplicar_ajuste(base, ajuste))
