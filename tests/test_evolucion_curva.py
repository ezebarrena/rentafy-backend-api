"""Tests de evolucion_curva.py: ajuste logarítmico, fecha de comparación y lectura de empinó/aplanó, con puntos armados a mano."""

import math
from datetime import date

from app import evolucion_curva


def _puntos(a, b, durations=(0.3, 0.6, 1.0, 2.0, 3.0, 5.0)):
    return [(d, a + b * math.log(d)) for d in durations]


def test_ajustar_recupera_los_parametros_de_una_curva_exacta():
    a, b, r2 = evolucion_curva.ajustar(_puntos(8.0, 1.5))
    assert abs(a - 8.0) < 1e-9 and abs(b - 1.5) < 1e-9 and abs(r2 - 1.0) < 1e-9


def test_ajustar_ignora_duration_corta_y_pide_minimo_de_puntos():
    assert evolucion_curva.ajustar([(0.05, 5.0), (0.1, 6.0), (1.0, 8.0)]) is None


def test_fecha_de_comparacion_toma_la_rueda_mas_cercana_hacia_atras():
    hoy = date(2026, 10, 20)
    fechas = [date(2026, 9, 18), date(2026, 10, 13), date(2026, 10, 20)]
    assert evolucion_curva.fecha_de_comparacion(fechas, hoy, 7) == date(2026, 10, 13)
    assert evolucion_curva.fecha_de_comparacion(fechas, hoy, 30) == date(2026, 9, 18)
    assert evolucion_curva.fecha_de_comparacion([date(2026, 10, 20)], hoy, 30) is None


def test_la_curva_se_empina_cuando_el_plazo_largo_sube_mas_que_el_corto():
    hoy, antes = date(2026, 10, 20), date(2026, 10, 13)
    por_fecha = {hoy: _puntos(8.0, 2.0), antes: _puntos(8.0, 1.0)}
    res = evolucion_curva.evolucion_de_grupo(por_fecha, hoy, [antes, hoy])
    assert [c["etiqueta"] for c in res["curvas"]] == ["Hoy", "Hace 1 semana"]
    assert res["comparaciones"][0]["lectura"] == "empinó"
    por_fecha[antes] = _puntos(8.0, 3.0)
    assert evolucion_curva.evolucion_de_grupo(por_fecha, hoy, [antes, hoy])["comparaciones"][0]["lectura"] == "aplanó"


def test_un_cambio_parejo_de_nivel_no_cambia_la_forma():
    hoy, antes = date(2026, 10, 20), date(2026, 10, 13)
    por_fecha = {hoy: _puntos(10.0, 1.5), antes: _puntos(8.0, 1.5)}
    c = evolucion_curva.evolucion_de_grupo(por_fecha, hoy, [antes, hoy])["comparaciones"][0]
    assert c["lectura"] == "sin cambios" and abs(c["cambioNivel"] - 2.0) < 0.01
