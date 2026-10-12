"""Notificaciones del usuario (plan Pro): lista cronológica, contador de no leídas y marcar como leídas. Las genera notificaciones.py una vez por día
(ver scheduler.py); `POST /notificaciones/generar` las genera a mano (solo admin, para probar)."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import notificaciones
from ..deps import get_db_financiera, get_db_no_financiera, require_admin, require_pro
from ..models_no_financiera import Notificacion, Usuario
from ..schemas import ContadorNotificaciones, NotificacionesPagina, NotificacionOut

router = APIRouter(prefix="/notificaciones", tags=["notificaciones"])  # cada ruta pide require_pro; /generar pide admin


def _out(n: Notificacion) -> NotificacionOut:
    return NotificacionOut(
        id=n.id, tipo=n.tipo, ticker=n.ticker, titulo=n.titulo, mensaje=n.mensaje, fechaEvento=n.fecha_evento, creadaEn=n.creado_en.isoformat() + "Z", leida=n.leida_en is not None
    )


def _no_leidas(db: Session, usuario: Usuario) -> int:
    return db.query(Notificacion).filter(Notificacion.usuario_id == usuario.id, Notificacion.leida_en.is_(None)).count()


@router.get("", response_model=NotificacionesPagina)
def listar(
    solo_no_leidas: bool = False,
    limite: int = Query(30, ge=1, le=100),
    pagina: int = Query(1, ge=1),
    usuario: Usuario = Depends(require_pro),
    db: Session = Depends(get_db_no_financiera),
):
    """Las más nuevas primero."""
    consulta = db.query(Notificacion).filter(Notificacion.usuario_id == usuario.id)
    if solo_no_leidas:
        consulta = consulta.filter(Notificacion.leida_en.is_(None))
    total = consulta.count()
    filas = consulta.order_by(Notificacion.creado_en.desc(), Notificacion.id.desc()).offset((pagina - 1) * limite).limit(limite).all()
    return NotificacionesPagina(items=[_out(n) for n in filas], total=total, noLeidas=_no_leidas(db, usuario))


@router.get("/no-leidas", response_model=ContadorNotificaciones)
def contador(usuario: Usuario = Depends(require_pro), db: Session = Depends(get_db_no_financiera)):
    return ContadorNotificaciones(noLeidas=_no_leidas(db, usuario))


@router.post("/leer-todas", response_model=ContadorNotificaciones)
def leer_todas(usuario: Usuario = Depends(require_pro), db: Session = Depends(get_db_no_financiera)):
    ahora = datetime.utcnow()
    for n in db.query(Notificacion).filter(Notificacion.usuario_id == usuario.id, Notificacion.leida_en.is_(None)):
        n.leida_en = ahora
    db.commit()
    return ContadorNotificaciones(noLeidas=0)


@router.post("/generar")
def generar(
    _admin: Usuario = Depends(require_admin),
    db_financiera: Session = Depends(get_db_financiera),
    db_no_financiera: Session = Depends(get_db_no_financiera),
):
    """Solo admin: genera ahora las notificaciones de todos los usuarios Pro (lo mismo que hace el job de las 18:20)."""
    return notificaciones.generar(db_financiera, db_no_financiera)


@router.post("/{notificacion_id}/leer", response_model=NotificacionOut)
def leer(notificacion_id: int, usuario: Usuario = Depends(require_pro), db: Session = Depends(get_db_no_financiera)):
    n = db.query(Notificacion).filter(Notificacion.id == notificacion_id, Notificacion.usuario_id == usuario.id).first()
    if n is None:
        raise HTTPException(404, "No encontramos esa notificación")
    if n.leida_en is None:
        n.leida_en = datetime.utcnow()
        db.commit()
    return _out(n)
