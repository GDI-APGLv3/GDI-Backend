
from pydantic import BaseModel, Field
from typing import List, Optional

from models.shared.base import DocumentTypeInfo


class SectorRestriction(BaseModel):
    department_acronym: str = Field(..., description="Acrónimo del departamento")
    sector_acronym: str = Field(..., description="Acrónimo del sector")


class DocumentTypeDetail(DocumentTypeInfo):
    description: Optional[str] = Field(None, description="Descripción del tipo de documento")
    restricted_sectors: Optional[List[SectorRestriction]] = Field(None, description="Sectores habilitados (null = sin restricción)")


class DocumentTypesResponse(BaseModel):
    document_types: List[DocumentTypeDetail] = Field(..., description="Lista de tipos de documentos disponibles")