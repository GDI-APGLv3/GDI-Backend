
from typing import List, Dict, Any
from models.schemas import AuthenticatedUser


def get_user_permissions_for_notes(current_user: AuthenticatedUser) -> List[Dict[str, Any]]:
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
    return [perm.sector_id for perm in current_user.permissions if perm.can_view]


def get_editable_sector_ids(current_user: AuthenticatedUser) -> List[str]:
    return [perm.sector_id for perm in current_user.permissions if perm.can_edit]


def get_primary_sector_id(current_user: AuthenticatedUser) -> str | None:
    for perm in current_user.permissions:
        if perm.is_primary:
            return perm.sector_id
    for perm in current_user.permissions:
        if perm.can_view:
            return perm.sector_id
    return None
