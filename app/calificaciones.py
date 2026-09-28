"""Calificación de riesgo crediticio por emisor de Obligaciones Negociables (ON) — ver
CalificacionCrediticia en models_financiera.py.

Motivación (chapter04.tex no lo cubre; extensión de esta iteración, a partir de dos hallazgos
de uso reales): el factor Riesgo de una ON hoy depende SOLO de su duration relativa dentro del
grupo de pares (tipo="ON", sin subtipo) — y como TODAS las ON caen en ese único grupo, una ON
de un emisor chico y sin trayectoria puede salir tan "segura" como una de YPF, si su duration es
corta. Este módulo agrega la calificación real de una calificadora de riesgo registrada ante la
CNV (FIX SCR, afiliada de Fitch Ratings; o Moody's Local Argentina), en escala nacional
argentina, como segunda señal — ver factores/riesgo.py del Servicio de IA para cómo se combina.

Fuente y método de curaduría — IMPORTANTE, a diferencia de cauciones.py (que scrapea un único
valor de una página con formato fijo), esto NO es un scraper automático: FIX SCR y Moody's Local
publican la calificación vigente en páginas de emisor con estructura propia (no una API ni un
único endpoint uniforme), y factores de renovación/degradación no cambian de un día para el
otro — un scraper diario sería sobre-ingeniería para un dato que se actualiza, en la práctica,
cada varios meses. Por eso esta tabla se cura A MANO (búsqueda directa en fixscr.com/calificaciones
y moodyslocal.com.ar, ambas de acceso público) y se vuelve a revisar periódicamente — no todos
los emisores de ON del catálogo tienen entrada acá todavía (ver docstring de RATING_POR_EMISOR
más abajo, y NUMERO_SIN_CALIFICACION en riesgo.py del Servicio de IA para qué pasa con esos).

Variantes de nombre del mismo emisor (ej. "Pampa" y "Pampa Energía", que el bono nombra distinto
según la serie — ver _derivar_emisor en ingest.py) se listan como entradas separadas apuntando
a la misma calificación: no hay una fuente de nombre legal único para des-duplicarlas del todo.
"""

from datetime import date

from sqlalchemy.orm import Session

from .models_financiera import CalificacionCrediticia

# Escala nacional Argentina (FIX SCR/Fitch y Moody's Local usan notches equivalentes, aunque
# con sufijo distinto: "(arg)" vs ".ar") mapeada a 0-100, 100 = AAA (máxima calidad, mínimo
# riesgo de default). No es una escala oficial de ninguna calificadora — es una linealización
# nuestra para poder promediarla con el percentil de duration en riesgo.py.
RATING_A_NUMERO: dict[str, float] = {
    "AAA": 100.0, "AA+": 96.0, "AA": 92.0, "AA-": 88.0,
    "A+": 82.0, "A": 76.0, "A-": 70.0,
    "BBB+": 62.0, "BBB": 54.0, "BBB-": 46.0,
    "BB+": 38.0, "BB": 30.0, "BB-": 24.0,
    "B+": 18.0, "B": 13.0, "B-": 9.0,
    "CCC": 5.0, "CC": 3.0, "C": 1.0,
    "D": 0.0, "RD": 0.0,
}


def rating_a_numero(rating_letra: str) -> float:
    """Normaliza "AA-(arg)" / "AA-.ar" / "Aa3.ar" (Moody's a veces usa números 1/2/3 en vez de
    +/-, pero nuestros hallazgos hasta ahora vinieron todos en notación +/- o plana) al notch
    base y lo busca en RATING_A_NUMERO. KeyError si no matchea ningún notch conocido — a
    propósito: mejor fallar la curaduría a mano que guardar un número inventado."""
    base = rating_letra.upper().replace("(ARG)", "").replace(".AR", "").strip()
    return RATING_A_NUMERO[base]


# Tabla curada a mano (ver docstring del módulo) — (emisor, agencia, rating, url de referencia).
# emisor debe matchear exactamente el resultado de _derivar_emisor() en ingest.py para que
# riesgo.py pueda encontrarlo por Instrumento.emisor. fecha_actualizacion es cuándo se verificó
# ESTA curaduría (no la fecha del comunicado original de la calificadora).
_HOY_CURADURIA = date(2026, 9, 28)

RATING_POR_EMISOR: list[tuple[str, str, str, str]] = [
    ("YPF", "FIX SCR", "AAA(arg)", "https://www.fixscr.com/finanzas-corporativas"),
    ("YPF Luz", "FIX SCR", "AAA(arg)", "https://www.fixscr.com/finanzas-corporativas"),
    ("Vista", "FIX SCR", "AAA(arg)", "https://www.fixscr.com/finanzas-corporativas"),
    ("Vista Energy", "FIX SCR", "AAA(arg)", "https://www.fixscr.com/finanzas-corporativas"),
    ("Pampa", "FIX SCR", "AAA(arg)", "https://www.fixscr.com/finanzas-corporativas"),
    ("Pampa Energía", "FIX SCR", "AAA(arg)", "https://www.fixscr.com/finanzas-corporativas"),
    ("Pluspetrol", "FIX SCR", "AAA(arg)", "https://www.fixscr.com/finanzas-corporativas"),
    ("Tecpetrol", "FIX SCR", "AAA(arg)", "https://www.fixscr.com/finanzas-corporativas"),
    ("Capex", "FIX SCR", "AA(arg)", "https://www.fixscr.com/finanzas-corporativas"),
    ("CGC", "FIX SCR", "AA-(arg)", "https://www.fixscr.com/finanzas-corporativas"),
    ("Pan American", "FIX SCR", "AAA(arg)", "https://www.fixscr.com/finanzas-corporativas"),
    ("Pan American Energy", "FIX SCR", "AAA(arg)", "https://www.fixscr.com/finanzas-corporativas"),
    ("MSU Energy", "FIX SCR", "AA-(arg)", "https://www.fixscr.com/finanzas-corporativas"),
    ("MSU Green", "FIX SCR", "AA-(arg)", "https://www.fixscr.com/finanzas-corporativas"),
    ("Central Puerto", "FIX SCR", "AAA(arg)", "https://www.fixscr.com/finanzas-corporativas"),
    ("Edenor", "FIX SCR", "AA-(arg)", "https://www.fixscr.com/finanzas-corporativas"),
    ("EDESA", "FIX SCR", "AA-(arg)", "https://www.fixscr.com/finanzas-corporativas"),
    ("EDEMSA", "FIX SCR", "AA-(arg)", "https://www.fixscr.com/finanzas-corporativas"),
    ("Telecom", "FIX SCR", "AAA(arg)", "https://www.fixscr.com/finanzas-corporativas"),
    ("Profertil", "FIX SCR", "AAA(arg)", "https://www.fixscr.com/finanzas-corporativas"),
    ("Aluar", "FIX SCR", "AAA(arg)", "https://www.fixscr.com/finanzas-corporativas"),
    ("Ledesma", "FIX SCR", "AA-(arg)", "https://www.fixscr.com/finanzas-corporativas"),
    ("Loma Negra", "FIX SCR", "AAA(arg)", "https://www.fixscr.com/finanzas-corporativas"),
    ("AA2000", "FIX SCR", "AA+(arg)", "https://www.fixscr.com/finanzas-corporativas"),
    ("San Miguel", "FIX SCR", "BBB-(arg)", "https://www.fixscr.com/emisor/view?type=emisor&id=585"),
    ("Arcor", "FIX SCR", "AAA(arg)", "https://www.fixscr.com/emisor/view?type=emisor&id=35"),
    ("Cresud", "FIX SCR", "AA-(arg)", "https://www.fixscr.com/emisor/view?type=emisor&id=218"),
    ("IRSA", "FIX SCR", "AA+(arg)", "https://www.fixscr.com/reportes-web/view?id=52197"),
    ("Genneia", "FIX SCR", "A+(arg)", "https://www.fixscr.com/emisor/view?type=emisor&id=401"),
    ("Camuzzi Gas Pampeana", "FIX SCR", "A+(arg)", "https://www.fixscr.com/emisor/view?type=emisor&id=156"),
    ("PECOM", "FIX SCR", "AA(arg)", "https://www.fixscr.com/emisor/view?type=emisor&id=4607"),
    ("Oldelval", "FIX SCR", "AAA(arg)", "https://www.fixscr.com/reportes-web/view?id=49122"),
    ("Scania Credit", "FIX SCR", "AAA(arg)", "https://www.fixscr.com/emisor/view?type=emisor&id=4675"),
    ("Banco Macro", "FIX SCR", "AAA(arg)", "https://www.fixscr.com/emisor/view?type=emisor&id=97"),
    ("Banco Comafi", "FIX SCR", "AA(arg)", "https://www.fixscr.com/finanzas-corporativas"),
    ("Banco BBVA", "FIX SCR", "AAA(arg)", "https://www.fixscr.com/emisor/view?type=emisor&id=129"),
    ("John Deere", "Moody's Local Argentina", "AA.ar", "https://moodyslocal.com.ar/sectores/entidades-financieras/companias-financieras/john-deere-credit-compania-financiera-s-a/"),
]


def importar_calificaciones(db: Session) -> dict:
    """Vuelca RATING_POR_EMISOR a la tabla (upsert por emisor — ver docstring del módulo:
    curaduría manual, no scraping en vivo). Se corre a mano cuando se revisa la tabla, no hay
    cadencia automática en el scheduler (a diferencia de importar_cauciones)."""
    filas_existentes = {fila.emisor: fila for fila in db.query(CalificacionCrediticia).all()}
    guardados = 0
    for emisor, agencia, rating_letra, fuente_url in RATING_POR_EMISOR:
        fila = filas_existentes.get(emisor)
        if fila is None:
            fila = CalificacionCrediticia(emisor=emisor)
            db.add(fila)
        fila.agencia = agencia
        fila.rating_letra = rating_letra
        fila.rating_numerico = rating_a_numero(rating_letra)
        fila.fecha_actualizacion = _HOY_CURADURIA
        fila.fuente_url = fuente_url
        guardados += 1

    db.commit()
    return {"emisoresCurados": guardados, "fechaCuraduria": _HOY_CURADURIA.isoformat()}
