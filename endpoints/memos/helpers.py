
from typing import Optional
from models.schemas import AuthenticatedUser


def get_primary_sector_id(current_user: AuthenticatedUser) -> Optional[str]:
    for perm in current_user.permissions:
        if perm.is_primary:
            return perm.sector_id
    for perm in current_user.permissions:
        if perm.can_view:
            return perm.sector_id
    return None
