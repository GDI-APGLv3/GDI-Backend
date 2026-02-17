"""
Módulo de modelos para usuarios.
Exporta todos los modelos relacionados con usuarios para facilitar su importación.
"""

# Modelos de búsqueda
from .search import (
    UserSearchDetail,
    UserSearchResponse
)

# Modelos de documentos
from .user_documents import (
    UserDocumentInfo,
    UserDocumentsResponse
)

__all__ = [
    # Búsqueda
    "UserSearchDetail",
    "UserSearchResponse",
    
    # Documentos
    "UserDocumentInfo",
    "UserDocumentsResponse"
]