"""Tests del rendimiento total (rendimientos.py) y de las series de referencia (referencias.py), con datos armados a mano."""

from datetime import date, timedelta

from app import referencias, rendimientos

D0 = date(2026, 9, 1)


def _serie(valores, inicio=D0):
    return [(inicio + timedelta(days=i), v) for i, v in enumerate(valores)]


def test_rendimiento_de_precio_puro():
    precios = _serie([100.0] * 10 + [110.0])  # 11 días: sube 10% en el último
    r = rendimientos.calcular(precios, [], D0, D0 + timedelta(days=10))
    assert r.estado == "ok" and abs(r.retorno - 0.10) < 1e-9


def test_el_cupon_cobrado_suma_al_rendimiento_y_uno_anterior_o_posterior_no():
    precios = _serie([100.0] * 11)
    flujos = [(D0 + timedelta(days=5), 3.0), (D0, 50.0), (D0 + timedelta(days=11), 50.0)]  # el de D0 y el posterior quedan afuera
    r = rendimientos.calcular(precios, flujos, D0, D0 + timedelta(days=10))
    assert r.estado == "ok" and abs(r.retorno - 0.03) < 1e-9


def test_una_amortizacion_que_baja_el_precio_no_se_lee_como_una_perdida():
    precios = _serie([100.0] * 5 + [50.0] * 6)  # amortiza la mitad el día 5
    r = rendimientos.calcular(precios, [(D0 + timedelta(days=5), 50.0)], D0, D0 + timedelta(days=10))
    assert r.estado == "ok" and abs(r.retorno) < 1e-9


def test_sin_historia_al_comienzo_de_la_ventana():
    precios = _serie([100.0, 101.0, 102.0], inicio=D0 + timedelta(days=8))  # el primer precio es posterior al inicio
    assert rendimientos.calcular(precios, [], D0, D0 + timedelta(days=10)).estado == "sin_historia"


def test_instrumento_que_dejo_de_cotizar_no_cuenta():
    precios = _serie([100.0] * 3)  # el último precio es del día 2 y la ventana termina el día 20
    assert rendimientos.calcular(precios, [], D0, D0 + timedelta(days=20)).estado == "sin_historia"


def test_un_salto_diario_sin_pago_es_dato_dudoso():
    precios = _serie([100.0, 100.0, 100.0, 160.0, 160.0, 160.0])  # +60% de un día al otro
    r = rendimientos.calcular(precios, [], D0, D0 + timedelta(days=5))
    assert r.estado == "dato_dudoso" and r.retorno is None


def test_un_salto_explicado_por_un_pago_no_es_dudoso():
    precios = _serie([100.0, 100.0, 70.0, 70.0])  # cae 30% el día en que paga 30
    r = rendimientos.calcular(precios, [(D0 + timedelta(days=2), 30.0)], D0, D0 + timedelta(days=3))
    assert r.estado == "ok"


def test_en_dolares_se_convierte_a_pesos_con_el_mep_de_cada_fecha():
    precios = _serie([100.0] * 11)  # el bono no se mueve en dólares
    mep = [(D0 - timedelta(days=1), 1000.0), (D0 + timedelta(days=10), 1100.0)]  # pero el dólar sube 10%
    r = rendimientos.calcular(precios, [], D0, D0 + timedelta(days=10), mep=mep)
    assert r.estado == "ok" and abs(r.retorno - 0.10) < 1e-9
    assert rendimientos.calcular(precios, [], D0, D0 + timedelta(days=10), mep=[]).estado == "sin_dolar"


def test_mediana_pide_un_minimo_de_instrumentos():
    assert rendimientos.mediana([0.01, 0.03]) is None
    assert rendimientos.mediana([0.01, 0.05, 0.03]) == 0.03


def test_variacion_de_una_serie_de_referencia():
    uva = _serie([100.0, 100.5, 101.0, 102.0], inicio=D0 - timedelta(days=1))
    assert abs(referencias.variacion(uva, D0, D0 + timedelta(days=2)) - (102.0 / 100.5 - 1)) < 1e-9
    assert referencias.variacion(uva, D0 - timedelta(days=30), D0) is None  # la serie empieza después del inicio


def test_valor_vigente_es_el_ultimo_anterior_o_igual():
    s = [(D0, 10.0), (D0 + timedelta(days=3), 12.0)]
    assert referencias.valor_en(s, D0 + timedelta(days=2)) == 10.0 and referencias.valor_en(s, D0 + timedelta(days=3)) == 12.0


def test_plazo_fijo_es_tna_promedio_por_dias_sobre_365():
    tna = [(D0 + timedelta(days=i), 30.0) for i in range(-5, 40)]
    r = referencias.rendimiento_plazo_fijo(tna, D0, D0 + timedelta(days=30))
    assert abs(r - 0.30 * 30 / 365) < 1e-9
    assert referencias.rendimiento_plazo_fijo([], D0, D0 + timedelta(days=30)) is None


def test_un_precio_mal_cargado_que_se_corrige_al_dia_siguiente_es_dato_dudoso():
    # caso real: cuatro BONCAP aparecieron un 14% más baratos un día y volvieron a su precio al siguiente; con la ventana
    # empezando justo ese día, el rendimiento daba +17% contra un precio erróneo
    precios = _serie([150.0, 149.5, 128.0, 150.4, 150.6, 150.7, 150.9])  # el día 2 es el error
    assert rendimientos.calcular(precios, [], D0 + timedelta(days=2), D0 + timedelta(days=6)).estado == "dato_dudoso"
    assert rendimientos.calcular(precios, [], D0, D0 + timedelta(days=6)).estado == "dato_dudoso"  # también si lo atraviesa


def test_una_caida_real_que_no_se_revierte_no_es_dato_dudoso():
    precios = _serie([100.0, 100.0, 90.0, 90.5, 91.0, 90.8])  # cae 10% y se queda abajo
    r = rendimientos.calcular(precios, [], D0, D0 + timedelta(days=5))
    assert r.estado == "ok" and abs(r.retorno - (-0.092)) < 1e-9


def test_en_un_instrumento_que_casi_no_se_mueve_un_error_chico_tambien_se_detecta():
    # una LECAP: se mueve ~0,05% por día, y un día aparece un 3,3% más barata y al siguiente vuelve
    precios = _serie([104.08, 104.15, 104.22, 100.77, 104.58, 104.65, 104.72, 104.84])
    assert rendimientos.calcular(precios, [], D0 + timedelta(days=2), D0 + timedelta(days=7)).estado == "dato_dudoso"


def test_en_un_instrumento_volatil_un_movimiento_de_ese_tamano_que_se_revierte_es_normal():
    precios = _serie([100.0, 102.0, 99.0, 103.0, 100.5, 104.0, 101.0, 103.5])  # sube y baja 2-3% todos los días
    assert rendimientos.calcular(precios, [], D0, D0 + timedelta(days=7)).estado == "ok"
