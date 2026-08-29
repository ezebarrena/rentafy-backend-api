"""RF-50 a RF-52: lista de seguimiento (favoritos) para usuarios con sesión iniciada.

Favorito vive en la base no financiera; Instrumento vive en la financiera. Al no haber una
FK real entre ambas, el "join" se resuelve acá mismo, a mano, en dos pasos: primero se trae
la lista de tickers favoritos desde la base no financiera, y después se consulta el detalle
de esos tickers en la financiera — el mismo patrón que la tesis describe para
PERFIL_INVERSOR <-> PESO_PERFIL."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..deps import get_current_user, get_db_financiera, get_db_no_financiera
from ..models_financiera import Instrumento
from ..models_no_financiera import Favorito, Usuario
from ..schemas import InstrumentoListItem, PerfilInversor
from ..serializers import to_list_item

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


@router.get("", response_model=list[InstrumentoListItem])
def obtener_watchlist(
    perfil: PerfilInversor = "moderado",
    usuario: Usuario = Depends(get_current_user),
    db_financiera: Session = Depends(get_db_financiera),
):
    tickers = [f.instrumento_ticker for f in usuario.favoritos]
    instrumentos = db_financiera.query(Instrumento).filter(Instrumento.ticker.in_(tickers)).all()
    return [item for item in (to_list_item(i, perfil) for i in instrumentos) if item is not None]


@router.post("/{ticker}", status_code=204)
def agregar_a_watchlist(
    ticker: str,
    usuario: Usuario = Depends(get_current_user),
    db_financiera: Session = Depends(get_db_financiera),
    db_no_financiera: Session = Depends(get_db_no_financiera),
):
    ticker = ticker.upper()
    instrumento = db_financiera.query(Instrumento).filter(Instrumento.ticker == ticker).first()
    if instrumento is None:
        raise HTTPException(404, f"No se encontró el instrumento «{ticker}»")

    ya_existe = db_no_financiera.query(Favorito).filter(
        Favorito.usuario_id == usuario.id, Favorito.instrumento_ticker == ticker
    ).first()
    if ya_existe is None:
        db_no_financiera.add(Favorito(usuario_id=usuario.id, instrumento_ticker=ticker))
        db_no_financiera.commit()


@router.delete("/{ticker}", status_code=204)
def quitar_de_watchlist(
    ticker: str,
    usuario: Usuario = Depends(get_current_user),
    db_no_financiera: Session = Depends(get_db_no_financiera),
):
    ticker = ticker.upper()
    favorito = db_no_financiera.query(Favorito).filter(
        Favorito.usuario_id == usuario.id, Favorito.instrumento_ticker == ticker
    ).first()
    if favorito is not None:
        db_no_financiera.delete(favorito)
        db_no_financiera.commit()
