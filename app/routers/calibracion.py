"""Informe de calibración semanal de pesos (TESTEO INTERNO, solo admin, junto a /auditoria).

El cálculo vive en rentafy-servicioIA (GET /calibracion/informe, ver calibracion.py): este router solo
hace de único punto de entrada público, igual que auditoria.py — ese servicio escucha en 127.0.0.1 de la
EC2 a propósito y el navegador nunca le pega directo. Es solo un informe: no aplica ningún peso.
"""

import requests
from fastapi import APIRouter, Depends, HTTPException

from ..config import IA_SERVICE_URL
from ..deps import require_admin

router = APIRouter(prefix="/calibracion", tags=["calibracion"], dependencies=[Depends(require_admin)])


@router.get("/informe")
def informe_calibracion():
    """Qué variantes de pesos le ganan a los vigentes en walk-forward, por plazo, perfil y tipo de
    activo, más las fotos semanales anteriores."""
    try:
        respuesta = requests.get(f"{IA_SERVICE_URL}/calibracion/informe", timeout=120)
        respuesta.raise_for_status()
        return respuesta.json()
    except requests.RequestException as exc:
        raise HTTPException(502, f"rentafy-servicioIA no disponible en {IA_SERVICE_URL}: {exc}") from exc
