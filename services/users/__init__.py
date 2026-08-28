
from .search import (
    search_users_for_autocomplete
)

from .management import (
    get_user_statistics,
    get_user_document_activity,
    get_users_with_roles,
    get_department_users_summary,
    validate_user_permissions
)

__all__ = [
    "search_users_for_autocomplete",

    "get_user_statistics",
    "get_user_document_activity",
    "get_users_with_roles",
    "get_department_users_summary",
    "validate_user_permissions",
]