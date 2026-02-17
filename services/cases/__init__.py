"""
Dominio de Cases - Lógica de negocio para expedientes

Este módulo contiene toda la lógica relacionada con la gestión de expedientes:
- Creación de expedientes con carátulas automáticas
- Validaciones de dominio
- Gestión de transferencias y asignaciones
- Historial de movimientos
- Permisos de usuarios
- Listado y búsqueda
- Consultas individuales
- Gestión de documentos

Estructura:
    - creation.py: Lógica de creación de expedientes
    - validation.py: Validaciones de dominio
    - cover_creator.py: Generación de carátulas
    - transfer_document_creator.py: Documentos de transferencia
    - history.py: Historial de movimientos
    - permissions.py: Permisos de usuarios sobre expedientes
    - retrieval.py: Listado y búsqueda de expedientes
    - queries.py: Consultas individuales y queries SQL base
    - transfer.py: Transferencias entre sectores
    - documents.py: Gestión de documentos del expediente
"""

# Creación
from .creation import create_case_with_cover_service

# Validación
from .validation import (
    validate_and_get_user,
    validate_and_get_template
)

# Historial
from .history import (
    get_case_movements,
    create_movement,
    get_case_history
)

# Permisos
from .permissions import (
    get_user_editable_sector_ids,
    get_user_viewable_sector_ids,
    get_user_case_permissions,
    can_user_view_case,
    calculate_access_reason,
    _calculate_access_reason  # Alias para backward compatibility
)

# Listado y búsqueda
from .retrieval import (
    get_cases_by_user,
    get_cases_summary,
)

# Consultas individuales
from .queries import (
    get_case_detail,
    get_case_by_exact_number,
    get_case_by_exact_number_unrestricted,
    get_available_templates,
)

# Transferencias
from .transfer import (
    transfer_case,
    close_assignment,
    get_available_sectors_for_transfer,
    get_sector_users,
)

# Documentos
from .documents import (
    get_case_documents,
    link_official_document,
)

__all__ = [
    # Creation
    'create_case_with_cover_service',
    # Validation
    'validate_and_get_user',
    'validate_and_get_template',
    # History
    'get_case_movements',
    'create_movement',
    'get_case_history',
    # Permissions
    'get_user_editable_sector_ids',
    'get_user_viewable_sector_ids',
    'get_user_case_permissions',
    'can_user_view_case',
    'calculate_access_reason',
    '_calculate_access_reason',  # Alias para backward compatibility
    # Listado y búsqueda
    'get_cases_by_user',
    'get_cases_summary',
    # Consultas individuales
    'get_case_detail',
    'get_case_by_exact_number',
    'get_case_by_exact_number_unrestricted',
    'get_available_templates',
    # Transferencias
    'transfer_case',
    'close_assignment',
    'get_available_sectors_for_transfer',
    'get_sector_users',
    # Documentos
    'get_case_documents',
    'link_official_document',
]
