"""Evaluación formal y permanente del modelo (TESTEO INTERNO, solo admin, junto a /auditoria y /calibracion).

El cálculo vive en rentafy-servicioIA (GET /evaluacion/informe, ver evaluacion_modelo.py): este router solo hace de único punto de
entrada público, igual que calibracion.py — ese servicio escucha en 127.0.0.1 de la EC2 a propósito y el navegador nunca le pega directo.
Es solo un informe: no cambia ningún Score ni ningún peso.
"""

import requests
from fastapi import APIRouter, Depends, HTTPException

from ..config import IA_SERVICE_URL
from ..deps import require_admin

router = APIRouter(prefix="/evaluacion", tags=["evaluacion"], dependencies=[Depends(require_admin)])


@router.get("/informe")
def informe_evaluacion():
    """Última evaluación del modelo vigente (AUC con intervalo de confianza de los 9 Scores, matriz de confusión, controles de
    integridad y criterios de aceptación) más la evolución de las últimas fotos diarias."""
    try:
        respuesta = requests.get(f"{IA_SERVICE_URL}/evaluacion/informe", timeout=60)
        respuesta.raise_for_status()
        return respuesta.json()
    except requests.RequestException as exc:
        raise HTTPException(502, f"rentafy-servicioIA no disponible en {IA_SERVICE_URL}: {exc}") from exc
