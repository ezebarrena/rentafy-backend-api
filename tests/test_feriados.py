"""Tests de feriados.py (sin red: se reemplaza la descarga)."""

from datetime import date

from app import feriados


def _con_descarga(funcion, cuerpo):
    original, cache = feriados._descargar, dict(feriados._cache)
    feriados._descargar = funcion
    feriados._cache.clear()
    try:
        cuerpo()
    finally:
        feriados._descargar = original
        feriados._cache.clear()
        feriados._cache.update(cache)


def _falla(anio):
    raise OSError("sin red")


def test_el_respaldo_marca_el_12_de_octubre_y_no_los_puentes():
    def cuerpo():
        assert feriados.es_feriado(date(2026, 10, 12))
        assert not feriados.es_dia_de_rueda(date(2026, 10, 12))  # lunes feriado
        assert feriados.es_dia_de_rueda(date(2026, 10, 13))
        assert feriados.es_dia_de_rueda(date(2026, 7, 10))  # puente turístico: el mercado opera
        assert not feriados.es_dia_de_rueda(date(2026, 10, 10))  # sábado

    _con_descarga(_falla, cuerpo)


def test_usa_la_respuesta_de_la_api():
    def descarga(anio):
        return frozenset({date(2026, 10, 12), date(2026, 10, 14)})

    def cuerpo():
        assert not feriados.es_dia_de_rueda(date(2026, 10, 14))
        assert feriados.es_dia_de_rueda(date(2026, 10, 15))

    _con_descarga(descarga, cuerpo)


def test_sin_red_ni_respaldo_cuenta_como_dia_de_rueda():
    def cuerpo():
        assert feriados.es_dia_de_rueda(date(2031, 10, 13))  # lunes de un año sin respaldo

    _con_descarga(_falla, cuerpo)


def test_si_la_api_falla_despues_de_funcionar_se_usa_lo_ultimo_que_se_bajo():
    def cuerpo():
        feriados._cache[2026] = (0.0, frozenset({date(2026, 10, 14)}))  # ya vencido: obliga a reconsultar
        assert not feriados.es_dia_de_rueda(date(2026, 10, 14))  # la descarga falla y queda lo anterior
        assert feriados.es_dia_de_rueda(date(2026, 10, 12))  # no el respaldo

    _con_descarga(_falla, cuerpo)


def test_semanal_corre_el_lunes_y_el_martes_si_el_lunes_fue_feriado():
    def cuerpo():
        f = feriados.es_primer_dia_de_rueda_de_la_semana
        assert f(date(2026, 10, 5))  # lunes común
        assert not f(date(2026, 10, 6))  # martes de una semana normal
        assert not f(date(2026, 10, 12))  # lunes feriado
        assert f(date(2026, 10, 13))  # martes: primer día hábil
        assert not f(date(2026, 10, 14))
        assert not f(date(2026, 10, 10))  # sábado

    _con_descarga(_falla, cuerpo)
