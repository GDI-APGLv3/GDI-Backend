"""
Modelos Pydantic para metadatos de documentos.
Define los esquemas para información de tipos y estados de documentos.

Nota: DocumentTypeInfo fue movida a models/shared/base.py para evitar duplicación.
"""

from pydantic import BaseModel, Field
from typing import List, Dict, Any

# Import del modelo canónico compartido
from models.shared.base import DocumentTypeInfo


class DocumentTypesResponse(BaseModel):
    """Response para listado de tipos de documentos."""
    document_types: List[DocumentTypeInfo] = Field(..., description="Lista de tipos de documentos disponibles")

class DisplayStateInfo(BaseModel):
    """Información de un estado de visualización de documento."""
    display_state: str = Field(..., description="Nombre del estado de visualización")

class DocumentStatesResponse(BaseModel):
    """Response para listado de estados de documentos."""
    display_states: List[DisplayStateInfo] = Field(..., description="Lista de estados de documentos disponibles")