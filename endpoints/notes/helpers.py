"""
Helpers compartidos para endpoints de NOTAS.
Funciones de utilidad para extraer permisos del usuario.
"""

from typing import List, Dict, Any
from models.schemas import AuthenticatedUser


def get_user_permissions_for_notes(current_user: AuthenticatedUser) -> List[Dict[str, Any]]:
    """
    Extrae los permisos del usuario desde current_user.permissions.

    Args:
        current_user: Usuario autenticado con sus permisos

    Returns:
        Lista de dicts con {sector_id, can_view, can_edit, is_primary}
    """
    return [
        {
            'sector_id': perm.sector_id,
            'can_view': perm.can_view,
            'can_edit': perm.can_edit,
            'is_primary': perm.is_primary
        }
        for perm in current_user.permissions
    ]


def get_viewable_sector_ids(current_user: AuthenticatedUser) -> List[str]:
    """
    Obtiene TODOS los sector_ids donde el usuario puede ver (can_view=true).

    Args:
        current_user: Usuario autenticado con sus permisos

    Returns:
        Lista de sector_ids con can_view=true
    """
    return [perm.sector_id for perm in current_user.permissions if perm.can_view]


def get_editable_sector_ids(current_user: AuthenticatedUser) -> List[str]:
    """
    Obtiene TODOS los sector_ids donde el usuario puede editar (can_edit=true).

    Args:
        current_user: Usuario autenticado con sus permisos

    Returns:
        Lista de sector_ids con can_edit=true
    """
    return [perm.sector_id for perm in current_user.permissions if perm.can_edit]


def get_primary_sector_id(current_user: AuthenticatedUser) -> str | None:
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
