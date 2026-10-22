"""Tests de la selección de instrumentos de la "Curva única" en pesos y en dólares (routers/instrumentos.py:_puntos_curva_unica), con objetos armados a mano."""

from datetime import date, timedelta
from types import SimpleNamespace

from app.routers.instrumentos import _puntos_curva_unica

HOY = date(2026, 10, 8)


def _inst(ticker, tipo, subtipo, dias, moneda="ARS"):
    return SimpleNamespace(ticker=ticker, nombre=ticker, tipo=tipo, subtipo=subtipo, moneda=moneda, vencimiento=HOY + timedelta(days=dias))


def _cot(tir, stale=False):
    return SimpleNamespace(tir=tir, fecha=HOY, precio_stale=stale, duration=None)


def test_clasifica_por_familia_y_unidad_de_la_tir():
    insts = [
        _inst("S1", "LECAP", None, 60),
        _inst("X1", "BONO", "BONCER", 300),
        _inst("M1", "BONO", "TAMAR", 400),
        _inst("U1", "BONO", "DUAL", 500),
        _inst("D1", "BONO", "Dólar Linked", 200),
    ]
    cots = {i.ticker: _cot(10.0) for i in insts}
    r = {p.ticker: (p.familia, p.baseTir) for p in _puntos_curva_unica(insts, cots, "ARS")}
    assert r == {
        "S1": ("fija", "nominal"),
        "X1": ("cer", "real"),
        "M1": ("tamar", "nominal"),
        "U1": ("tamar", "nominal"),
        "D1": ("dolar_linked", "usd"),
    }


def test_descarta_lo_que_no_se_puede_graficar():
    insts = [
        _inst("OK", "LECAP", None, 60),
        _inst("SINTIR", "LECAP", None, 60),
        _inst("USD", "BONO", "Bono USD", 400, moneda="USD"),
        _inst("ON", "ON", None, 400),
        _inst("CORTO", "LECAP", None, 5),
        _inst("LARGO", "BONO", "BONCER", 40 * 365),
        _inst("VIEJO", "LECAP", None, 60),
    ]
    cots = {i.ticker: _cot(20.0) for i in insts}
    cots["SINTIR"] = _cot(None)
    cots["VIEJO"] = _cot(20.0, stale=True)
    assert [p.ticker for p in _puntos_curva_unica(insts, cots, "ARS")] == ["OK"]


def test_sin_cotizacion_no_entra_y_el_resultado_va_ordenado_por_plazo():
    insts = [_inst("B", "LECAP", None, 200), _inst("A", "LECAP", None, 30), _inst("C", "LECAP", None, 90)]
    r = _puntos_curva_unica(insts, {"B": _cot(20.0), "A": _cot(21.0)}, "ARS")
    assert [p.ticker for p in r] == ["A", "B"]


def test_en_dolares_entran_bonos_soberanos_y_ons_de_cualquier_plazo_razonable():
    insts = [
        _inst("AL30D", "BONO", "Bono USD", 1000, moneda="USD"),
        _inst("ONX", "ON", None, 2000, moneda="USD"),
        _inst("LARGO", "BONO", "Bono USD", 7000, moneda="USD"),
        _inst("PESOS", "LECAP", None, 60),
        _inst("ONPESOS", "ON", None, 400),
    ]
    cots = {i.ticker: _cot(8.0) for i in insts}
    r = {p.ticker: (p.familia, p.baseTir) for p in _puntos_curva_unica(insts, cots, "USD")}
    assert r == {"AL30D": ("soberano", "nominal"), "ONX": ("on", "nominal"), "LARGO": ("soberano", "nominal")}
    assert [p.ticker for p in _puntos_curva_unica(insts, cots, "ARS")] == ["PESOS"]
