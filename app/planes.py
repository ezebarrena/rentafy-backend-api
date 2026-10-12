"""Planes Casual y Pro (por ahora sin cobro: el usuario se pasa de uno a otro con un switch, ver PUT /auth/me/plan en routers/auth.py).

Qué cambia entre planes (el front replica lo visual; el backend valida lo que importa): tope de favoritos, Analítica (/curvas-macro), rendimiento
de los favoritos y notificaciones. Los admin NO tienen un trato especial acá: tienen plan como cualquiera y pueden cambiarlo con el switch del
menú de perfil (así pueden probar los dos planes). Su rol de admin solo habilita las pantallas de testeo interno (ver require_admin)."""

from .config import PLAN_POR_DEFECTO
from .models_no_financiera import Usuario

PLAN_CASUAL = "casual"
PLAN_PRO = "pro"
PLANES = (PLAN_CASUAL, PLAN_PRO)
LIMITE_FAVORITOS = {PLAN_CASUAL: 5, PLAN_PRO: 30}
NOMBRE_PLAN = {PLAN_CASUAL: "Casual", PLAN_PRO: "Pro"}


def plan_de(usuario: Usuario) -> str:
    """Plan vigente del usuario: el de su fila en `planes_usuario`, o PLAN_POR_DEFECTO (config.py: hoy Pro, sin cobro) si nunca eligió."""
    registro = usuario.plan_registro
    return registro.plan if registro is not None and registro.plan in PLANES else PLAN_POR_DEFECTO


def limite_favoritos(usuario: Usuario) -> int:
    return LIMITE_FAVORITOS[plan_de(usuario)]
