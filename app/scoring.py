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

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy import true
from sqlalchemy.orm import Session

from . import config
from .models_financiera import PLACEHOLDER_MODELO_ID, Modelo, PesoPerfil, Scoring
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


# --- Ajuste por horizonte (afinidad perfil-tipo y calce de plazo). Valores de partida, ver rentafy-servicioIA/scripts/escenarios_horizonte.py (v2) ---
AJUSTE_HORIZONTE_ACTIVO = config.SCORE_AJUSTE_HORIZONTE
# Banda de años al vencimiento que cubre cada horizonte (cortes de PlazoInversion: corto ≤1 año, mediano 1-3, largo >3). Dentro de la banda no hay descalce:
# un activo de 1 año calza con un horizonte corto y con uno mediano. En "largo" no hay techo: puede entrar cualquier plazo.
BANDA_ANIOS: dict[PlazoInversion, tuple[float, float]] = {"corto": (0.0, 1.0), "mediano": (1.0, 3.0), "largo": (1.0, 99.0)}
TOPE_AFINIDAD = 10.0
# El ajuste no resta más de MAX_RESTA_AJUSTE puntos ni deja el Score por debajo de FRACCION_PISO × el Score base: un activo poco adecuado queda con Score bajo
# pero no en 0–1, y el piso es PROPORCIONAL (no plano) para que activos distintos no empaten en el mismo número y se pierda el orden.
MAX_RESTA_AJUSTE, FRACCION_PISO = 25.0, 0.5
# Años mínimos de vida para que una afinidad POSITIVA rinda completa (por debajo, proporcional): LECAP/BONCER que vencen en semanas no son
# una opción para un horizonte mediano o largo aunque sean del tipo "favorito" del perfil.
MIN_ANIOS_AFINIDAD: dict[PlazoInversion, float] = {"corto": 0.0, "mediano": 1.0, "largo": 1.0}
# (puntos por año fuera de la banda, tope) de vencer ANTES (riesgo de reinversión) / DESPUÉS (riesgo de precio al vender) de la banda del horizonte.
# Corto: no castiga vencer antes y es el más restrictivo con lo que vence después (tiene que rendir dentro del período). Largo: no castiga vencer después;
# lo que vence en menos de 1 año se descuenta (DESCUENTO_LARGO_*), porque no tiene recorrido para un horizonte largo. El descuento por vencer antes es FUERTE a propósito (mediano 24 por año con
# tope 24; largo 30 con tope 24): el Score base de lo que está a semanas de vencer es alto de forma mecánica (el precio ya casi no se mueve) y con un descuento chico quedaba en el cuartil superior
# del conservador y el moderado de largo plazo, justo lo que el producto no quiere recomendar (verificar_scores_integral.py, control B13).
DESCALCE: dict[PlazoInversion, dict[str, tuple[float, float]]] = {
    "corto": {"antes": (0.0, 0.0), "despues": (6.0, 15.0)},
    "mediano": {"antes": (24.0, 24.0), "despues": (3.0, 10.0)},
    "largo": {"antes": (0.0, 0.0), "despues": (0.0, 0.0)},
}
DESCUENTO_LARGO_PUNTOS_POR_ANIO_QUE_FALTA, DESCUENTO_LARGO_TOPE = 30.0, 24.0
# Quien tolera más riesgo de precio sufre menos por vencer después de su horizonte: el agresivo de corto plazo "puede experimentar con más cosas".
MULT_DESCALCE_DESPUES: dict[PerfilInversor, float] = {"conservador": 1.0, "moderado": 0.8, "agresivo": 0.5}
# Afinidad (puntos de Score, antes de acotar a ±TOPE_AFINIDAD) por perfil, plazo y clase de activo. Lo que no figura vale 0 A PROPÓSITO: ninguna inclinación, decide el Score.
# Criterio de cada clase (decisión de producto, no estimación estadística; se midió con scripts/evaluar_ajuste_horizonte.py del servicio de IA):
#   LECAP / BONCAP  tasa fija en pesos de corto plazo: el conservador busca eso para ganarle al plazo fijo; para quien invierte a mediano o largo plazo solo
#                   cuentan como parking (vencen antes del horizonte), y el agresivo no los busca.
#   BONCER          ajusta por inflación: cobertura natural del conservador y, en menor medida, del moderado.
#   TAMAR / DUAL    tasa variable o mixta en pesos: acompañan a la inflación sin la duration de un bono fijo; sirven al conservador y, para 1 a 3 años, al moderado.
#   Dólar Linked    cubre contra el dólar, no contra la inflación: el conservador no lo busca, el moderado lo toma como cobertura, el agresivo "experimenta" con tipo de cambio.
#   Bono ARS        tasa fija en pesos de plazo medio/largo: es una apuesta a que la tasa baje y se la juega con duration; el conservador la evita, el agresivo la busca a largo plazo.
#   Bono USD / ON   riesgo de precio y de emisor en dólares: castigo al conservador y al moderado de corto plazo (se atenúa si el Riesgo es bajo, ver atenuacion_por_calidad),
#                   premio al agresivo de largo plazo.
AFINIDAD: dict[PerfilInversor, dict[PlazoInversion, dict[str, float]]] = {
    # Conservador de CORTO plazo: letras y activos que ajustan por inflación (ganarle al plazo fijo con poco riesgo); en dólares y Bono ARS se castiga.
    # En MEDIANO y LARGO no hay castigo por tipo ni moneda (decisión del 13/10): decide la calidad del activo (sus factores y su posición en los percentiles de su perfil).
    "conservador": {
        "corto": {"LECAP": 10, "BONCAP": 10, "BONCER": 10, "TAMAR": 4, "DUAL": 3, "Dólar Linked": -2, "Bono ARS": -3, "Bono USD": -4, "ON": -4},
        "mediano": {"LECAP": 5, "BONCAP": 5, "BONCER": 10, "TAMAR": 4, "DUAL": 3},
        "largo": {"BONCER": 10, "TAMAR": 4, "DUAL": 3, "Bono USD": 2, "ON": 2},
    },
    # Moderado: término medio. De corto plazo solo se inclina a las letras y a la cobertura de inflación; en MEDIANO no tiene ninguna inclinación por tipo ni moneda (pesos o dólares
    # por igual): decide la calidad del activo y su posición en los percentiles de su perfil. Sin castigos en ningún horizonte.
    # LARGO (más de 3 años): premio leve a los dólares, porque a ese plazo el inversor termina apuntando a dólares por el país y la inflación (decisión del 13/10), SIN rechazar
    # las opciones en pesos: ninguna clase en pesos recibe afinidad negativa en largo plazo, en ningún perfil.
    "moderado": {
        "corto": {"LECAP": 4, "BONCAP": 4, "BONCER": 4, "TAMAR": 2, "DUAL": 1, "Dólar Linked": 1},
        "mediano": {},
        "largo": {"Bono USD": 3, "ON": 2},
    },
    "agresivo": {
        "corto": {"Dólar Linked": 3, "DUAL": 1, "Bono USD": 2, "ON": 1},
        "mediano": {"Dólar Linked": 2, "DUAL": 1, "Bono ARS": 1, "Bono USD": 1, "ON": 1},
        "largo": {"BONCER": 1, "DUAL": 1, "Dólar Linked": 1, "Bono ARS": 3, "Bono USD": 4, "ON": 2},
    },
}
# Combinaciones (perfil, plazo) → clases SIN inclinación a propósito (afinidad 0: decide solo el Score). Existe para que "no tiene regla" sea una decisión explícita y no un olvido:
# la prueba de cobertura exige que toda clase esté en AFINIDAD o acá.
CLASES = ("LECAP", "BONCAP", "BONCER", "TAMAR", "DUAL", "Dólar Linked", "Bono ARS", "Bono USD", "ON")
SIN_INCLINACION_A_PROPOSITO: dict[tuple[str, str], tuple[str, ...]] = {
    ("conservador", "mediano"): ("Dólar Linked", "Bono ARS", "Bono USD", "ON"),  # decide la calidad, no el tipo ni la moneda
    ("conservador", "largo"): ("LECAP", "BONCAP", "Dólar Linked", "Bono ARS"),  # LECAP/BONCAP: el descuento por vencer pronto ya los trata; los pesos no se rechazan
    ("moderado", "corto"): ("Bono ARS", "Bono USD", "ON"),  # término medio: sin castigo por tipo ni moneda
    ("moderado", "mediano"): CLASES,  # término medio: sin inclinación por tipo ni moneda
    ("moderado", "largo"): ("LECAP", "BONCAP", "BONCER", "TAMAR", "DUAL", "Dólar Linked", "Bono ARS"),  # solo se inclina (leve) a ON y bonos USD
    ("agresivo", "corto"): ("LECAP", "BONCAP", "BONCER", "TAMAR", "Bono ARS"),  # el agresivo no busca estos tipos: los decide el Score
    ("agresivo", "mediano"): ("LECAP", "BONCAP", "BONCER", "TAMAR"),
    ("agresivo", "largo"): ("LECAP", "BONCAP", "TAMAR"),
}
# Atenuación por calidad: el castigo (afinidad negativa) a las clases con riesgo de emisor y de precio se reduce si el factor Riesgo del propio instrumento es alto (menos
# riesgo: duration corta, buena calificación). Con Riesgo ≤ RIESGO_ATENUA_DESDE se aplica entero; con Riesgo ≥ RIESGO_ATENUA_TOTAL queda en FACTOR_MINIMO_CALIDAD; en el medio, lineal.
# Así una ON de alta calidad no se castiga igual que una sin calificación (el Riesgo ya incluye la calificación). El premio y las otras clases no se tocan.
CLASES_CON_RIESGO_DE_EMISOR = ("ON", "Bono USD")
RIESGO_ATENUA_DESDE, RIESGO_ATENUA_TOTAL, FACTOR_MINIMO_CALIDAD = 40.0, 75.0, 0.25


def atenuacion_por_calidad(clase: str, riesgo: float | None) -> float:
    """Multiplicador (FACTOR_MINIMO_CALIDAD a 1) del castigo de afinidad según el Riesgo del instrumento. 1 si la clase no tiene riesgo de emisor o no se conoce el Riesgo."""
    if riesgo is None or clase not in CLASES_CON_RIESGO_DE_EMISOR:
        return 1.0
    tramo = min(1.0, max(0.0, (riesgo - RIESGO_ATENUA_DESDE) / (RIESGO_ATENUA_TOTAL - RIESGO_ATENUA_DESDE)))
    return 1.0 - (1.0 - FACTOR_MINIMO_CALIDAD) * tramo


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
    atenuacion_calidad: float = 1.0  # 1 = sin atenuar; menos de 1 = el castigo de afinidad se redujo porque el Riesgo del instrumento es alto (ver atenuacion_por_calidad)


logger = logging.getLogger("rentafy.scoring")
_CLASES_AVISADAS: set[str] = set()


def clase_instrumento(tipo: str, subtipo: str | None) -> str:
    """Clave de la tabla de afinidad: el subtipo si existe (BONCER, TAMAR, DUAL, Dólar Linked, Bono USD, Bono ARS), si no el tipo (ON, LECAP, BONCAP)."""
    return subtipo or tipo


def _descalce(anios: float, perfil: PerfilInversor, plazo: PlazoInversion) -> float:
    if plazo == "largo":
        return min(DESCUENTO_LARGO_TOPE, DESCUENTO_LARGO_PUNTOS_POR_ANIO_QUE_FALTA * max(0.0, 1.0 - anios))
    desde, hasta = BANDA_ANIOS[plazo]
    if anios < desde:
        pendiente, tope = DESCALCE[plazo]["antes"]
        return min(tope, (desde - anios) * pendiente)
    if anios > hasta:
        pendiente, tope = DESCALCE[plazo]["despues"]
        mult = MULT_DESCALCE_DESPUES[perfil]
        return min(tope * mult, (anios - hasta) * pendiente * mult)
    return 0.0


def ajuste_horizonte(clase: str, anios: float, perfil: PerfilInversor, plazo: PlazoInversion, riesgo: float | None = None) -> AjusteHorizonte:
    """`riesgo`: factor Riesgo (0-100, más alto = menos riesgo) del instrumento en esa fecha; si se pasa, atenúa el castigo de afinidad de las clases con riesgo de emisor."""
    anios = max(0.0, anios)
    if clase not in CLASES and clase not in _CLASES_AVISADAS:
        # Una clase nueva (otro subtipo en el importador) no tiene regla: se le aplica solo el descalce y la afinidad vale 0. Se avisa UNA vez para que alguien la defina en AFINIDAD.
        _CLASES_AVISADAS.add(clase)
        logger.warning("Clase de activo sin regla de afinidad: %r (se usa afinidad 0). Definirla en scoring.AFINIDAD y composicion.AFINIDAD.", clase)
    afinidad = max(-TOPE_AFINIDAD, min(TOPE_AFINIDAD, AFINIDAD[perfil][plazo].get(clase, 0.0)))
    minimo = MIN_ANIOS_AFINIDAD[plazo]
    if afinidad > 0 and minimo:
        afinidad *= min(1.0, anios / minimo)  # sin recorrido suficiente, la afinidad positiva se apaga gradualmente
    if afinidad < 0 and plazo != "largo":
        afinidad *= min(1.0, 0.5 + 0.5 * anios)  # si vence en menos de 1 año hay poco riesgo de precio y de emisor que castigar: la afinidad negativa se atenúa
    calidad = atenuacion_por_calidad(clase, riesgo) if afinidad < 0 else 1.0
    afinidad *= calidad
    return AjusteHorizonte(clase=clase, anios=anios, afinidad=afinidad, descalce=_descalce(anios, perfil, plazo), atenuacion_calidad=calidad)


def ajuste_para(instrumento, fecha: date, perfil: PerfilInversor, plazo: PlazoInversion, riesgo: float | None = None) -> AjusteHorizonte | None:
    """Ajuste de un instrumento (objeto con tipo, subtipo y vencimiento) a la fecha de su Score; `riesgo` = su factor Riesgo de esa fecha. None si el interruptor está apagado."""
    if not config.SCORE_AJUSTE_HORIZONTE or instrumento is None or instrumento.vencimiento is None:
        return None
    anios = (instrumento.vencimiento - fecha).days / 365.0
    return ajuste_horizonte(clase_instrumento(instrumento.tipo, instrumento.subtipo), anios, perfil, plazo, riesgo)


def aplicar_ajuste(base: float, ajuste: AjusteHorizonte | None) -> float:
    """Score base + afinidad − descalce, sin que el ajuste reste más de MAX_RESTA_AJUSTE ni lleve el Score por debajo de FRACCION_PISO × el base:
    un activo poco adecuado queda con Score bajo, pero no en 0–1, y el orden entre activos distintos se conserva (piso proporcional, no plano)."""
    if ajuste is None:
        return base
    return min(100.0, max(base + ajuste.afinidad - ajuste.descalce, base - MAX_RESTA_AJUSTE, base * FRACCION_PISO))


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


def score_de_fila(fila, instrumento, perfil: PerfilInversor, plazo: PlazoInversion = "mediano", pesos_base: dict[PerfilInversor, PesosPerfil] | None = None) -> int | None:
    """EL punto de entrada para pedir el Score de un instrumento en una fecha: ponderación de los cuatro factores de `fila` (Scoring o cualquier fila con
    rendimiento, riesgo, liquidez, estabilidad y fecha_calculo) + ajuste por horizonte según el vencimiento de `instrumento`. Que todas las pantallas pasen
    por acá evita que alguna se olvide del ajuste."""
    ajuste = ajuste_para(instrumento, fila.fecha_calculo, perfil, plazo, fila.riesgo)
    return compute_score(fila.rendimiento, fila.riesgo, fila.liquidez, fila.estabilidad, perfil, plazo, pesos_base, ajuste)


def es_scoring_real(fila) -> bool:
    """False para el Score aleatorio de reserva (ver config.MOSTRAR_SCORE_PLACEHOLDER)."""
    return config.MOSTRAR_SCORE_PLACEHOLDER or fila.modelo_id != PLACEHOLDER_MODELO_ID


def condicion_scoring_real():
    """Condición SQL equivalente a es_scoring_real(): excluye las filas del modelo placeholder salvo que se haya pedido mostrarlas."""
    return true() if config.MOSTRAR_SCORE_PLACEHOLDER else Scoring.modelo_id != PLACEHOLDER_MODELO_ID
