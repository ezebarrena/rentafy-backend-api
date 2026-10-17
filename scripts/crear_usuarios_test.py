"""Crea (o actualiza) dos cuentas de prueba, test1 y test2, con correos falsos (@example.com: no existen, no reciben nada).

Uso, desde la raíz de rentafy-backend-api y con el .env de la base que quieras tocar (local o producción):

    python -m scripts.crear_usuarios_test                 # las dos cuentas, plan Casual
    python -m scripts.crear_usuarios_test --pro           # las dos con plan Pro
    python -m scripts.crear_usuarios_test --solo test1    # una sola

La contraseña NO está escrita acá: se pide por teclado (no se muestra) o se toma de la variable de entorno TEST_PASSWORD. Tiene que cumplir las reglas del
registro (8 a 72 caracteres, una mayúscula, una minúscula y un número). Se usa la misma para las dos cuentas.

Las cuentas quedan con el correo ya confirmado (sin código de verificación pendiente), el perfil moderado a mediano plazo y la política de privacidad aceptada,
igual que una cuenta que terminó el registro. Si la cuenta ya existe, no se crea de nuevo: solo se le pone la contraseña nueva y el plan pedido.
Como los correos son falsos, "olvidé mi contraseña" no funciona con estas cuentas: para cambiarla, volvé a correr este script.
"""

import argparse
import getpass
import os
import sys
from datetime import datetime

from app.config import VERSION_TERMINOS
from app.database import SessionNoFinanciera
from app.models_no_financiera import (
    ConsentimientoUsuario,
    PerfilInversorHistorial,
    PlanUsuario,
    PlazoInversionHistorial,
    Usuario,
)
from app.schemas import validar_password_nueva
from app.security import hash_password

CUENTAS = {
    "test1": ("Test", "Uno", "test1@example.com"),
    "test2": ("Test", "Dos", "test2@example.com"),
}


def main() -> int:
    ap = argparse.ArgumentParser(description="Crea las cuentas de prueba test1 y test2.")
    ap.add_argument("--pro", action="store_true", help="dejarlas con plan Pro (por defecto, Casual)")
    ap.add_argument("--solo", choices=sorted(CUENTAS), help="crear solo una de las dos")
    args = ap.parse_args()

    password = os.environ.get("TEST_PASSWORD") or getpass.getpass("Contraseña para las cuentas de prueba: ")
    try:
        validar_password_nueva(password)
    except ValueError as e:
        print(f"Contraseña inválida: {e}")
        return 1

    db = SessionNoFinanciera()
    try:
        for clave, (nombre, apellido, email) in CUENTAS.items():
            if args.solo and args.solo != clave:
                continue
            usuario = db.query(Usuario).filter(Usuario.email == email).first()
            nuevo = usuario is None
            if nuevo:
                usuario = Usuario(nombre=nombre, apellido=apellido, email=email, password_hash=hash_password(password))
                db.add(usuario)
                db.flush()
                db.add(PerfilInversorHistorial(usuario_id=usuario.id, perfil="moderado"))
                db.add(PlazoInversionHistorial(usuario_id=usuario.id, plazo="mediano"))
                db.add(ConsentimientoUsuario(usuario_id=usuario.id, version=VERSION_TERMINOS, aceptado_en=datetime.utcnow()))
            else:
                usuario.password_hash = hash_password(password)
            plan = "pro" if args.pro else "casual"
            fila = db.query(PlanUsuario).filter(PlanUsuario.usuario_id == usuario.id).first()
            if fila is None:
                db.add(PlanUsuario(usuario_id=usuario.id, plan=plan, actualizado_en=datetime.utcnow()))
            else:
                fila.plan = plan
                fila.actualizado_en = datetime.utcnow()
            print(f"{'Creada' if nuevo else 'Actualizada'}: {email} (plan {plan})")
        db.commit()
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
