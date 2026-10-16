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
    base_on = score_base(**FACTORES, perfil="conservador", plazo="corto")
    assert s_on >= round(base_on * scoring.FRACCION_PISO)  # nunca cae a 0-1 por el ajuste


def test_agresivo_largo_no_castiga_vencer_despues_y_premia_bono_usd():
    aj = ajuste_para(_inst("BONO", "Bono USD", 12.0), HOY, "agresivo", "largo")
    assert aj.descalce == 0 and aj.afinidad == 4
    assert compute_score(**FACTORES, perfil="agresivo", plazo="largo", ajuste=aj) > compute_score(**FACTORES, perfil="agresivo", plazo="largo")


def test_largo_descuenta_lo_que_vence_en_menos_de_un_anio_y_apaga_la_afinidad():
    aj = ajuste_horizonte("LECAP", 0.1, "conservador", "largo")
    assert aj.afinidad == 0 and aj.descalce > 9  # LECAP no tiene afinidad en largo y vence en semanas
    aj = ajuste_horizonte("BONCER", 0.1, "conservador", "largo")
    assert 0 < aj.afinidad < 10 / 5  # BONCER con 0,1 años: la afinidad positiva se apaga gradualmente


def test_el_ajuste_tiene_tope_y_piso():
    mal = AjusteHorizonte(clase="ON", anios=14.0, afinidad=-8.0, descalce=20.0)
    assert aplicar_ajuste(60.0, mal) == 35.0  # no resta más de MAX_RESTA_AJUSTE
    assert aplicar_ajuste(40.0, mal) == 20.0  # no baja de la mitad del Score base (piso proporcional)
    assert aplicar_ajuste(30.0, mal) == 15.0
    assert aplicar_ajuste(30.0, mal) != aplicar_ajuste(34.0, mal)  # el piso no empata activos distintos
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


def test_la_banda_del_horizonte_no_castiga_y_el_agresivo_sufre_menos_que_el_conservador():
    assert ajuste_horizonte("ON", 1.0, "conservador", "corto").descalce == 0  # vence dentro de la banda del corto (hasta 1 año)
    assert ajuste_horizonte("ON", 2.0, "conservador", "mediano").descalce == 0  # banda del mediano: 1 a 3 años
    assert ajuste_horizonte("ON", 0.2, "conservador", "corto").descalce == 0  # vencer antes no castiga a un horizonte corto
    cons = ajuste_horizonte("Bono USD", 6.0, "conservador", "corto").descalce
    agre = ajuste_horizonte("Bono USD", 6.0, "agresivo", "corto").descalce
    assert agre == cons * 0.5 and cons > 0


def test_la_afinidad_negativa_se_atenua_si_vence_pronto():
    pronto = ajuste_horizonte("ON", 0.2, "conservador", "corto").afinidad
    lejos = ajuste_horizonte("ON", 5.0, "conservador", "corto").afinidad
    assert lejos == -4 and -4 < pronto < -2


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


# --- retorno esperado, restricciones operativas, placeholder y punto único de entrada ---


def test_retorno_esperado_hasta_el_horizonte_con_riesgo_de_tasa():
    from app.lectura_perfil import armar_retorno_esperado

    r = armar_retorno_esperado(tir=10.0, tir_nominal_estimada=None, subtipo="Bono USD", duration=4.0, anios=8.0, moneda="USD", plazo="mediano")
    assert r is not None and not r.hastaVencimiento and r.horizonteMeses == 24
    assert abs(r.retornoPct - 21.0) < 0.01  # 1,10^2 - 1
    assert r.caidaPorPuntoDeTasa == 4.0 and "no una predicción" in r.texto and "en dólares" in r.texto


def test_retorno_esperado_hasta_el_vencimiento_si_vence_antes_que_el_horizonte():
    from app.lectura_perfil import armar_retorno_esperado

    r = armar_retorno_esperado(tir=40.0, tir_nominal_estimada=None, subtipo=None, duration=0.3, anios=0.25, moneda="ARS", plazo="largo")
    assert r is not None and r.hastaVencimiento and r.horizonteMeses == 3 and r.caidaPorPuntoDeTasa is None
    assert abs(r.retornoPct - ((1.4**0.25 - 1) * 100)) < 0.01


def test_retorno_esperado_boncer_usa_la_tir_nominal_estimada_y_sin_tir_no_hay():
    from app.lectura_perfil import armar_retorno_esperado

    r = armar_retorno_esperado(tir=5.0, tir_nominal_estimada=35.0, subtipo="BONCER", duration=1.0, anios=3.0, moneda="ARS", plazo="corto")
    assert r is not None and r.tirAnual == 35.0
    assert armar_retorno_esperado(None, None, None, None, 3.0, "ARS", "corto") is None
    assert armar_retorno_esperado(10.0, None, None, 1.0, 0.01, "ARS", "corto") is None  # vence en días


def test_una_restriccion_operativa_baja_recomendable_a_con_reservas_sin_tocar_el_score():
    from app.lectura_perfil import restricciones_operativas

    base = score_base(rendimiento=90, riesgo=90, liquidez=90, estabilidad=90, perfil="agresivo", plazo="largo")
    aj = ajuste_horizonte("Bono USD", 8.0, "agresivo", "largo")
    libre = armar_lectura(base, aj, FACTORES, "agresivo", "largo")
    rest = restricciones_operativas("Baja", False, 0)
    limitada = armar_lectura(base, aj, FACTORES, "agresivo", "largo", None, rest)
    assert libre.veredicto == "recomendable" and limitada.veredicto == "con_reservas"
    assert limitada.scoreBase == libre.scoreBase and limitada.ajuste == libre.ajuste
    assert "liquidez baja" in limitada.razones[0].lower()
    assert restricciones_operativas("Alta", False, 0) == []
    assert "cotización reciente" in restricciones_operativas("Alta", False, 7)[0]


def test_score_de_fila_aplica_el_ajuste_y_es_la_unica_via():
    from app.scoring import score_de_fila

    fila = SimpleNamespace(rendimiento=60.0, riesgo=60.0, liquidez=60.0, estabilidad=60.0, fecha_calculo=HOY, modelo_id="v1")
    inst = _inst("LECAP", None, 0.3)
    directo = compute_score(**FACTORES, perfil="conservador", plazo="corto", ajuste=ajuste_para(inst, HOY, "conservador", "corto"))
    assert score_de_fila(fila, inst, "conservador", "corto") == directo > 60


def test_el_score_aleatorio_de_reserva_no_se_muestra_salvo_que_se_pida():
    from app.models_financiera import PLACEHOLDER_MODELO_ID
    from app.scoring import es_scoring_real

    previo = config.MOSTRAR_SCORE_PLACEHOLDER
    try:
        config.MOSTRAR_SCORE_PLACEHOLDER = False
        assert not es_scoring_real(SimpleNamespace(modelo_id=PLACEHOLDER_MODELO_ID)) and es_scoring_real(SimpleNamespace(modelo_id="v-2026-10-13"))
        config.MOSTRAR_SCORE_PLACEHOLDER = True
        assert es_scoring_real(SimpleNamespace(modelo_id=PLACEHOLDER_MODELO_ID))
    finally:
        config.MOSTRAR_SCORE_PLACEHOLDER = previo


# --- atenuación por calidad y cobertura de reglas ---


def test_el_castigo_a_on_y_bonos_usd_se_atenua_con_riesgo_alto_y_no_toca_el_premio():
    from app.scoring import atenuacion_por_calidad

    sin = ajuste_horizonte("ON", 5.0, "conservador", "corto").afinidad
    sucia = ajuste_horizonte("ON", 5.0, "conservador", "corto", riesgo=20.0).afinidad
    media = ajuste_horizonte("ON", 5.0, "conservador", "corto", riesgo=57.5).afinidad
    buena = ajuste_horizonte("ON", 5.0, "conservador", "corto", riesgo=90.0).afinidad
    assert sin == sucia == -4 and abs(buena - (-4 * scoring.FACTOR_MINIMO_CALIDAD)) < 1e-9
    assert buena > media > sucia  # menos castigo cuanto menos riesgo
    assert abs(atenuacion_por_calidad("ON", 57.5) - (1 - 0.75 * 0.5)) < 1e-9
    assert atenuacion_por_calidad("LECAP", 90.0) == 1.0 and atenuacion_por_calidad("ON", None) == 1.0
    assert ajuste_horizonte("LECAP", 0.5, "conservador", "corto", riesgo=95.0).afinidad == 10 * 1.0  # el premio no se modula
    assert ajuste_horizonte("Bono USD", 12.0, "agresivo", "largo", riesgo=90.0).afinidad == 4  # tampoco el premio del agresivo
    assert ajuste_horizonte("ON", 5.0, "conservador", "corto", riesgo=90.0).atenuacion_calidad < 1.0


def test_toda_clase_tiene_regla_o_esta_declarada_sin_inclinacion_a_proposito():
    from app.scoring import CLASES, SIN_INCLINACION_A_PROPOSITO

    for perfil in scoring.AFINIDAD:
        for plazo in scoring.AFINIDAD[perfil]:
            neutras = SIN_INCLINACION_A_PROPOSITO.get((perfil, plazo), ())
            for clase in CLASES:
                en_tabla = clase in scoring.AFINIDAD[perfil][plazo]
                assert en_tabla != (clase in neutras), (perfil, plazo, clase, "o tiene regla o está declarada neutra, no las dos ni ninguna")


def test_las_reglas_nuevas_por_perfil():
    # Bono ARS: el conservador lo evita, el agresivo de largo plazo lo busca
    assert ajuste_horizonte("Bono ARS", 3.0, "conservador", "corto").afinidad < 0 < ajuste_horizonte("Bono ARS", 3.0, "agresivo", "largo").afinidad
    # el agresivo de corto plazo experimenta con tipo de cambio; el conservador no lo busca
    assert ajuste_horizonte("Dólar Linked", 1.0, "agresivo", "corto").afinidad > 0 > ajuste_horizonte("Dólar Linked", 1.0, "conservador", "corto").afinidad
    # el moderado toma tasa variable como cobertura de corto plazo, y en mediano y largo no se inclina por ningún tipo ni moneda
    assert ajuste_horizonte("TAMAR", 2.0, "moderado", "corto").afinidad > 0 and ajuste_horizonte("DUAL", 2.0, "moderado", "corto").afinidad > 0
    assert all(ajuste_horizonte(c, 3.0, "moderado", "mediano").afinidad == 0 for c in scoring.CLASES)


def test_la_lectura_avisa_que_el_bajo_riesgo_atenua_el_descuento():
    base = score_base(**FACTORES, perfil="conservador", plazo="corto")
    atenuado = armar_lectura(base, ajuste_horizonte("ON", 2.0, "conservador", "corto", riesgo=60.0), FACTORES, "conservador", "corto")  # castigo ≈ −2,3: todavía se explica
    parejo = armar_lectura(base, ajuste_horizonte("ON", 2.0, "conservador", "corto", riesgo=10.0), FACTORES, "conservador", "corto")
    assert any("atenúa ese descuento" in r for r in atenuado.razones) and not any("atenúa ese descuento" in r for r in parejo.razones)
    ars = armar_lectura(base, ajuste_horizonte("Bono ARS", 3.0, "conservador", "corto"), FACTORES, "conservador", "corto")
    assert any("tasa fija en pesos" in r for r in ars.razones)


def test_en_mediano_y_largo_no_hay_castigo_por_tipo_ni_moneda_para_conservador_y_moderado():
    for perfil, plazo in (("conservador", "mediano"), ("conservador", "largo"), ("moderado", "corto"), ("moderado", "mediano"), ("moderado", "largo")):
        for clase in scoring.CLASES:
            for anios in (0.3, 2.0, 8.0):
                assert ajuste_horizonte(clase, anios, perfil, plazo, riesgo=20.0).afinidad >= 0, (perfil, plazo, clase)
    # el conservador de corto plazo sí mantiene el castigo a lo que no es letra ni inflación
    assert ajuste_horizonte("ON", 2.0, "conservador", "corto", riesgo=20.0).afinidad < 0
    assert ajuste_horizonte("Bono USD", 2.0, "conservador", "corto", riesgo=20.0).afinidad < 0


def test_en_largo_plazo_hay_un_premio_leve_a_dolares_en_los_tres_perfiles_sin_castigar_a_los_pesos():
    pesos = ("LECAP", "BONCAP", "BONCER", "TAMAR", "DUAL", "Dólar Linked", "Bono ARS")
    for perfil in ("conservador", "moderado", "agresivo"):
        for clase in ("Bono USD", "ON"):
            a = ajuste_horizonte(clase, 6.0, perfil, "largo", riesgo=50.0).afinidad
            assert 0 < a <= 4, (perfil, clase, a)  # leve: nunca más de 4 puntos
        for clase in pesos:
            assert ajuste_horizonte(clase, 6.0, perfil, "largo").afinidad >= 0, (perfil, clase)  # los pesos no se rechazan


def test_el_moderado_de_mediano_y_largo_premia_las_on_y_bonos_usd_de_gran_calidad_y_lo_apaga_si_vence_pronto():
    from app.scoring import ajuste_horizonte

    for plazo in ("mediano", "largo"):
        alto = ajuste_horizonte("ON", 2.0, "moderado", plazo, riesgo=85.0).afinidad
        medio = ajuste_horizonte("ON", 2.0, "moderado", plazo, riesgo=65.0).afinidad
        bajo = ajuste_horizonte("ON", 2.0, "moderado", plazo, riesgo=40.0).afinidad
        base = ajuste_horizonte("BONCER", 2.0, "moderado", plazo, riesgo=85.0).afinidad
        assert alto > medio > bajo and alto - bajo == 10.0 and base == 0.0  # máximo con Riesgo >= 80, nada con <= 50, y solo ON/Bono USD
        # vence en menos de 1 año: ya es corto plazo, el premio se apaga en proporción
        assert ajuste_horizonte("ON", 0.5, "moderado", plazo, riesgo=85.0).afinidad - ajuste_horizonte("ON", 0.5, "moderado", plazo, riesgo=40.0).afinidad == 5.0
    # no alcanza a los otros perfiles ni al corto plazo
    assert ajuste_horizonte("ON", 2.0, "conservador", "mediano", riesgo=90.0).afinidad == 0.0
    assert ajuste_horizonte("ON", 2.0, "agresivo", "mediano", riesgo=90.0).afinidad == 1.0
    assert ajuste_horizonte("ON", 2.0, "moderado", "corto", riesgo=90.0).afinidad == 0.0
