"""Modelo de oportunidades (TESTEO INTERNO, solo admin, junto a /auditoria, /calibracion y /evaluacion).

El cálculo vive en rentafy-servicioIA (GET /oportunidad/informe, ver oportunidad.py): este router solo hace de único punto de entrada público, igual
que evaluacion.py — ese servicio escucha en 127.0.0.1 de la EC2 a propósito y el navegador nunca le pega directo.
Es solo un informe: no cambia ningún Score ni ninguna pantalla de usuario, y exige admin (403 a cualquier otro).
"""

import requests
from fastapi import APIRouter, Depends, HTTPException

from ..config import IA_SERVICE_URL
from ..deps import require_admin

router = APIRouter(prefix="/oportunidades", tags=["oportunidades"], dependencies=[Depends(require_admin)])


@router.get("/informe")
def informe_oportunidades():
    """Calidad del modelo fuera de muestra, matrices de confusión (walk-forward y en vivo) y las predicciones de la última fecha por horizonte."""
    try:
        respuesta = requests.get(f"{IA_SERVICE_URL}/oportunidad/informe", timeout=120)
        respuesta.raise_for_status()
        return respuesta.json()
    except requests.RequestException as exc:
        raise HTTPException(502, f"rentafy-servicioIA no disponible en {IA_SERVICE_URL}: {exc}") from exc
