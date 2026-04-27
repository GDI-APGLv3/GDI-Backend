"""
Helpers compartidos para endpoints de MEMOS.
Funciones de utilidad para extraer datos del usuario.

A diferencia de NOTAS (sector-based), MEMOS usa user_id directo.
"""

from typing import Optional
from models.schemas import AuthenticatedUser


def get_primary_sector_id(current_user: AuthenticatedUser) -> Optional[str]:
    """
    Obtiene el sector primario del usuario.
    Fallback: primer sector con can_view si no hay primario.

    Args:
        current_user: Usuario autenticado con sus permisos

    Returns:
        sector_id primario o None si no tiene permisos
    """
    for perm in current_user.permissions:
        if perm.is_primary:
            return perm.sector_id
    # Fallback: primer sector con can_view
    for perm in current_user.permissions:
        if perm.can_view:
            return perm.sector_id
    return None
