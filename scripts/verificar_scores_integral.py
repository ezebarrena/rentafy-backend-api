"""Verificación integral de los Scores: ¿lo que se calcula y lo que ve el usuario tiene sentido para cada perfil y horizonte? SOLO LECTURA (no escribe en la base).

Uso, desde la raíz de rentafy-backend-api (con la base y, si se quiere el chequeo de punta a punta, el backend levantado):

    python -m scripts.verificar_scores_integral                      # Markdown por pantalla
    python -m scripts.verificar_scores_integral --api http://127.0.0.1:8000 --informe informes_scores.md
    python -m scripts.verificar_scores_integral --sin-api

Devuelve código 1 si falla algún control. Tiene tres bloques:
  A. Invariantes: acotación del ajuste, regla de factores ausentes, orden conservado, empates, interruptor, coherencia del veredicto, una sola fórmula
     (backend contra la copia del servicio de IA) y cobertura de reglas.
  B. "Tiene sentido": expectativas por perfil y horizonte escritas ANTES de mirar los resultados (son lo que pidió el producto: el conservador de corto plazo
     prefiere letras e inflación y no activos en dólares de plazo largo, el agresivo de largo plazo puede ir por rendimiento con cualquier plazo, el horizonte corto es el
     más restrictivo, la calidad atenúa el castigo, etc.). Cada una informa el valor medido y el umbral.
  C. Punta a punta por la API: el Score del listado, del detalle, del histórico y de la lectura coinciden con el cálculo directo.
Y un informe con los mejores y peores de cada una de las 9 combinaciones, para leerlos a ojo.
"""

import argparse
import importlib.util
import json
import sys
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from statistics import mean

from sqlalchemy import func

from app import config, scoring
from app.database import SessionFinanciera
from app.lectura_perfil import AJUSTE_FUERTE, veredicto_de
from app.models_financiera import Cotizacion, Instrumento, Scoring
from app.scoring import ajuste_horizonte, aplicar_ajuste, clase_instrumento, score_base, score_de_fila

PERFILES = ("conservador", "moderado", "agresivo")
PLAZOS = ("corto", "mediano", "largo")
PESOS_CORTOS_PESOS = ("LECAP", "BONCAP", "BONCER")
USD = ("ON", "Bono USD")
RUTA_IA = Path(__file__).resolve().parents[2] / "rentafy-servicioIA" / "app" / "composicion.py"


# ------------------------------------------------------------------------------------------------ utilidades
def rangos(valores: list[float]) -> list[float]:
    orden = sorted(range(len(valores)), key=lambda i: valores[i])
    r = [0.0] * len(valores)
    i = 0
    while i < len(orden):
        j = i
        while j + 1 < len(orden) and valores[orden[j + 1]] == valores[orden[i]]:
            j += 1
        for k in range(i, j + 1):
            r[orden[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def spearman(a: list[float], b: list[float]) -> float | None:
    if len(a) < 5:
        return None
    ra, rb = rangos(a), rangos(b)
    ma, mb = mean(ra), mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return None if den == 0 else num / den


class Resultados:
    def __init__(self) -> None:
        self.filas: list[tuple[str, str, bool, str]] = []

    def check(self, bloque: str, nombre: str, ok: bool, detalle: str, informativo: bool = False) -> None:
        """`informativo`: el resultado se muestra (⚠️ si no se cumple) pero no cuenta como falla: es una observación sobre lo que DA el Score, no una propiedad que deba cumplirse."""
        self.filas.append((bloque, nombre, bool(ok) or informativo, detalle if ok or not informativo else f"⚠️ {detalle}"))

    @property
    def fallas(self) -> list[tuple[str, str, bool, str]]:
        return [f for f in self.filas if not f[2]]


def cargar_composicion_ia():
    if not RUTA_IA.exists():
        return None
    spec = importlib.util.spec_from_file_location("composicion_ia", RUTA_IA)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def http(base: str, ruta: str):
    with urllib.request.urlopen(base + ruta, timeout=20) as r:
        return json.load(r)


# ------------------------------------------------------------------------------------------------ datos
def cargar_datos():
    db = SessionFinanciera()
    try:
        fecha = db.query(func.max(Scoring.fecha_calculo)).filter(Scoring.modelo_id != scoring.PLACEHOLDER_MODELO_ID).scalar()
        filas = (
            db.query(Scoring, Instrumento)
            .join(Instrumento, Instrumento.ticker == Scoring.instrumento_ticker)
            .filter(Scoring.fecha_calculo == fecha, Instrumento.activo.is_(True), Scoring.modelo_id != scoring.PLACEHOLDER_MODELO_ID)
            .all()
        )
        cots = {c.instrumento_ticker: c for c in db.query(Cotizacion).filter(Cotizacion.fecha >= fecha - timedelta(days=15))}
        datos = []
        for sc, inst in filas:
            datos.append(
                {
                    "ticker": inst.ticker,
                    "clase": clase_instrumento(inst.tipo, inst.subtipo),
                    "moneda": inst.moneda,
                    "inst": inst,
                    "sc": sc,
                    "anios": (inst.vencimiento - fecha).days / 365.0,
                    "liquidez": inst.liquidez,
                    "cot": cots.get(inst.ticker),
                }
            )
        # Se desprende de la sesión: la verificación sigue usando solo atributos ya cargados.
        for d in datos:
            db.expunge(d["inst"])
            db.expunge(d["sc"])
        return fecha, datos
    finally:
        db.close()


def calcular(datos):
    """Una fila por (instrumento, perfil, plazo) con Score base, ajuste y Score final (sin redondear) y veredicto."""
    filas = []
    for d in datos:
        sc = d["sc"]
        for perfil in PERFILES:
            for plazo in PLAZOS:
                base = score_base(sc.rendimiento, sc.riesgo, sc.liquidez, sc.estabilidad, perfil, plazo, None)
                aj = ajuste_horizonte(d["clase"], d["anios"], perfil, plazo, sc.riesgo)
                if base is None:
                    filas.append(dict(d=d, perfil=perfil, plazo=plazo, base=None, aj=aj, final=None, ver=None))
                    continue
                final = aplicar_ajuste(base, aj)
                filas.append(dict(d=d, perfil=perfil, plazo=plazo, base=base, aj=aj, final=final, ver=veredicto_de(final, final - base)))
    return filas


# ------------------------------------------------------------------------------------------------ bloque A
def bloque_a(r: Resultados, datos, filas, ia) -> None:
    con = [f for f in filas if f["final"] is not None]
    # A1 cotas
    fuera = [f for f in con if not (0 <= f["final"] <= 100) or f["final"] < f["base"] - scoring.MAX_RESTA_AJUSTE - 1e-9 or f["final"] < f["base"] * scoring.FRACCION_PISO - 1e-9]
    r.check("A", "Score entre 0 y 100, sin restar más de 25 puntos ni bajar de la mitad del base", not fuera, f"{len(con)} Scores, {len(fuera)} fuera de cota")
    # A2 regla de factores ausentes
    mal = []
    for d in datos:
        sc = d["sc"]
        ausentes = sum(v is None for v in (sc.rendimiento, sc.riesgo, sc.liquidez, sc.estabilidad))
        for perfil in PERFILES:
            for plazo in PLAZOS:
                s = score_de_fila(sc, d["inst"], perfil, plazo, None)
                if (ausentes >= scoring.MINIMO_FACTORES_AUSENTES_PARA_ANULAR) != (s is None):
                    mal.append((d["ticker"], perfil, plazo, ausentes, s))
    n2 = sum(1 for d in datos if sum(v is None for v in (d["sc"].rendimiento, d["sc"].riesgo, d["sc"].liquidez, d["sc"].estabilidad)) >= 2)
    r.check("A", "Con 2 o más factores sin calcular no hay Score; con 0 o 1 sí", not mal, f"{n2} instrumentos sin Score por esta regla; {len(mal)} violaciones")
    # A3 orden conservado (en lo que no vence pronto: lo que vence en menos de 6 meses se reordena A PROPÓSITO en mediano y largo plazo, ver B13)
    peor = None
    peor_total = None
    for perfil in PERFILES:
        for plazo in PLAZOS:
            g = [f for f in con if f["perfil"] == perfil and f["plazo"] == plazo]
            rho_total = spearman([f["base"] for f in g], [f["final"] for f in g])
            resto = [f for f in g if f["d"]["anios"] >= 0.5]
            rho = spearman([f["base"] for f in resto], [f["final"] for f in resto])
            if rho is not None and (peor is None or rho < peor[0]):
                peor = (rho, perfil, plazo)
            if rho_total is not None and (peor_total is None or rho_total < peor_total[0]):
                peor_total = (rho_total, perfil, plazo)
    r.check("A", "El ajuste inclina el ranking pero no lo pisa: Spearman entre Score base y final ≥ 0,85 en las 9 combinaciones, sin contar lo que vence en menos de 6 meses", peor is not None and peor[0] >= 0.85, f"el más bajo: {peor[0]:.3f} ({peor[1]} {peor[2]}); contando todo: {peor_total[0]:.3f} ({peor_total[1]} {peor_total[2]})" if peor else "sin datos")
    # A4 piso proporcional: el orden se conserva entre los activos que llegan al piso
    peor_rho, n_piso, n_distintos = 1.0, 0, 0
    for perfil in PERFILES:
        for plazo in PLAZOS:
            g = [f for f in con if f["perfil"] == perfil and f["plazo"] == plazo and f["final"] <= f["base"] * scoring.FRACCION_PISO + 1e-9 and f["aj"].afinidad - f["aj"].descalce < 0]
            if len(g) >= 5:
                rho = spearman([f["base"] for f in g], [f["final"] for f in g])
                if rho is not None:
                    peor_rho = min(peor_rho, rho)
            n_piso += len(g)
            n_distintos += len({round(f["final"]) for f in g})
    r.check("A", "El piso es proporcional al Score base: entre los activos que llegan al piso se conserva el orden (Spearman ≥ 0,99)", peor_rho >= 0.99, f"{n_piso} casos en el piso; Spearman mínimo {peor_rho:.3f}; el redondeo a entero deja {n_distintos} valores distintos")
    # A5 interruptor
    previo = config.SCORE_AJUSTE_HORIZONTE
    try:
        config.SCORE_AJUSTE_HORIZONTE = False
        distinto = [d["ticker"] for d in datos[:80] if score_de_fila(d["sc"], d["inst"], "moderado", "mediano", None) != (None if score_base(d["sc"].rendimiento, d["sc"].riesgo, d["sc"].liquidez, d["sc"].estabilidad, "moderado", "mediano", None) is None else round(score_base(d["sc"].rendimiento, d["sc"].riesgo, d["sc"].liquidez, d["sc"].estabilidad, "moderado", "mediano", None)))]
    finally:
        config.SCORE_AJUSTE_HORIZONTE = previo
    r.check("A", "Con SCORE_AJUSTE_HORIZONTE=0 el Score vuelve a ser solo la ponderación de los factores", not distinto, f"{len(distinto)} instrumentos distintos de 80 revisados")
    # A6 veredicto
    inco = [f for f in con if (f["ver"] == "recomendable" and not (f["final"] >= 70 and f["final"] - f["base"] > -AJUSTE_FUERTE)) or (f["ver"] == "no_recomendable" and not (f["final"] < 40 or (f["final"] - f["base"] <= -15 and f["final"] < 55)))]
    r.check("A", "El veredicto es coherente con el Score (recomendable ≥ 70 sin castigo fuerte; no recomendable < 40 o castigo ≥ 15 con Score < 55)", not inco, f"{len(inco)} incoherentes")
    # A7 una sola fórmula
    if ia is None:
        r.check("A", "Una sola fórmula: backend = copia del servicio de IA (composicion.py)", True, "se omite: no está rentafy-servicioIA al lado")
    else:
        difs = 0
        for f in con:
            d = f["d"]
            sc = d["sc"]
            w = ia.pesos_finales(ia.PESOS_PARTIDA[f["perfil"]], f["plazo"])
            base_ia = ia.score_fila(sc.rendimiento, sc.riesgo, sc.liquidez, sc.estabilidad, tuple(w))
            af, ds = ia.ajuste_fila(d["clase"], d["anios"], f["perfil"], f["plazo"], sc.riesgo)
            final_ia = ia.aplicar_ajuste_fila(base_ia, af, ds)
            if base_ia is None or abs(final_ia - f["final"]) > 1e-6 or round(final_ia) != round(f["final"]):
                difs += 1
        r.check("A", "Una sola fórmula: backend = copia del servicio de IA (composicion.py) en todos los Scores", difs == 0, f"{len(con)} Scores comparados, {difs} distintos")
    # A8 cobertura de reglas
    faltan = []
    for perfil in scoring.AFINIDAD:
        for plazo in scoring.AFINIDAD[perfil]:
            neutras = scoring.SIN_INCLINACION_A_PROPOSITO.get((perfil, plazo), ())
            for clase in scoring.CLASES:
                if (clase in scoring.AFINIDAD[perfil][plazo]) == (clase in neutras):
                    faltan.append((perfil, plazo, clase))
    r.check("A", "Toda combinación perfil × plazo × clase tiene regla o está declarada neutra a propósito", not faltan, f"{len(faltan)} sin definir")
    # A9 clases del catálogo conocidas
    desconocidas = sorted({d["clase"] for d in datos} - set(scoring.CLASES))
    r.check("A", "Todas las clases del catálogo están contempladas por las reglas", not desconocidas, f"clases desconocidas: {desconocidas or 'ninguna'}")


# ------------------------------------------------------------------------------------------------ bloque B
def media(valores):
    valores = list(valores)
    return mean(valores) if valores else float("nan")


def bloque_b(r: Resultados, datos, filas) -> None:
    con = [f for f in filas if f["final"] is not None]

    def sel(perfil, plazo, clases=None, anios=None):
        for f in con:
            if f["perfil"] != perfil or f["plazo"] != plazo:
                continue
            if clases is not None and f["d"]["clase"] not in clases:
                continue
            if anios is not None and not anios(f["d"]["anios"]):
                continue
            yield f

    def top(perfil, plazo, n):
        return sorted(sel(perfil, plazo), key=lambda f: -f["final"])[:n]

    # B1..B3 conservador corto
    a, b = media(f["final"] for f in sel("conservador", "corto", PESOS_CORTOS_PESOS)), media(f["final"] for f in sel("conservador", "corto", USD))
    r.check("B", "Conservador · corto: letras y activos que ajustan por inflación puntúan al menos 10 puntos más que ON y bonos en dólares (promedios)", a - b >= 10, f"LECAP/BONCAP/BONCER {a:.1f} vs ON/Bono USD {b:.1f}")
    malos = [f["d"]["ticker"] for f in top("conservador", "corto", 5) if f["d"]["clase"] in USD and f["d"]["anios"] > 3]
    r.check("B", "Conservador · corto: ningún ON ni bono en dólares de más de 3 años entre los 5 primeros", not malos, f"{malos or 'ninguno'}")
    largos = [(f["d"]["ticker"], round(f["final"]), f["ver"]) for f in sel("conservador", "corto", USD, lambda x: x >= 8) if f["ver"] != "no_recomendable"]
    maximo = max((f["final"] for f in sel("conservador", "corto", USD, lambda x: x >= 8)), default=0)
    r.check("B", "Conservador · corto: todo ON o bono en dólares de 8 años o más es «no recomendable» (Score por debajo de 40, bajo pero no cero)", not largos, f"{largos[:5] or 'todos no recomendables'}; el más alto: {maximo:.0f}")
    # B4 (ver B13: los activos que vencen pronto no están sobrerrepresentados arriba en mediano ni largo plazo)
    cortos = [f["d"]["ticker"] for f in top("agresivo", "largo", 15) if f["d"]["anios"] < 1]
    r.check("B", "Agresivo · largo: ningún activo que vence en menos de 1 año entre los 15 primeros", not cortos, f"{cortos or 'ninguno'}")
    largo_agresivo = [f for f in sel("agresivo", "largo", USD, lambda x: x >= 5)]
    r.check("B", "Agresivo · largo: no se castiga vencer después (descalce 0 en los de 5 años o más)", all(f["aj"].descalce == 0 for f in largo_agresivo), f"{len(largo_agresivo)} activos revisados")
    # B5 moderado
    a, b = media(f["final"] for f in sel("moderado", "corto", PESOS_CORTOS_PESOS)), media(f["final"] for f in sel("moderado", "corto", USD))
    r.check("B", "Moderado · corto: letras e inflación puntúan más que ON y bonos en dólares (promedios)", a > b, f"LECAP/BONCAP/BONCER {a:.1f} vs ON/Bono USD {b:.1f}")
    for perfil in PERFILES:
        g = sorted(sel(perfil, "largo"), key=lambda f: -f["final"])
        q = g[: len(g) // 4]
        usd_q = sum(1 for f in q if f["d"]["clase"] in USD) / max(sum(1 for f in g if f["d"]["clase"] in USD), 1)
        letras_q = sum(1 for f in q if f["d"]["clase"] in ("LECAP", "BONCAP")) / max(sum(1 for f in g if f["d"]["clase"] in ("LECAP", "BONCAP")), 1)
        if perfil != "conservador":
            r.check("B", f"{perfil.capitalize()} · largo: ON y bonos en dólares están mejor representados que LECAP y BONCAP en el cuartil superior", usd_q >= letras_q, f"{100 * usd_q:.0f} % de las ON/Bono USD vs {100 * letras_q:.0f} % de las LECAP/BONCAP están en el 25 % mejor")
    # B6 el corto es el más restrictivo con lo que vence lejos
    malos = []
    for d in datos:
        if d["clase"] in USD and d["anios"] >= 8:
            for perfil in PERFILES:
                ds = [ajuste_horizonte(d["clase"], d["anios"], perfil, p, d["sc"].riesgo).descalce for p in PLAZOS]
                if not (ds[0] >= ds[1] >= ds[2] == 0):
                    malos.append((d["ticker"], perfil, [round(x, 1) for x in ds]))
    r.check("B", "El horizonte corto es el más restrictivo: para lo que vence en 8 años o más, descalce corto ≥ mediano ≥ largo = 0", not malos, f"{len(malos)} violaciones")
    # B7 ordenamiento por perfil
    malos = []
    for d in datos:
        if d["clase"] == "Bono USD" and d["anios"] >= 5:
            v = [ajuste_horizonte(d["clase"], d["anios"], p, "corto", d["sc"].riesgo) for p in ("agresivo", "moderado", "conservador")]
            totales = [x.afinidad - x.descalce for x in v]
            if not (totales[0] >= totales[1] >= totales[2]):
                malos.append((d["ticker"], [round(t, 1) for t in totales]))
    r.check("B", "Bono en dólares de 5+ años a corto plazo: se castiga menos al agresivo que al moderado y menos al moderado que al conservador", not malos, f"{len(malos)} violaciones")
    afin = [ajuste_horizonte("LECAP", 0.5, p, "corto").afinidad for p in PERFILES]
    r.check("B", "LECAP a corto plazo: afinidad conservador ≥ moderado ≥ agresivo", afin[0] >= afin[1] >= afin[2], f"{dict(zip(PERFILES, afin))}")
    # B8 los LECAP/BONCAP que vencen en semanas no son para largo plazo
    cortos = [f for f in sel("conservador", "largo", ("LECAP", "BONCAP"), lambda x: x < 0.5)]
    r.check("B", "Conservador · largo: LECAP/BONCAP que vencen en menos de 6 meses quedan por debajo de su Score base (no son para un horizonte largo)", all(f["final"] < f["base"] for f in cortos), f"{len(cortos)} revisados")
    # B9 calidad atenúa el castigo
    violaciones = 0
    revisados = 0
    grupo = [d for d in datos if d["clase"] in USD and d["anios"] >= 1]
    for perfil, plazo in (("conservador", "corto"), ("conservador", "mediano"), ("conservador", "largo"), ("moderado", "corto")):
        for clase in USD:
            pares = sorted((d["sc"].riesgo, ajuste_horizonte(d["clase"], d["anios"], perfil, plazo, d["sc"].riesgo).afinidad) for d in grupo if d["clase"] == clase)
            revisados += len(pares)
            violaciones += sum(1 for (r1, a1), (r2, a2) in zip(pares, pares[1:]) if a2 < a1 - 1e-9)
    for perfil, plazo, clase, anios in [(p, pl, c, a) for p in ("conservador", "moderado") for pl in PLAZOS for c in USD for a in (0.3, 2.0, 8.0)]:
        serie = [ajuste_horizonte(clase, anios, perfil, plazo, float(x)).afinidad for x in range(0, 101, 5)]
        revisados += len(serie)
        violaciones += sum(1 for a1, a2 in zip(serie, serie[1:]) if a2 < a1 - 1e-9)
    r.check("B", "Dentro de una misma clase, el castigo a ON y bonos en dólares es menor cuanto menor es el riesgo (datos reales y grilla 0–100)", violaciones == 0, f"{revisados} casos, {violaciones} inversiones")
    alta = [d for d in grupo if d["sc"].riesgo >= 75]
    sin_atenuar = [d["ticker"] for d in alta if ajuste_horizonte(d["clase"], d["anios"], "conservador", "corto", d["sc"].riesgo).afinidad < -4 * 0.25 - 1e-9]
    r.check("B", "Con Riesgo ≥ 75 el castigo de afinidad del conservador queda en un cuarto (≈ −1)", not sin_atenuar, f"{len(alta)} activos con Riesgo ≥ 75")
    # B10 el agresivo corto experimenta
    dl = [d for d in datos if d["clase"] == "Dólar Linked"]
    if dl:
        ag = media(ajuste_horizonte("Dólar Linked", d["anios"], "agresivo", "corto").afinidad for d in dl)
        co = media(ajuste_horizonte("Dólar Linked", d["anios"], "conservador", "corto").afinidad for d in dl)
        r.check("B", "Dólar Linked: el agresivo de corto plazo lo busca (afinidad > 0) y el conservador no (< 0)", ag > 0 > co, f"agresivo {ag:+.1f}, conservador {co:+.1f}")
    ba = [d for d in datos if d["clase"] == "Bono ARS"]
    if ba:
        co = media(ajuste_horizonte("Bono ARS", d["anios"], "conservador", "corto").afinidad for d in ba)
        ag = media(ajuste_horizonte("Bono ARS", d["anios"], "agresivo", "largo").afinidad for d in ba)
        r.check("B", "Bono ARS: el conservador de corto plazo lo evita (< 0) y el agresivo de largo plazo lo busca (> 0)", ag > 0 > co, f"conservador corto {co:+.1f}, agresivo largo {ag:+.1f}")
    # B13 lo que vence en menos de 6 meses no es para un horizonte mediano ni largo: no puede estar sobrerrepresentado en el cuartil superior
    for plazo in ("mediano", "largo"):
        for perfil in PERFILES:
            g = sorted(sel(perfil, plazo), key=lambda f: -f["final"])
            q = g[: len(g) // 4]
            cortos = [f for f in g if f["d"]["anios"] < 0.5]
            resto = [f for f in g if f["d"]["anios"] >= 0.5]
            p_cortos = sum(1 for f in q if f["d"]["anios"] < 0.5) / max(len(cortos), 1)
            p_resto = sum(1 for f in q if f["d"]["anios"] >= 0.5) / max(len(resto), 1)
            r.check("B", f"{perfil.capitalize()} · {plazo}: lo que vence en menos de 6 meses no está sobrerrepresentado en el 25 % mejor (≤ el resto + 5 puntos)", p_cortos <= p_resto + 0.05, f"{100 * p_cortos:.0f} % de {len(cortos)} activos que vencen pronto vs {100 * p_resto:.0f} % del resto")
    # B14 y para un horizonte corto sí está bien representado (el horizonte cambia qué se recomienda)
    g = sorted(sel("conservador", "corto"), key=lambda f: -f["final"])
    q = g[: len(g) // 4]
    cortos = [f for f in g if f["d"]["anios"] < 0.5]
    p_cortos = sum(1 for f in q if f["d"]["anios"] < 0.5) / max(len(cortos), 1)
    r.check("B", "Conservador · corto: lo que vence en menos de 6 meses SÍ está bien representado en el 25 % mejor (≥ 50 %)", p_cortos >= 0.5, f"{100 * p_cortos:.0f} % de {len(cortos)} activos")
    # B15 en mediano y largo la calidad decide, no el tipo ni la moneda (conservador y moderado), y entran activos en pesos y en dólares
    for perfil, plazo in (("conservador", "mediano"), ("conservador", "largo"), ("moderado", "corto"), ("moderado", "mediano"), ("moderado", "largo")):
        neg = [f["d"]["ticker"] for f in sel(perfil, plazo) if f["aj"].afinidad < 0]
        r.check("B", f"{perfil.capitalize()} · {plazo}: ningún activo recibe castigo de afinidad por tipo o moneda", not neg, f"{len(neg)} con castigo")
    for perfil, plazo in (("conservador", "mediano"), ("conservador", "largo"), ("moderado", "mediano"), ("moderado", "largo")):
        g = sorted(sel(perfil, plazo), key=lambda f: -f["final"])
        q = g[:15]
        pesos = sum(1 for f in q if f["d"]["moneda"] == "ARS")
        top30 = sum(1 for f in g[:30] if f["d"]["moneda"] == "ARS")
        r.check("B", f"{perfil.capitalize()} · {plazo}: la moneda no excluye: entre los 15 mejores hay activos en pesos y en dólares (informativo)", 0 < pesos < 15, f"{pesos} en pesos y {15 - pesos} en dólares entre los 15 mejores; {top30} en pesos entre los 30 mejores", informativo=True)
    # B16 dentro del cuartil superior pesa la calidad: Riesgo y Estabilidad altos
    for perfil, plazo in (("conservador", "mediano"), ("conservador", "largo")):
        g = sorted(sel(perfil, plazo), key=lambda f: -f["final"])
        q = g[: len(g) // 4]
        riesgo_q = mean(f["d"]["sc"].riesgo for f in q)
        riesgo_g = mean(f["d"]["sc"].riesgo for f in g)
        r.check("B", f"{perfil.capitalize()} · {plazo}: el cuartil superior tiene más calidad (Riesgo medio) que el universo", riesgo_q > riesgo_g + 5, f"Riesgo medio {riesgo_q:.0f} vs {riesgo_g:.0f}")
    # B17 largo plazo (más de 3 años): premio leve a los dólares en los tres perfiles, sin rechazar las opciones en pesos
    pesos_clases = ("LECAP", "BONCAP", "BONCER", "TAMAR", "DUAL", "Dólar Linked", "Bono ARS")
    for perfil in PERFILES:
        g = list(sel(perfil, "largo"))
        usd_af = [f["aj"].afinidad for f in g if f["d"]["clase"] in USD]
        peso_neg = [f["d"]["ticker"] for f in g if f["d"]["clase"] in pesos_clases and f["aj"].afinidad < 0]
        ok = bool(usd_af) and all(0 <= a <= 4 for a in usd_af) and mean(usd_af) > 0 and not peso_neg
        r.check("B", f"{perfil.capitalize()} · largo: premio leve (≤ 4 puntos) a ON y bonos en dólares y ningún activo en pesos con afinidad negativa", ok, f"premio medio {mean(usd_af):+.1f}, máximo {max(usd_af):+.1f}; {len(peso_neg)} activos en pesos con castigo")
    # B11 el mismo activo cambia con perfil y horizonte (el Score no es una sola columna)
    variados = 0
    for d in datos:
        valores = {round(f["final"]) for f in con if f["d"] is d}
        if len(valores) >= 5:
            variados += 1
    r.check("B", "El Score de un mismo instrumento varía según perfil y horizonte (≥ 5 valores distintos entre las 9 combinaciones) en la mayoría", variados >= 0.8 * len(datos), f"{variados} de {len(datos)} instrumentos")
    # B12 la lista de los mejores cambia entre combinaciones extremas
    ta = {f["d"]["ticker"] for f in top("conservador", "corto", 15)}
    tb = {f["d"]["ticker"] for f in top("agresivo", "largo", 15)}
    r.check("B", "Los 15 mejores del conservador de corto plazo y del agresivo de largo plazo se diferencian (≤ 5 en común)", len(ta & tb) <= 5, f"{len(ta & tb)} en común")


# ------------------------------------------------------------------------------------------------ bloque C
def bloque_c(r: Resultados, datos, filas, base_url: str) -> None:
    import random

    rnd = random.Random(13)
    muestra = rnd.sample(datos, min(45, len(datos)))
    # explícitos que sirven de referencia
    por_ticker = {d["ticker"]: d for d in datos}
    for t in ("MR46D", "S13N6", "GD41C", "X30N6", "TZXM8", "AL30C"):
        if t in por_ticker and por_ticker[t] not in muestra:
            muestra.append(por_ticker[t])
    comparados = difs = errores = lecturas = 0
    ejemplos = []
    retornos_mal = []
    for d in muestra:
        for perfil in PERFILES:
            for plazo in PLAZOS:
                try:
                    det = http(base_url, f"/instrumentos/{d['ticker']}?perfil={perfil}&plazo={plazo}")
                except (urllib.error.URLError, TimeoutError, ValueError):
                    errores += 1
                    continue
                directo = score_de_fila(d["sc"], d["inst"], perfil, plazo, None)
                comparados += 1
                api = det.get("score")
                if (api is None) != (directo is None) or (api is not None and abs(api - directo) > 0.51):
                    difs += 1
                    ejemplos.append((d["ticker"], perfil, plazo, api, directo))
                lec = det.get("lecturaPerfil")
                if lec is not None and api is not None:
                    lecturas += 1
                    if abs(lec["scoreBase"] + lec["ajuste"] - api) > 1.01:
                        difs += 1
                        ejemplos.append((d["ticker"], perfil, plazo, "lectura", lec["scoreBase"] + lec["ajuste"], api))
                    re = lec.get("retornoEsperado")
                    if re is not None:
                        tir = det.get("tirNominalEstimada") if det.get("subtipo") == "BONCER" and det.get("tirNominalEstimada") is not None else det.get("tir")
                        if tir is not None and ((re["retornoPct"] >= 0) != (tir >= 0) or re["horizonteMeses"] > 60 or re["horizonteMeses"] > d["anios"] * 12 + 1):
                            retornos_mal.append((d["ticker"], perfil, plazo, tir, re["retornoPct"], re["horizonteMeses"]))
    r.check("C", "API de detalle: el Score coincide con el cálculo directo y la lectura (Score base + ajuste) con el Score", comparados > 0 and difs == 0 and errores == 0, f"{comparados} combinaciones, {difs} distintas, {errores} errores de red; ejemplos: {ejemplos[:3] or 'ninguno'}")
    r.check("C", "Retorno esperado: mismo signo que la TIR, ≤ 60 meses y nunca más allá del vencimiento", not retornos_mal, f"{lecturas} lecturas; {len(retornos_mal)} raras: {retornos_mal[:3] or 'ninguna'}")
    # histórico = valor vigente
    inco = []
    revisados = 0
    for d in muestra[:15]:
        for perfil, plazo in (("conservador", "corto"), ("agresivo", "largo"), ("moderado", "mediano")):
            try:
                h = http(base_url, f"/instrumentos/{d['ticker']}/scoring-historico?perfil={perfil}&plazo={plazo}")
                det = http(base_url, f"/instrumentos/{d['ticker']}?perfil={perfil}&plazo={plazo}")
            except (urllib.error.URLError, TimeoutError, ValueError):
                continue
            if det.get("score") is None:
                continue
            revisados += 1
            if not h or abs(h[-1]["score"] - det["score"]) > 0.51:
                inco.append((d["ticker"], perfil, plazo, h[-1]["score"] if h else None, det["score"]))
    r.check("C", "El último punto del gráfico histórico coincide con el Score vigente", revisados > 0 and not inco, f"{revisados} revisados, {len(inco)} distintos {inco[:3] or ''}")
    # listado ordena por el mismo Score
    try:
        lista = http(base_url, "/instrumentos?perfil=conservador&plazo=corto&sort=score&dir=desc&pageSize=50")
        items = lista["items"] if isinstance(lista, dict) else lista
        scores = [i["score"] for i in items if i.get("score") is not None]
        directo = {d["ticker"]: score_de_fila(d["sc"], d["inst"], "conservador", "corto", None) for d in datos}
        incoh = [i["ticker"] for i in items if i.get("score") is not None and directo.get(i["ticker"]) is not None and abs(i["score"] - directo[i["ticker"]]) > 0.51]
        r.check("C", "El listado devuelve el mismo Score que el cálculo directo", not incoh and len(scores) > 0, f"{len(scores)} filas, {len(incoh)} distintas")
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as exc:
        r.check("C", "El listado devuelve el mismo Score que el cálculo directo", False, f"no se pudo consultar: {exc}")


# ------------------------------------------------------------------------------------------------ informe
def tablas_por_combinacion(filas) -> str:
    texto = []
    for perfil in PERFILES:
        for plazo in PLAZOS:
            g = [f for f in filas if f["perfil"] == perfil and f["plazo"] == plazo and f["final"] is not None]
            g.sort(key=lambda f: -f["final"])
            texto.append(f"\n### {perfil} · {plazo}\n")
            texto.append("| # | Ticker | Clase | Años | Base | Afinidad | Descalce | Score | Veredicto |")
            texto.append("|---|---|---|---|---|---|---|---|---|")
            for i, f in enumerate(g[:10], start=1):
                texto.append(fila_md(i, f))
            texto.append("| … | | | | | | | | |")
            for i, f in enumerate(g[-4:], start=len(g) - 3):
                texto.append(fila_md(i, f))
    return "\n".join(texto)


def fila_md(i: int, f) -> str:
    aj = f["aj"]
    return f"| {i} | {f['d']['ticker']} | {f['d']['clase']} | {f['d']['anios']:.1f} | {f['base']:.0f} | {aj.afinidad:+.1f} | −{aj.descalce:.1f} | {round(f['final'])} | {f['ver']} |"


def resumen_clases(filas) -> str:
    tabla = defaultdict(list)
    for f in filas:
        if f["final"] is not None:
            tabla[(f["d"]["clase"], f["perfil"], f["plazo"])].append(f["final"] - f["base"])
    clases = sorted({k[0] for k in tabla})
    cab = "| Clase | " + " | ".join(f"{p[:4]}·{pl[:3]}" for p in PERFILES for pl in PLAZOS) + " |"
    lineas = [cab, "|" + "---|" * (1 + len(PERFILES) * len(PLAZOS))]
    for c in clases:
        lineas.append(f"| {c} | " + " | ".join(f"{mean(tabla[(c, p, pl)]):+.1f}" if tabla[(c, p, pl)] else "—" for p in PERFILES for pl in PLAZOS) + " |")
    return "\n".join(lineas)


def veredictos(filas) -> str:
    lineas = ["| Perfil · plazo | Recomendable | Con reservas | No recomendable | Sin Score |", "|---|---|---|---|---|"]
    for perfil in PERFILES:
        for plazo in PLAZOS:
            g = [f for f in filas if f["perfil"] == perfil and f["plazo"] == plazo]
            n = len(g)
            c = defaultdict(int)
            for f in g:
                c[f["ver"] or "sin"] += 1
            lineas.append(f"| {perfil} · {plazo} | {100 * c['recomendable'] / n:.0f} % | {100 * c['con_reservas'] / n:.0f} % | {100 * c['no_recomendable'] / n:.0f} % | {100 * c['sin'] / n:.0f} % |")
    return "\n".join(lineas)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://127.0.0.1:8000")
    ap.add_argument("--sin-api", action="store_true")
    ap.add_argument("--informe", default=None)
    args = ap.parse_args()
    fecha, datos = cargar_datos()
    filas = calcular(datos)
    r = Resultados()
    ia = cargar_composicion_ia()
    bloque_a(r, datos, filas, ia)
    bloque_b(r, datos, filas)
    if not args.sin_api:
        bloque_c(r, datos, filas, args.api)
    salida = [f"# Verificación integral de los Scores — {fecha} ({len(datos)} instrumentos activos con Scoring real, 9 combinaciones perfil × plazo)\n"]
    for bloque, titulo in (("A", "A. Invariantes del cálculo"), ("B", "B. ¿Tiene sentido para cada perfil y horizonte?"), ("C", "C. De punta a punta por la API")):
        filas_b = [f for f in r.filas if f[0] == bloque]
        if not filas_b:
            continue
        salida.append(f"## {titulo}\n")
        salida.append("| Control | Resultado | Medido |")
        salida.append("|---|---|---|")
        for _, nombre, ok, detalle in filas_b:
            salida.append(f"| {nombre} | {'⚠️' if ok and detalle.startswith('⚠️') else '✅' if ok else '❌'} | {detalle.removeprefix('⚠️ ')} |")
        salida.append("")
    avisos = sum(1 for f in r.filas if f[3].startswith("⚠️"))
    salida.append(f"**{len(r.filas) - len(r.fallas)} de {len(r.filas)} controles cumplidos**" + (f" ({avisos} con aviso informativo)." if avisos else ".") + "\n")
    salida.append("## Veredictos por combinación\n\n" + veredictos(filas) + "\n")
    salida.append("## Ajuste medio (Score final − Score base, en puntos) por clase de activo\n\n" + resumen_clases(filas) + "\n")
    salida.append("## Los 10 mejores y los 4 peores de cada combinación (para leerlos a ojo)\n" + tablas_por_combinacion(filas))
    texto = "\n".join(salida)
    if args.informe:
        Path(args.informe).write_text(texto, encoding="utf-8")
    print(texto)
    return 1 if r.fallas else 0


if __name__ == "__main__":
    sys.exit(main())
