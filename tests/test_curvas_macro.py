"""Tests de curvas_macro.py: dólar de equilibrio y sensibilidad al riesgo país, con series armadas a mano."""

import math
import random
from datetime import date, timedelta

from app import curvas_macro

D0 = date(2025, 1, 1)


def test_suba_del_dolar_para_igualar():
    # 26,95 % anual a 112 días: (1,2695)^(112/365) - 1 ≈ 7,6 %
    assert abs(curvas_macro.suba_del_dolar_para_igualar(26.95, 112) - 7.6) < 0.1
    assert abs(curvas_macro.suba_del_dolar_para_igualar(30.0, 365) - 30.0) < 1e-9  # a un año exacto, la suba es la TIR
    assert curvas_macro.suba_del_dolar_para_igualar(0.0, 100) == 0.0


def _series(beta_por_100pb, dias=300, ruido=0.0, seed=1):
    """Riesgo país con pasos aleatorios y un precio que cae `beta_por_100pb` (en log) por cada +100 pb, más ruido."""
    azar = random.Random(seed)
    riesgo, precios = {}, []
    rp, ln_p = 700.0, math.log(60.0)
    for i in range(dias):
        f = D0 + timedelta(days=i)
        paso = azar.gauss(0, 12)
        rp += paso
        ln_p += beta_por_100pb * paso / 100 + azar.gauss(0, ruido)
        riesgo[f] = rp
        precios.append((f, math.exp(ln_p)))
    return precios, riesgo


def test_recupera_la_sensibilidad_conocida():
    precios, riesgo = _series(-0.04)
    r = curvas_macro.sensibilidad_al_riesgo_pais(precios, riesgo)
    assert r is not None
    assert abs(r["porCada100pb"] - (math.exp(-0.04) - 1) * 100) < 0.05
    assert r["r2"] > 0.99 and r["observaciones"] > 250


def test_un_cupon_que_baja_el_precio_de_golpe_no_distorsiona():
    precios, riesgo = _series(-0.04, ruido=0.002)
    precios = [(f, p * (0.95 if i in (100, 250) else 1.0)) for i, (f, p) in enumerate(precios)]  # dos amortizaciones de -5 %
    r = curvas_macro.sensibilidad_al_riesgo_pais(precios, riesgo)
    assert r is not None and abs(r["porCada100pb"] - (math.exp(-0.04) - 1) * 100) < 0.3


def test_poca_historia_devuelve_none():
    precios, riesgo = _series(-0.04, dias=60)
    assert curvas_macro.sensibilidad_al_riesgo_pais(precios, riesgo) is None
    assert curvas_macro.sensibilidad_al_riesgo_pais([], {}) is None


def test_solo_cuentan_las_fechas_que_estan_en_las_dos_series():
    precios, riesgo = _series(-0.04)
    sin_riesgo = {f: v for f, v in riesgo.items() if f.day % 2 == 0}  # faltan los días impares
    r = curvas_macro.sensibilidad_al_riesgo_pais(precios, sin_riesgo)
    assert r is not None and abs(r["porCada100pb"] - (math.exp(-0.04) - 1) * 100) < 0.1


def test_unir_dolares_arma_una_fila_por_fecha_con_none_donde_falta_uno():
    mep = [(date(2026, 1, 1), 1000.0), (date(2026, 1, 2), 1010.0)]
    ccl = [(date(2026, 1, 2), 1050.0), (date(2026, 1, 3), 1060.0)]
    filas = curvas_macro.unir_dolares(mep, ccl, date(2026, 1, 2))
    assert filas == [
        {"fecha": date(2026, 1, 2), "mep": 1010.0, "ccl": 1050.0},
        {"fecha": date(2026, 1, 3), "mep": None, "ccl": 1060.0},
    ]


def test_inflacion_de_equilibrio():
    # tasa fija 30 % y CER + 5 %: (1,30 / 1,05) − 1 ≈ 23,8 % anual ≈ 1,8 % mensual
    anual, mensual = curvas_macro.inflacion_de_equilibrio(5.0, 30.0)
    assert abs(anual - 23.81) < 0.01 and abs(mensual - 1.80) < 0.02
    # si el CER rinde más real que la tasa nominal, la inflación de equilibrio es negativa
    assert curvas_macro.inflacion_de_equilibrio(40.0, 30.0)[0] < 0


def test_unir_inflacion_junta_el_mensual_con_el_interanual_por_mes():
    m = [(date(2026, 7, 31), 2.1), (date(2026, 8, 31), 1.7)]
    i = [(date(2026, 8, 31), 33.5)]
    assert curvas_macro.unir_inflacion(m, i, date(2026, 7, 1)) == [
        {"fecha": date(2026, 7, 31), "mensual": 2.1, "interanual": None},
        {"fecha": date(2026, 8, 31), "mensual": 1.7, "interanual": 33.5},
    ]


def test_serie_mediana_del_grupo_arranca_en_cero_y_exige_tres_instrumentos():
    from datetime import date, timedelta

    from app.routers.curvas_macro import serie_mediana_del_grupo

    inicio = date(2026, 9, 1)
    def precios(final):  # +final en 10 días, escalonado
        return [(inicio + timedelta(days=d), 100 + final * d / 10) for d in range(0, 11)]
    grupo = {"A": precios(1.0), "B": precios(2.0), "C": precios(3.0)}
    serie = serie_mediana_del_grupo(grupo, {}, inicio, inicio + timedelta(days=10))
    assert serie[0]["mediana"] == 0.0 and serie[0]["fecha"] == "2026-09-01"
    assert abs(serie[-1]["mediana"] - 0.02) < 1e-9  # la mediana de 1 %, 2 % y 3 %
    assert serie[-1]["n"] == 3
    solo_dos = serie_mediana_del_grupo({"A": grupo["A"], "B": grupo["B"]}, {}, inicio, inicio + timedelta(days=10))
    assert len(solo_dos) == 1  # con menos de 3 instrumentos no se informa la mediana
