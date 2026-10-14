"""Tests de resumen_texto.py: el resumen corto va a los espacios chicos y el completo al Análisis Rentafy; los textos viejos sin marca también se parten."""

from app.resumen_texto import SEPARADOR_RESUMEN, partir_resumen, resumen_completo, resumen_corto

CORTO = "Se destaca por un rendimiento sobresaliente frente a instrumentos comparables."
CONTEXTO = "Es un bono en dólares que paga cupones y cuyo precio responde al riesgo país y a las tasas. Vence el 31/10/2028, en unos 2,1 años. Hoy rinde una TIR de 12,80% y tiene una duration de 1,9 años."


def test_con_la_marca_el_corto_es_la_oracion_de_factores_y_el_completo_suma_el_contexto():
    texto = CORTO + SEPARADOR_RESUMEN + CONTEXTO
    assert resumen_corto(texto) == CORTO
    assert resumen_completo(texto) == CORTO + " " + CONTEXTO
    assert partir_resumen(texto) == (CORTO, CONTEXTO)


def test_los_textos_guardados_antes_de_la_marca_tambien_se_parten():
    viejo = CORTO + " " + CONTEXTO  # el contexto pegado con un espacio
    assert resumen_corto(viejo) == CORTO and resumen_completo(viejo) == viejo
    con_una = "Cuenta con buena liquidez, aunque presenta volatilidad. Es una letra capitalizable en pesos a tasa fija: paga capital más interés al vencimiento. Vence el 13/11/2026, en 1 mes."
    assert resumen_corto(con_una) == "Cuenta con buena liquidez, aunque presenta volatilidad."


def test_un_texto_sin_contexto_es_igual_de_corto_y_de_completo():
    assert resumen_corto(CORTO) == CORTO and resumen_completo(CORTO) == CORTO
    assert resumen_corto("") == "" and resumen_completo(None) == ""
    # una oración de factores que contiene "Es un" en medio de la oración no se corta
    assert resumen_corto("Su precio es un reflejo de la curva; se destaca por su liquidez.") == "Su precio es un reflejo de la curva; se destaca por su liquidez."
