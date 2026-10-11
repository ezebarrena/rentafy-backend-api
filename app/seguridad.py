"""Límite de solicitudes por IP y cabeceras de seguridad (RNF-16).

Un único middleware ASGI, sin dependencias nuevas:

- Límite de solicitudes: ventana deslizante en memoria por IP. Hay dos topes (config.py): uno general y otro mucho más bajo para las rutas
  de acceso (login, registro, Google, verificación y recuperación por mail), que son las que sirven para probar contraseñas o disparar
  mails. Al pasarse responde 429 con `Retry-After`. Es por proceso: sirve tal cual con un solo proceso de uvicorn (hoy, la EC2), y como
  defensa en profundidad queda además el límite de nginx (ver deploy/nginx_seguridad.conf).
- Cabeceras de seguridad en todas las respuestas (HSTS, nosniff, anti-iframe, política de referer y de permisos, y una CSP cerrada: la
  API solo devuelve JSON). La documentación interactiva (/docs, /redoc) carga scripts de un CDN, así que ahí no se aplica la CSP.

La IP del cliente: detrás de nginx la conexión llega desde loopback y la IP real viene en `X-Real-IP` (o, si no, en el último valor de
`X-Forwarded-For`, que es el que agrega nginx; los anteriores los puede inventar el cliente). Si la conexión es de loopback y no trae ninguna
de las dos, no se puede saber quién es: NO se limita (si no, todos los usuarios compartirían un mismo contador) y se avisa una vez en el log.
"""

import logging
import threading
import time
from collections import deque

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from . import config

logger = logging.getLogger("rentafy.seguridad")

LOOPBACK = {"127.0.0.1", "::1", "localhost"}
RUTAS_DE_ACCESO = {
    ("POST", "/auth/login"),
    ("POST", "/auth/registro"),
    ("POST", "/auth/google"),
    ("POST", "/auth/verificar-email"),
    ("POST", "/auth/reenviar-codigo"),
    ("POST", "/auth/olvide-password"),
    ("POST", "/auth/restablecer-password"),
}
RUTAS_SIN_CSP = ("/docs", "/redoc", "/openapi.json")
MAX_CLAVES = 50_000  # tope de IPs recordadas: evita que un atacante llene la memoria cambiando de IP

CABECERAS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
CSP_API = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"


class Limitador:
    """Ventana deslizante: cuántas solicitudes hizo cada clave en los últimos `ventana` segundos."""

    def __init__(self, ventana: float):
        self.ventana = ventana
        self._golpes: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._ultima_limpieza = time.monotonic()

    def permitir(self, clave: str, limite: int, ahora: float | None = None) -> tuple[bool, float]:
        """(permitido, segundos hasta poder volver a intentar). Solo cuenta las solicitudes permitidas."""
        ahora = time.monotonic() if ahora is None else ahora
        corte = ahora - self.ventana
        with self._lock:
            self._limpiar(ahora, corte)
            golpes = self._golpes.setdefault(clave, deque())
            while golpes and golpes[0] <= corte:
                golpes.popleft()
            if len(golpes) >= limite:
                return False, max(golpes[0] + self.ventana - ahora, 1.0)
            golpes.append(ahora)
            return True, 0.0

    def _limpiar(self, ahora: float, corte: float) -> None:
        if ahora - self._ultima_limpieza < self.ventana and len(self._golpes) < MAX_CLAVES:
            return
        self._ultima_limpieza = ahora
        for clave in [k for k, g in self._golpes.items() if not g or g[-1] <= corte]:
            del self._golpes[clave]
        if len(self._golpes) >= MAX_CLAVES:  # sigue lleno: se descarta lo más viejo
            for clave in list(self._golpes)[: MAX_CLAVES // 10]:
                del self._golpes[clave]


def ip_cliente(request: Request) -> str | None:
    """IP real del cliente, o None si la conexión es de loopback sin cabecera de proxy (no se puede saber)."""
    par = request.client.host if request.client else None
    if par is None:
        return None
    if par not in LOOPBACK:
        return par
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    reenviado = request.headers.get("x-forwarded-for")
    if reenviado:
        return reenviado.split(",")[-1].strip()
    return None


def es_ruta_de_acceso(metodo: str, ruta: str) -> bool:
    return (metodo.upper(), ruta.rstrip("/") or "/") in RUTAS_DE_ACCESO


class SeguridadMiddleware:
    def __init__(self, app: ASGIApp, limitador: Limitador | None = None):
        self.app = app
        self.limitador = limitador or Limitador(config.RATE_LIMIT_VENTANA_SEG)
        self._avisado = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        ruta = request.url.path
        bloqueo = self._revisar(request, ruta)
        if bloqueo is not None:
            respuesta = JSONResponse(
                {"detail": "Hiciste demasiadas solicitudes en poco tiempo. Esperá un momento y volvé a intentar."},
                status_code=429,
                headers={"Retry-After": str(int(bloqueo) + 1)},
            )

            async def enviar_bloqueo(mensaje):
                if mensaje["type"] == "http.response.start":
                    mensaje["headers"] = list(mensaje.get("headers", [])) + _cabeceras(ruta)
                await send(mensaje)

            await respuesta(scope, receive, enviar_bloqueo)
            return

        async def enviar(mensaje):
            if mensaje["type"] == "http.response.start":
                mensaje["headers"] = list(mensaje.get("headers", [])) + _cabeceras(ruta)
            await send(mensaje)

        await self.app(scope, receive, enviar)

    def _revisar(self, request: Request, ruta: str) -> float | None:
        """Segundos de espera si hay que rechazar la solicitud; None si pasa."""
        if not config.RATE_LIMIT_ENABLED or request.method == "OPTIONS" or ruta == "/":
            return None
        ip = ip_cliente(request)
        if ip is None:
            ip_es_loopback = request.client is not None and request.client.host in LOOPBACK
            if ip_es_loopback and config.RATE_LIMIT_LOOPBACK:
                ip = request.client.host  # type: ignore[union-attr]
            else:
                if not self._avisado:
                    self._avisado = True
                    logger.warning("Solicitud por loopback sin X-Real-IP ni X-Forwarded-For: no se limita. ¿nginx envía esas cabeceras?")
                return None
        if es_ruta_de_acceso(request.method, ruta):
            ok, espera = self.limitador.permitir(f"acceso:{ip}", config.RATE_LIMIT_ACCESO)
            if not ok:
                logger.warning("Límite de acceso superado por %s en %s", ip, ruta)
                return espera
        ok, espera = self.limitador.permitir(f"general:{ip}", config.RATE_LIMIT_GENERAL)
        return None if ok else espera


def _cabeceras(ruta: str) -> list[tuple[bytes, bytes]]:
    pares = dict(CABECERAS)
    if not ruta.startswith(RUTAS_SIN_CSP):
        pares["Content-Security-Policy"] = CSP_API
    return [(k.lower().encode(), v.encode()) for k, v in pares.items()]
