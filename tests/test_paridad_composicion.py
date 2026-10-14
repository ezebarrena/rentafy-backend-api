"""Paridad entre la fórmula del Score del producto (app/scoring.py) y la copia única del servicio de IA (rentafy-servicioIA/app/composicion.py).

Son dos códigos independientes (cada servicio tiene su propio venv y su despliegue), así que la única forma de garantizar UNA sola versión es compararlos: acá se carga
composicion.py por ruta (es un módulo puro: su núcleo escalar no tiene dependencias, y la versión vectorizada con numpy se prueba contra él en el servicio de IA) y se recorre una grilla de factores, pesos, perfiles, plazos, tipos de activo y vencimientos. Si alguien cambia una
fórmula, una tabla de afinidad o un tope sin cambiar la otra, esta prueba falla. Se omite si el servicio de IA no está al lado (ej. un despliegue solo del backend).
"""

import importlib.util
import itertools
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

from app import scoring

RUTA = Path(__file__).resolve().parents[2] / "rentafy-servicioIA" / "app" / "composicion.py"
HOY = date(2026, 10, 13)
CLASES = ["LECAP", "BONCAP", "BONCER", "TAMAR", "DUAL", "Dólar Linked", "Bono USD", "Bono ARS", "ON"]
RIESGOS = [None, 0.0, 20.0, 40.0, 57.5, 75.0, 95.0]
ANIOS = [0.0, 0.02, 0.1, 0.3, 0.5, 0.9, 1.0, 1.4, 2.0, 3.0, 3.5, 5.0, 8.2, 14.7]
FACTORES = [
    (80.0, 60.0, 40.0, 20.0),
    (None, 60.0, 40.0, 20.0),
    (80.0, 60.0, 40.0, None),
    (None, 60.0, 40.0, None),  # 2 ausentes: el producto no muestra Score
    (10.0, 0.0, 100.0, 55.5),
    (None, None, None, None),
]


def _ia():
    spec = importlib.util.spec_from_file_location("composicion_ia", RUTA)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _inst(clase, anios):
    subtipo = clase if clase not in ("ON", "LECAP", "BONCAP") else None
    tipo = {"ON": "ON", "LECAP": "LECAP", "BONCAP": "BONCAP"}.get(clase, "BONO")
    return SimpleNamespace(tipo=tipo, subtipo=subtipo, vencimiento=HOY + timedelta(days=round(anios * 365)))


def test_las_constantes_del_ajuste_y_de_los_pesos_son_iguales():
    if not RUTA.exists():
        return
    ia = _ia()
    assert ia.AFINIDAD == scoring.AFINIDAD and ia.DESCALCE == scoring.DESCALCE and ia.BANDA_ANIOS == scoring.BANDA_ANIOS
    assert ia.MIN_ANIOS_AFINIDAD == scoring.MIN_ANIOS_AFINIDAD and ia.MULT_DESCALCE_DESPUES == scoring.MULT_DESCALCE_DESPUES
    assert (ia.TOPE_AFINIDAD, ia.MAX_RESTA_AJUSTE, ia.FRACCION_PISO) == (scoring.TOPE_AFINIDAD, scoring.MAX_RESTA_AJUSTE, scoring.FRACCION_PISO)
    assert (ia.DESCUENTO_LARGO_PUNTOS_POR_ANIO_QUE_FALTA, ia.DESCUENTO_LARGO_TOPE) == (scoring.DESCUENTO_LARGO_PUNTOS_POR_ANIO_QUE_FALTA, scoring.DESCUENTO_LARGO_TOPE)
    assert ia.MINIMO_FACTORES_AUSENTES_PARA_ANULAR == scoring.MINIMO_FACTORES_AUSENTES_PARA_ANULAR
    assert ia.CLASES_CON_RIESGO_DE_EMISOR == scoring.CLASES_CON_RIESGO_DE_EMISOR
    assert (ia.RIESGO_ATENUA_DESDE, ia.RIESGO_ATENUA_TOTAL, ia.FACTOR_MINIMO_CALIDAD) == (scoring.RIESGO_ATENUA_DESDE, scoring.RIESGO_ATENUA_TOTAL, scoring.FACTOR_MINIMO_CALIDAD)
    for perfil in ia.PERFILES:
        w = scoring.PESOS_PERFIL[perfil]
        assert ia.PESOS_PARTIDA[perfil] == (w.rendimiento, w.riesgo, w.liquidez, w.estabilidad)
    for plazo in ia.PLAZOS:
        a = scoring.AJUSTE_PLAZO[plazo]
        assert ia.AJUSTE_PLAZO[plazo] == (a["rendimiento"], a["riesgo"], a["liquidez"], a["estabilidad"])


def test_el_score_compuesto_del_servicio_de_ia_coincide_con_el_del_producto():
    if not RUTA.exists():
        return
    ia = _ia()
    pesos_base = scoring.PESOS_PERFIL
    for perfil, plazo, (r, k, l, e) in itertools.product(ia.PERFILES, ia.PLAZOS, FACTORES):
        esperado = scoring.score_base(r, k, l, e, perfil, plazo, pesos_base)
        w = ia.pesos_finales(ia.PESOS_PARTIDA[perfil], plazo)
        obtenido = ia.score_fila(r, k, l, e, tuple(w))
        if esperado is None:
            assert obtenido is None, (perfil, plazo, r, k, l, e)
        else:
            assert obtenido is not None and abs(obtenido - esperado) < 1e-6, (perfil, plazo, r, k, l, e, esperado, obtenido)


def test_el_ajuste_por_horizonte_del_servicio_de_ia_coincide_con_el_del_producto():
    if not RUTA.exists():
        return
    ia = _ia()
    for perfil, plazo, clase, anios, riesgo in itertools.product(ia.PERFILES, ia.PLAZOS, CLASES, ANIOS, RIESGOS):
        esperado = scoring.ajuste_horizonte(clase, anios, perfil, plazo, riesgo)
        af, ds = ia.ajuste_fila(clase, anios, perfil, plazo, riesgo)
        assert abs(af - esperado.afinidad) < 1e-9 and abs(ds - esperado.descalce) < 1e-9, (perfil, plazo, clase, anios, riesgo)
        for base in (5.0, 28.0, 60.0, 95.0):
            esp = scoring.aplicar_ajuste(base, esperado)
            obt = ia.aplicar_ajuste_fila(base, af, ds)
            assert abs(esp - obt) < 1e-9, (perfil, plazo, clase, anios, base)


def test_score_final_de_punta_a_punta_coincide_incluyendo_el_redondeo_a_entero():
    if not RUTA.exists():
        return
    ia = _ia()
    for perfil, plazo, clase, anios in itertools.product(ia.PERFILES, ia.PLAZOS, CLASES, ANIOS):
        for r, k, l, e in FACTORES[:5]:
            aj = scoring.ajuste_para(_inst(clase, anios), HOY, perfil, plazo, k)
            esperado = scoring.compute_score(r, k, l, e, perfil, plazo, None, aj)
            w = ia.pesos_finales(ia.PESOS_PARTIDA[perfil], plazo)
            base = ia.score_fila(r, k, l, e, tuple(w))
            if base is None:
                assert esperado is None
                continue
            af, ds = ia.ajuste_fila(clase, (_inst(clase, anios).vencimiento - HOY).days / 365.0, perfil, plazo, k)
            final = round(ia.aplicar_ajuste_fila(base, af, ds))
            assert esperado == final, (perfil, plazo, clase, anios, (r, k, l, e), esperado, final)
