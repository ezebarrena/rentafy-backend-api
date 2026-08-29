"""RF-50 a RF-52: lista de seguimiento (favoritos) para usuarios con sesión iniciada."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..deps import get_current_user, get_db
from ..models import Favorito, Instrumento, Usuario
from ..schemas import InstrumentoListItem, PerfilInversor
from ..serializers import to_list_item

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.get("", response_model=list[InstrumentoListItem])
def obtener_watchlist(
    perfil: PerfilInversor = "moderado",
    usuario: Usuario = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tickers = [f.instrumento_ticker for f in usuario.favoritos]
    instrumentos = db.query(Instrumento).filter(Instrumento.ticker.in_(tickers)).all()
    return [to_list_item(i, perfil) for i in instrumentos]


@router.post("/{ticker}", status_code=204)
def agregar_a_watchlist(ticker: str, usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    ticker = ticker.upper()
    instrumento = db.query(Instrumento).filter(Instrumento.ticker == ticker).first()
    if instrumento is None:
        raise HTTPException(404, f"No se encontró el instrumento «{ticker}»")

    ya_existe = db.query(Favorito).filter(
        Favorito.usuario_id == usuario.id, Favorito.instrumento_ticker == ticker
    ).first()
    if ya_existe is None:
        db.add(Favorito(usuario_id=usuario.id, instrumento_ticker=ticker))
        db.commit()


@router.delete("/{ticker}", status_code=204)
def quitar_de_watchlist(ticker: str, usuario: Usuario = Depends(get_current_user), db: Session = Depends(get_db)):
    ticker = ticker.upper()
    favorito = db.query(Favorito).filter(
        Favorito.usuario_id == usuario.id, Favorito.instrumento_ticker == ticker
    ).first()
    if favorito is not None:
        db.delete(favorito)
        db.commit()
