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

import functools
import logging
import time
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .cauciones import importar_cauciones
from .database import SessionFinanciera, SessionNoFinanciera
from .feriados import es_dia_de_rueda
from .financial_utils import actualizar_todo as actualizar_financial_utils
from .ingest import importar
from .notificaciones import generar as generar_notificaciones
from .referencias import actualizar as actualizar_referencias
from .tir_externo import completar_tir_faltante

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
            # La TIR de TAMAR/DUAL/dólar linked solo la trae el relleno externo (ver tir_externo.py), que necesita las cotizaciones de hoy ya
            # cargadas. Antes dependía solo del horario de las 18:07: si el import se demoraba, o la máquina arrancaba tarde y los dos jobs se
            # disparaban a la vez, el relleno corría sin filas que completar y ese día quedaba sin TIR. Encadenarlo acá lo deja siempre
            # después del import; el job de las 18:07 queda como red de seguridad (solo completa lo que siga vacío).
            _completar_tir_faltante()
            return
        except Exception as exc:  # noqa: BLE001 — se loguea y se reintenta, no debe tumbar el proceso
            logger.warning("Intento %s/%s de actualización diaria falló: %s", intento, MAX_REINTENTOS, exc)
            if intento < MAX_REINTENTOS:
                time.sleep(ESPERA_ENTRE_REINTENTOS_SEG)
        finally:
            db.close()
    logger.error("Actualización diaria de compararfondos.com.ar falló tras %s intentos.", MAX_REINTENTOS)


def _actualizar_financial_utils() -> None:
    """REM (BCRA), Riesgo País y Dólar CCL/MEP (ArgentinaDatos) — ver financial_utils.py.
    Indicadores secundarios: un solo intento cada uno, sin reintentos ni bloquear la
    actualización del catálogo si alguna fuente externa no responde."""
    db = SessionFinanciera()
    try:
        actualizar_financial_utils(db)
    finally:
        db.close()


def _completar_tir_faltante() -> None:
    """Ver tir_externo.py — corre DESPUÉS de _actualizar_catalogo (ya tiene que estar la
    cotización de hoy) y ANTES de que el Servicio de IA arranque a las 18:10, para que el
    Motor de Scoring de hoy ya encuentre la TIR rellena. Un solo intento (no MAX_REINTENTOS):
    si bonistas.com no responde hoy, esos instrumentos simplemente quedan sin TIR un día más,
    no es motivo para reintentar y atrasar la cadena de jobs de las 18hs."""
    db = SessionFinanciera()
    try:
        resultado = completar_tir_faltante(db)
        logger.info("Relleno de TIR externa OK: %s", resultado)
    except Exception as exc:  # noqa: BLE001 — se loguea, no debe tumbar el proceso ni bloquear lo que sigue
        logger.warning("Relleno de TIR externa falló: %s", exc)
    finally:
        db.close()


def _actualizar_cauciones() -> None:
    """Ver cauciones.py — sin dependencia de orden con los demás jobs de las 18hs (las cauciones
    no entran al Motor de Scoring), un solo intento como el resto de las fuentes secundarias."""
    db = SessionFinanciera()
    try:
        resultado = importar_cauciones(db)
        logger.info("Actualización diaria de cauciones OK: %s", resultado)
    except Exception as exc:  # noqa: BLE001 — se loguea, no debe tumbar el proceso
        logger.warning("Actualización diaria de cauciones falló: %s", exc)
    finally:
        db.close()


def _actualizar_referencias() -> None:
    """Ver referencias.py: baja las series (UVA, dólar MEP, plazo fijo) con las que se comparan los rendimientos. Fuente
    secundaria: un solo intento y, si falla, queda lo que ya estaba guardado."""
    db = SessionFinanciera()
    try:
        logger.info("Actualización diaria de series de referencia OK: %s", actualizar_referencias(db))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Actualización diaria de series de referencia falló: %s", exc)
    finally:
        db.close()


def _generar_notificaciones() -> None:
    """Ver notificaciones.py: avisos de vencimientos, pagos y alertas de rendimiento para los favoritos de los usuarios Pro. Corre después de que el
    backend importó el catálogo y las series de referencia (18:00-18:11) y de que el Servicio de IA calculó el Score (18:15-18:16)."""
    db_fin, db_nofin = SessionFinanciera(), SessionNoFinanciera()
    try:
        logger.info("Notificaciones OK: %s", generar_notificaciones(db_fin, db_nofin))
    except Exception as exc:  # noqa: BLE001 — se loguea, no debe tumbar el proceso
        logger.warning("Generación de notificaciones falló: %s", exc)
        db_nofin.rollback()
    finally:
        db_fin.close()
        db_nofin.close()


def _solo_en_dia_de_rueda(job: Callable[[], None]) -> Callable[[], None]:
    """Los jobs no corren sábados, domingos ni feriados nacionales (ver feriados.py): en un día sin rueda no hay cotización nueva y la
    corrida insertaría una fila con el precio viejo bajo la fecha del feriado. El chequeo está acá y no en el trigger porque APScheduler
    no conoce el calendario de feriados; el endpoint manual de importación llama a la función sin esto."""

    @functools.wraps(job)
    def envoltura() -> None:
        hoy = datetime.now(ZoneInfo(ZONA_HORARIA_MERCADO)).date()
        if not es_dia_de_rueda(hoy):
            logger.info("«%s» no corre: %s es feriado o fin de semana.", job.__name__, hoy)
            return
        job()

    return envoltura


def iniciar_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=ZONA_HORARIA_MERCADO)
    scheduler.add_job(
        _solo_en_dia_de_rueda(_actualizar_catalogo),
        # day_of_week="mon-fri": el mercado argentino no opera fines de semana, así que no hay
        # cotización nueva que traer — sin esto el job corría también sábado y domingo,
        # insertando una fila con el precio (stale) del viernes bajo la fecha del fin de semana.
        trigger=CronTrigger(hour=HORA_ACTUALIZACION, minute=0, day_of_week="mon-fri"),
        id="actualizar_compararfondos_diario",
        name=f"Actualización diaria del catálogo (compararfondos.com.ar, {HORA_ACTUALIZACION}:00 ART)",
        replace_existing=True,
        misfire_grace_time=3600,  # si el proceso estaba caído a las 18:00, la corre al levantar
    )
    scheduler.add_job(
        _solo_en_dia_de_rueda(_actualizar_financial_utils),
        trigger=CronTrigger(hour=HORA_ACTUALIZACION, minute=5, day_of_week="mon-fri"),
        id="financial_utils_diario",
        name="Actualización diaria de financial_utils (REM BCRA, Riesgo País, Dólar CCL/MEP)",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _solo_en_dia_de_rueda(_completar_tir_faltante),
        # minute=7: después de _actualizar_catalogo (18:00) y _actualizar_financial_utils
        # (18:05), antes de que rentafy-servicioIA arranque su cadena a las 18:10 (Entrenamiento
        # → Motor de Scoring, ver scheduler.py de ese repo).
        trigger=CronTrigger(hour=HORA_ACTUALIZACION, minute=7, day_of_week="mon-fri"),
        id="completar_tir_faltante_diario",
        name=f"Relleno diario de TIR externa ({HORA_ACTUALIZACION}:07 ART)",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _solo_en_dia_de_rueda(_actualizar_cauciones),
        trigger=CronTrigger(hour=HORA_ACTUALIZACION, minute=9, day_of_week="mon-fri"),
        id="actualizar_cauciones_diario",
        name=f"Actualización diaria de cauciones ({HORA_ACTUALIZACION}:09 ART)",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _solo_en_dia_de_rueda(_actualizar_referencias),
        trigger=CronTrigger(hour=HORA_ACTUALIZACION, minute=11, day_of_week="mon-fri"),
        id="actualizar_referencias_diario",
        name=f"Actualización diaria de series de referencia ({HORA_ACTUALIZACION}:11 ART)",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _solo_en_dia_de_rueda(_generar_notificaciones),
        trigger=CronTrigger(hour=HORA_ACTUALIZACION, minute=20, day_of_week="mon-fri"),
        id="generar_notificaciones_diario",
        name=f"Notificaciones de favoritos ({HORA_ACTUALIZACION}:20 ART)",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    scheduler.start()
    logger.info("Scheduler iniciado: próxima corrida %s", scheduler.get_job("actualizar_compararfondos_diario").next_run_time)
    return scheduler
