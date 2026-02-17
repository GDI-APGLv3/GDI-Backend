"""
Catalog: Datos maestros y catalogos del dominio de documentos.

Contiene:
- types.py: Tipos de documentos
- states.py: Estados de visualizacion
- metadata.py: Funciones de metadata y mapeo de estados
"""

from .types import get_all_document_types
from .states import (
    get_display_state_name,
    get_all_display_states,
    get_all_state_mappings,
    DEFAULT_STATES,
    STATE_CODE_MAPPING
)
from .metadata import (
    map_display_status,
    get_document_basic_info
)

__all__ = [
    # Types
    "get_all_document_types",
    # States
    "get_display_state_name",
    "get_all_display_states",
    "get_all_state_mappings",
    "DEFAULT_STATES",
    "STATE_CODE_MAPPING",
    # Metadata
    "map_display_status",
    "get_document_basic_info"
]
