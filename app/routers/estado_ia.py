"""Estado de los procesos programados del servicio de IA (solo admin, TESTEO INTERNO).

El registro vive en rentafy-servicioIA (GET /estado/jobs, ver estado_jobs.py): este router solo hace de
único punto de entrada público, igual que auditoria.py — ese servicio escucha en 127.0.0.1 de la EC2 y el
navegador nunca le pega directo.
"""

import requests
from fastapi import APIRouter, Depends, HTTPException

from ..config import IA_SERVICE_URL
from ..deps import require_admin

router = APIRouter(prefix="/estado-ia", tags=["estado-ia"], dependencies=[Depends(require_admin)])


@router.get("/jobs")
def estado_jobs():
    """Última corrida de cada job del servicio de IA y si alguno falló o está atrasado."""
    try:
        respuesta = requests.get(f"{IA_SERVICE_URL}/estado/jobs", timeout=15)
        respuesta.raise_for_status()
        return respuesta.json()
    except requests.RequestException as exc:
        raise HTTPException(502, f"rentafy-servicioIA no disponible en {IA_SERVICE_URL}: {exc}") from exc
