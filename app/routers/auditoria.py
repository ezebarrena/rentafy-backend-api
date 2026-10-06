"""Panel de auditoría del Score (TESTEO INTERNO, junto a /app/debug-score en el front).

El cálculo vive en rentafy-servicioIA (GET /auditoria/panel, ver auditoria_panel.py): este
router solo hace de único punto de entrada público, igual que debug.py con /debug/{ticker} — ese
servicio escucha en 127.0.0.1 de la EC2 a propósito y el navegador nunca le pega directo.
"""

import requests
from fastapi import APIRouter, HTTPException

from ..config import IA_SERVICE_URL

router = APIRouter(prefix="/auditoria", tags=["auditoria"])


@router.get("/panel")
def panel_auditoria():
    """TESTEO INTERNO: ¿el Score ordena los retornos que vienen después? Devuelve todos los
    horizontes y perfiles en una sola respuesta (el front elige cuál mostrar)."""
    try:
        # El primer cálculo tras reiniciar el servicio recorre todo Scoring/Cotizacion; las
        # siguientes llamadas salen del caché del servicio de IA.
        respuesta = requests.get(f"{IA_SERVICE_URL}/auditoria/panel", timeout=60)
        respuesta.raise_for_status()
        return respuesta.json()
    except requests.RequestException as exc:
        raise HTTPException(502, f"rentafy-servicioIA no disponible en {IA_SERVICE_URL}: {exc}") from exc
