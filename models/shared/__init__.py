"""
Módulo de modelos compartidos.
Contiene modelos base y utilidades reutilizadas por múltiples dominios.
"""

from .base import (
    BaseResponse,
    PaginationRequest,
    PaginationResponse,
    DocumentTypeInfo,
    UserBasicInfo
)

__all__ = [
    "BaseResponse",
    "PaginationRequest", 
    "PaginationResponse",
    "DocumentTypeInfo",
    "UserBasicInfo"
]