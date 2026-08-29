"""Job programado (RF-07/RF-10, RNF-05): actualiza el catálogo desde compararfondos.com.ar
una vez por día, después del cierre del mercado argentino (18:00 hora Argentina).

Corre dentro del mismo proceso de FastAPI vía APScheduler — no se agrega infraestructura
adicional (sin Celery, sin cron del sistema operativo). En producción, según el diseño de
la tesis (RNF-05, "procesamiento asincrónico"), este job se movería a un proceso/worker
separado para no compartir recursos con las solicitudes interactivas de los usuarios; acá
alcanza con que no bloquee el loop de asyncio de FastAPI, que es lo que logra APScheduler
corriendo los jobs sincrónicos en un thread pool aparte.

El endpoint manual (POST /mercado/importar/compararfondos) sigue existiendo sin cambios,
para poder disparar una actualización ad-hoc sin esperar al horario programado.
"""

import logging
import time

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .database import SessionFinanciera
from .ingest import importar

logger = logging.getLogger("rentafy.scheduler")

ZONA_HORARIA_MERCADO = "America/Argentina/Buenos_Aires"
HORA_ACTUALIZACION = 18  # después del cierre de la rueda (RF-07: cadencia mínima diaria)
MAX_REINTENTOS = 3
ESPERA_ENTRE_REINTENTOS_SEG = 30


def _actualizar_catalogo() -> None:
    """RNF-10: reintenta ante fallos temporales de comunicación con la fuente externa, en
    lugar de descartar la corrida del día directamente ante el primer error."""
    for intento in range(1, MAX_REINTENTOS + 1):
        db = SessionFinanciera()
        try:
            resultado = importar(db)
            logger.info("Actualización diaria de compararfondos.com.ar OK: %s", resultado)
            return
        except Exception as exc:  # noqa: BLE001 — se loguea y se reintenta, no debe tumbar el proceso
            logger.warning("Intento %s/%s de actualización diaria falló: %s", intento, MAX_REINTENTOS, exc)
            if intento < MAX_REINTENTOS:
                time.sleep(ESPERA_ENTRE_REINTENTOS_SEG)
        finally:
            db.close()
    logger.error("Actualización diaria de compararfondos.com.ar falló tras %s intentos.", MAX_REINTENTOS)


def iniciar_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=ZONA_HORARIA_MERCADO)
    scheduler.add_job(
        _actualizar_catalogo,
        trigger=CronTrigger(hour=HORA_ACTUALIZACION, minute=0),
        id="actualizar_compararfondos_diario",
        name=f"Actualización diaria del catálogo (compararfondos.com.ar, {HORA_ACTUALIZACION}:00 ART)",
        replace_existing=True,
        misfire_grace_time=3600,  # si el proceso estaba caído a las 18:00, la corre al levantar
    )
    scheduler.start()
    logger.info("Scheduler iniciado: próxima corrida %s", scheduler.get_job("actualizar_compararfondos_diario").next_run_time)
    return scheduler
