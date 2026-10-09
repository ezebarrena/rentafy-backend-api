"""Códigos de un solo uso por mail (verificación de cuenta y recuperación de contraseña).

Reglas de seguridad:
- 6 dígitos generados con `secrets` (aleatorio criptográfico), vigentes VIGENCIA y válidos para UN solo uso.
- Se guarda un HMAC-SHA256 del código atado al usuario y al propósito (con JWT_SECRET como clave), nunca el código:
  quien lea la base no puede usarlo, y un código de verificación no sirve para recuperar la contraseña.
- Máximo MAX_INTENTOS equivocaciones por código: al llegar, el código se descarta y hay que pedir uno nuevo (con 10^6
  combinaciones y 5 intentos no se puede adivinar probando).
- No se emite un código nuevo antes de ESPERA_REENVIO desde el último: evita que alguien use el servicio para llenar de
  mails el correo de un tercero.
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from .config import JWT_SECRET
from .models_no_financiera import CodigoEmail, Usuario

VERIFICACION = "verificacion"
RESET = "reset"
VIGENCIA = timedelta(minutes=15)
VIGENCIA_MINUTOS = int(VIGENCIA.total_seconds() // 60)
MAX_INTENTOS = 5
ESPERA_REENVIO = timedelta(seconds=60)
LARGO = 6


def _hash(usuario_id: int, proposito: str, codigo: str) -> str:
    return hmac.new(JWT_SECRET.encode(), f"{usuario_id}:{proposito}:{codigo}".encode(), hashlib.sha256).hexdigest()


def _fila(db: Session, usuario: Usuario, proposito: str) -> CodigoEmail | None:
    return db.query(CodigoEmail).filter(CodigoEmail.usuario_id == usuario.id, CodigoEmail.proposito == proposito).first()


def pendiente(db: Session, usuario: Usuario, proposito: str) -> bool:
    """Hay un código emitido y sin usar (para la verificación: la cuenta todavía no confirmó su mail)."""
    return _fila(db, usuario, proposito) is not None


def emitir(db: Session, usuario: Usuario, proposito: str, ahora: datetime | None = None) -> str | None:
    """Genera un código nuevo (reemplaza el anterior) y lo devuelve en claro, para mandarlo por mail. Devuelve None si
    todavía no pasó ESPERA_REENVIO desde el último: en ese caso no se cambia nada."""
    ahora = ahora or datetime.utcnow()
    fila = _fila(db, usuario, proposito)
    if fila is not None and ahora - fila.enviado_en < ESPERA_REENVIO:
        return None
    codigo = "".join(str(secrets.randbelow(10)) for _ in range(LARGO))
    if fila is None:
        fila = CodigoEmail(usuario_id=usuario.id, proposito=proposito)
        db.add(fila)
    fila.codigo_hash = _hash(usuario.id, proposito, codigo)
    fila.expira_en = ahora + VIGENCIA
    fila.intentos = 0
    fila.enviado_en = ahora
    db.commit()
    return codigo


def descartar(db: Session, usuario: Usuario, proposito: str) -> None:
    db.query(CodigoEmail).filter(CodigoEmail.usuario_id == usuario.id, CodigoEmail.proposito == proposito).delete()
    db.commit()


def verificar(db: Session, usuario: Usuario, proposito: str, codigo: str, ahora: datetime | None = None) -> bool:
    """True si el código es el vigente. Si es correcto lo consume (no sirve una segunda vez); si no, suma un intento y,
    al llegar a MAX_INTENTOS o vencerse, lo descarta."""
    ahora = ahora or datetime.utcnow()
    fila = _fila(db, usuario, proposito)
    if fila is None:
        return False
    if ahora > fila.expira_en:
        descartar(db, usuario, proposito)
        return False
    ingresado = "".join(c for c in (codigo or "") if c.isdigit())
    if len(ingresado) == LARGO and hmac.compare_digest(fila.codigo_hash, _hash(usuario.id, proposito, ingresado)):
        descartar(db, usuario, proposito)
        return True
    fila.intentos += 1
    if fila.intentos >= MAX_INTENTOS:
        db.delete(fila)
    db.commit()
    return False
