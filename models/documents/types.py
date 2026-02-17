"""
Modelos Pydantic para tipos de documentos.
Define los esquemas para información de tipos de documentos.

Nota: DocumentTypeInfo fue movida a models/shared/base.py para evitar duplicación.
"""

from pydantic import BaseModel, Field
from typing import List, Optional

# Import del modelo canónico compartido
from models.shared.base import DocumentTypeInfo


class SectorRestriction(BaseModel):
    """Restricción de sector para un tipo de documento."""
    department_acronym: str = Field(..., description="Acrónimo del departamento")
    sector_acronym: str = Field(..., description="Acrónimo del sector")


class DocumentTypeDetail(DocumentTypeInfo):
    """DocumentTypeInfo extendido con descripción y restricciones de sector."""
    description: Optional[str] = Field(None, description="Descripción del tipo de documento")
    restricted_sectors: Optional[List[SectorRestriction]] = Field(None, description="Sectores habilitados (null = sin restricción)")


class DocumentTypesResponse(BaseModel):
    """Response para listado de tipos de documentos."""
    document_types: List[DocumentTypeDetail] = Field(..., description="Lista de tipos de documentos disponibles")