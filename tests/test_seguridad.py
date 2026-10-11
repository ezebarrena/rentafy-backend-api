"""Tests del límite de solicitudes y las cabeceras de seguridad (app/seguridad.py, RNF-16)."""

import asyncio

from starlette.requests import Request

from app import config, seguridad
from app.seguridad import Limitador, SeguridadMiddleware, es_ruta_de_acceso, ip_cliente


def _request(par=("127.0.0.1", 5000), headers=None) -> Request:
    cabeceras = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "path": "/", "headers": cabeceras, "client": par, "query_string": b""})


async def _inner(scope, receive, send):
    await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": b"{}"})


def _llamar(app, metodo="GET", ruta="/instrumentos", headers=None, par=("127.0.0.1", 5000)):
    """Una solicitud HTTP directa al middleware: (status, cabeceras en minúscula)."""
    resultado = {}

    async def send(mensaje):
        if mensaje["type"] == "http.response.start":
            resultado["status"] = mensaje["status"]
            resultado["headers"] = {k.decode(): v.decode() for k, v in mensaje["headers"]}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http", "method": metodo, "path": ruta, "raw_path": ruta.encode(), "query_string": b"", "scheme": "https",
        "server": ("api", 443), "client": par, "http_version": "1.1", "root_path": "",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    asyncio.run(app(scope, receive, send))
    return resultado["status"], resultado["headers"]


def _app(general=5, acceso=2):
    config.RATE_LIMIT_GENERAL = general
    config.RATE_LIMIT_ACCESO = acceso
    return SeguridadMiddleware(_inner, Limitador(60))


def test_limitador_permite_hasta_el_tope_y_despues_bloquea():
    lim = Limitador(60)
    assert [lim.permitir("ip", 3, ahora=t)[0] for t in (0, 1, 2)] == [True, True, True]
    ok, espera = lim.permitir("ip", 3, ahora=3)
    assert not ok and 56 <= espera <= 58  # recién puede volver cuando sale la primera, a los 60 s
    assert lim.permitir("otra", 3, ahora=3)[0]  # cada IP tiene su propio contador


def test_limitador_vuelve_a_permitir_cuando_pasa_la_ventana():
    lim = Limitador(60)
    for t in (0, 1, 2):
        lim.permitir("ip", 3, ahora=t)
    assert not lim.permitir("ip", 3, ahora=30)[0]
    assert lim.permitir("ip", 3, ahora=61)[0]


def test_limitador_no_cuenta_lo_rechazado_y_olvida_las_ip_inactivas():
    lim = Limitador(10)
    lim._ultima_limpieza = 0.0  # los tiempos del test son sintéticos (arrancan en 0), no el reloj real
    for t in range(5):
        lim.permitir("ip", 2, ahora=t)  # 2 permitidas, 3 rechazadas
    assert lim.permitir("ip", 2, ahora=10.5)[0]  # la rechazada no extendió el bloqueo
    lim.permitir("vieja", 5, ahora=0)
    lim.permitir("nueva", 5, ahora=100)  # dispara la limpieza
    assert "vieja" not in lim._golpes


def test_ip_cliente_confia_en_la_cabecera_solo_si_viene_de_nginx():
    assert ip_cliente(_request(par=("203.0.113.9", 1), headers={"X-Real-IP": "1.2.3.4"})) == "203.0.113.9"  # un cliente directo no puede falsearla
    assert ip_cliente(_request(headers={"X-Real-IP": "198.51.100.7"})) == "198.51.100.7"
    assert ip_cliente(_request(headers={"X-Forwarded-For": "6.6.6.6, 198.51.100.7"})) == "198.51.100.7"  # lo anterior lo inventó el cliente
    assert ip_cliente(_request()) is None  # loopback sin cabeceras: no se sabe quién es


def test_rutas_de_acceso():
    assert es_ruta_de_acceso("POST", "/auth/login") and es_ruta_de_acceso("post", "/auth/olvide-password/")
    assert not es_ruta_de_acceso("GET", "/auth/login") and not es_ruta_de_acceso("GET", "/auth/me")


def test_el_middleware_responde_429_con_retry_after_al_pasar_el_tope_general():
    app = _app(general=3)
    cab = {"X-Real-IP": "198.51.100.7"}
    assert [_llamar(app, headers=cab)[0] for _ in range(3)] == [200, 200, 200]
    status, headers = _llamar(app, headers=cab)
    assert status == 429 and int(headers["retry-after"]) >= 1
    assert _llamar(app, headers={"X-Real-IP": "198.51.100.8"})[0] == 200  # otra IP sigue pasando


def test_las_rutas_de_acceso_tienen_un_tope_propio_mas_bajo():
    app = _app(general=100, acceso=2)
    cab = {"X-Real-IP": "198.51.100.7"}
    assert [_llamar(app, "POST", "/auth/login", cab)[0] for _ in range(3)] == [200, 200, 429]
    assert _llamar(app, "GET", "/instrumentos", cab)[0] == 200  # lo demás no se ve afectado por el tope de acceso


def test_no_limita_preflight_salud_ni_conexiones_sin_ip_conocida():
    app = _app(general=1)
    cab = {"X-Real-IP": "198.51.100.7"}
    assert [_llamar(app, "OPTIONS", "/auth/login", cab)[0] for _ in range(5)] == [200] * 5
    assert [_llamar(app, "GET", "/", cab)[0] for _ in range(5)] == [200] * 5
    assert [_llamar(app, "GET", "/instrumentos")[0] for _ in range(5)] == [200] * 5  # loopback sin cabecera: no limitar a todos juntos


def test_se_puede_desactivar_por_configuracion():
    app = _app(general=1)
    config.RATE_LIMIT_ENABLED = False
    try:
        assert [_llamar(app, headers={"X-Real-IP": "198.51.100.7"})[0] for _ in range(4)] == [200] * 4
    finally:
        config.RATE_LIMIT_ENABLED = True


def test_todas_las_respuestas_llevan_cabeceras_de_seguridad_tambien_la_429():
    app = _app(general=1)
    cab = {"X-Real-IP": "198.51.100.7"}
    for esperado in (200, 429):
        status, headers = _llamar(app, headers=cab)
        assert status == esperado
        assert headers["x-content-type-options"] == "nosniff" and headers["x-frame-options"] == "DENY"
        assert "max-age=31536000" in headers["strict-transport-security"]
        assert headers["content-security-policy"].startswith("default-src 'none'")
        assert headers["referrer-policy"] and headers["permissions-policy"]


def test_la_documentacion_interactiva_no_lleva_csp():
    app = _app()
    status, headers = _llamar(app, "GET", "/docs", {"X-Real-IP": "198.51.100.7"})
    assert status == 200 and "content-security-policy" not in headers and headers["x-content-type-options"] == "nosniff"
