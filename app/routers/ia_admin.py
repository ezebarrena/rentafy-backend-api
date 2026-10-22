"""Informes del servicio de IA para el panel de administración (TESTEO INTERNO, solo admin): auditoría, calibración, evaluación, oportunidades y estado de los procesos.

El cálculo de cada uno vive en rentafy-servicioIA (ver los módulos que se nombran abajo); este router solo hace de único punto de entrada público (ver ia_cliente.py). Son solo informes: no cambian
ningún Score, ningún peso ni ninguna pantalla de usuario, y exigen admin (403 a cualquier otro)."""

from fastapi import APIRouter, Depends

from ..deps import require_admin
from ..ia_cliente import consultar_ia

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/auditoria/panel", tags=["auditoria"])
def panel_auditoria():
    """TESTEO INTERNO: ¿el Score ordena los retornos que vienen después? Devuelve todos los
    horizontes y perfiles en una sola respuesta (el front elige cuál mostrar)."""
    # El primer cálculo tras reiniciar el servicio recorre todo Scoring/Cotizacion; las siguientes llamadas salen del caché del servicio de IA.
    return consultar_ia("/auditoria/panel", timeout=60)


@router.get("/calibracion/informe", tags=["calibracion"])
def informe_calibracion():
    """Qué variantes de pesos le ganan a los vigentes en walk-forward, por plazo, perfil y tipo de
    activo, más las fotos semanales anteriores. Solo es un informe: no aplica ningún peso."""
    return consultar_ia("/calibracion/informe", timeout=120)


@router.get("/evaluacion/informe", tags=["evaluacion"])
def informe_evaluacion():
    """Última evaluación del modelo vigente (AUC con intervalo de confianza de los 9 Scores, matriz de confusión, controles de
    integridad y criterios de aceptación) más la evolución de las últimas fotos diarias."""
    return consultar_ia("/evaluacion/informe", timeout=60)


@router.get("/oportunidades/informe", tags=["oportunidades"])
def informe_oportunidades():
    """Calidad del modelo fuera de muestra, matrices de confusión (walk-forward y en vivo) y las predicciones de la última fecha por horizonte."""
    return consultar_ia("/oportunidad/informe", timeout=120)


@router.get("/estado-ia/jobs", tags=["estado-ia"])
def estado_jobs():
    """Última corrida de cada job del servicio de IA y si alguno falló o está atrasado."""
    return consultar_ia("/estado/jobs", timeout=15)
