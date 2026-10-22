"""Cliente del servicio de IA (rentafy-servicioIA): UN solo lugar por donde el backend le pide cosas.

El servicio de IA escucha en 127.0.0.1 de la EC2 a propósito (ver rentafy-servicioIA/deploy/rentafy-servicioia.service): el navegador nunca le pega directo, el backend es el único punto de entrada público
(siempre detrás de require_admin). Acá viven el timeout, la sesión reutilizada y el error uniforme, en lugar de repetirlos en cada router."""

import logging

import requests
from fastapi import HTTPException

from .config import IA_SERVICE_URL

logger = logging.getLogger("rentafy.ia")

# Una sola sesión: reutiliza la conexión al servicio de IA en lugar de abrir una nueva en cada pedido.
_sesion = requests.Session()


def consultar_ia(ruta: str, timeout: float):
    """GET a `IA_SERVICE_URL + ruta` y devuelve el JSON. Si el servicio no responde o falla, 502 con un mensaje genérico (el detalle, con la URL interna, queda en el log y no se le muestra a quien llama)."""
    try:
        respuesta = _sesion.get(f"{IA_SERVICE_URL}{ruta}", timeout=timeout)
        respuesta.raise_for_status()
        return respuesta.json()
    except requests.RequestException as exc:
        logger.warning("rentafy-servicioIA no disponible (%s%s): %s", IA_SERVICE_URL, ruta, exc)
        raise HTTPException(502, "rentafy-servicioIA no disponible") from exc
