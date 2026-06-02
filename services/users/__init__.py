"""
Servicios de usuarios - Lógica de negocio para gestión de usuarios.

Este módulo contiene servicios especializados para:
- search: Búsqueda y consultas básicas de usuarios
- management: Gestión avanzada, estadísticas y permisos

Uso:
    from services.users.search import search_users, get_user_by_id
    from services.users.management import get_user_statistics, validate_user_permissions
"""

# Importaciones de search
from .search import (
    search_users_for_autocomplete
)

# Importaciones de management
from .management import (
    get_user_statistics,
    get_user_document_activity,
    get_users_with_roles,
    get_department_users_summary,
    validate_user_permissions
)

__all__ = [
    # Search (refactorizado)
    "search_users_for_autocomplete",

    # Management
    "get_user_statistics",
    "get_user_document_activity",
    "get_users_with_roles",
    "get_department_users_summary",
    "validate_user_permissions",
]