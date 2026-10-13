"""Tests del ajuste del Score por horizonte (afinidad perfil-tipo y calce de plazo, scoring.py) y de la lectura para el perfil (lectura_perfil.py)."""

from datetime import date, timedelta
from types import SimpleNamespace

from app import config, scoring
from app.lectura_perfil import armar_lectura, veredicto_de
from app.scoring import AjusteHorizonte, aplicar_ajuste, ajuste_horizonte, ajuste_para, compute_score, score_base

HOY = date(2026, 10, 13)
FACTORES = dict(rendimiento=60.0, riesgo=60.0, liquidez=60.0, estabilidad=60.0)


def _inst(tipo, subtipo, anios):
    return SimpleNamespace(tipo=tipo, subtipo=subtipo, vencimiento=HOY + timedelta(days=int(anios * 365)))


def test_sin_ajuste_el_score_es_el_de_siempre():
    s = compute_score(80, 70, 60, 50, "moderado", "mediano")
    assert s == round(score_base(80, 70, 60, 50, "moderado", "mediano"))
    assert compute_score(80, 70, 60, 50, "moderado", "mediano", None, None) == s


def test_conservador_corto_prefiere_lecap_a_on_de_largo_plazo():
    lecap = ajuste_para(_inst("LECAP", None, 0.3), HOY, "conservador", "corto")
    on = ajuste_para(_inst("ON", None, 8.0), HOY, "conservador", "corto")
    s_lecap = compute_score(**FACTORES, perfil="conservador", plazo="corto", ajuste=lecap)
    s_on = compute_score(**FACTORES, perfil="conservador", plazo="corto", ajuste=on)
    assert s_lecap > 60 > s_on
    assert s_on >= scoring.PISO_AJUSTE  # nunca cae a 0-1 por el ajuste


def test_agresivo_largo_no_castiga_vencer_despues_y_premia_bono_usd():
    aj = ajuste_para(_inst("BONO", "Bono USD", 12.0), HOY, "agresivo", "largo")
    assert aj.descalce == 0 and aj.afinidad == 4
    assert compute_score(**FACTORES, perfil="agresivo", plazo="largo", ajuste=aj) > compute_score(**FACTORES, perfil="agresivo", plazo="largo")


def test_largo_descuenta_lo_que_vence_en_menos_de_un_anio_y_apaga_la_afinidad():
    aj = ajuste_horizonte("LECAP", 0.1, "conservador", "largo")
    assert aj.afinidad == 0 and aj.descalce > 6  # LECAP no tiene afinidad en largo y vence en semanas
    aj = ajuste_horizonte("BONCER", 0.1, "conservador", "largo")
    assert 0 < aj.afinidad < 8 / 5  # BONCER con 0,1 años: la afinidad positiva se apaga gradualmente


def test_el_ajuste_tiene_tope_y_piso():
    mal = AjusteHorizonte(clase="ON", anios=14.0, afinidad=-8.0, descalce=20.0)
    assert aplicar_ajuste(60.0, mal) == 35.0  # no resta más de MAX_RESTA_AJUSTE
    assert aplicar_ajuste(30.0, mal) == 15.0  # no baja del piso
    assert aplicar_ajuste(10.0, mal) == 10.0  # si ya estaba por debajo del piso, no lo sube
    bien = AjusteHorizonte(clase="LECAP", anios=0.3, afinidad=8.0, descalce=0.0)
    assert aplicar_ajuste(95.0, bien) == 100.0


def test_interruptor_apagado_devuelve_none(monkeypatch=None):
    previo = config.SCORE_AJUSTE_HORIZONTE
    try:
        config.SCORE_AJUSTE_HORIZONTE = False
        assert ajuste_para(_inst("ON", None, 5.0), HOY, "conservador", "corto") is None
    finally:
        config.SCORE_AJUSTE_HORIZONTE = previo


def test_el_corto_es_mas_restrictivo_que_el_largo_con_un_bono_de_diez_anios():
    d_corto = ajuste_horizonte("Bono USD", 10.0, "moderado", "corto").descalce
    d_med = ajuste_horizonte("Bono USD", 10.0, "moderado", "mediano").descalce
    d_largo = ajuste_horizonte("Bono USD", 10.0, "moderado", "largo").descalce
    assert d_corto > d_med > d_largo == 0


def test_veredicto_y_lectura_coherentes_con_el_score():
    assert veredicto_de(85, 4) == "recomendable"
    assert veredicto_de(85, -10) == "con_reservas"  # buen activo con mala afinidad: reservas, no "no recomendable"
    assert veredicto_de(30, -5) == "no_recomendable"
    assert veredicto_de(50, -20) == "no_recomendable"
    base = score_base(**FACTORES, perfil="conservador", plazo="corto")
    aj = ajuste_horizonte("ON", 8.0, "conservador", "corto")
    lec = armar_lectura(base, aj, FACTORES, "conservador", "corto")
    assert lec.scoreBase + lec.ajuste == compute_score(**FACTORES, perfil="conservador", plazo="corto", ajuste=aj)
    assert lec.veredicto in ("con_reservas", "no_recomendable") and 1 <= len(lec.razones) <= 3
    assert any("Vence en 8 años" in r for r in lec.razones)
    assert any("conservador" in r for r in lec.razones)


def test_lectura_cambia_con_el_perfil_para_el_mismo_activo():
    base = score_base(**FACTORES, perfil="agresivo", plazo="largo")
    aj = ajuste_horizonte("Bono USD", 12.0, "agresivo", "largo")
    lec = armar_lectura(base, aj, FACTORES, "agresivo", "largo")
    otro = armar_lectura(score_base(**FACTORES, perfil="conservador", plazo="corto"), ajuste_horizonte("Bono USD", 12.0, "conservador", "corto"), FACTORES, "conservador", "corto")
    assert lec.razones != otro.razones and lec.ajuste > 0 > otro.ajuste
