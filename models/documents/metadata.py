
from pydantic import BaseModel, Field
from typing import List

from models.shared.base import DocumentTypeInfo


class DocumentTypesResponse(BaseModel):
    document_types: List[DocumentTypeInfo] = Field(..., description="Lista de tipos de documentos disponibles")

class DisplayStateInfo(BaseModel):
    display_state: str = Field(..., description="Nombre del estado de visualización")

class DocumentStatesResponse(BaseModel):
    display_states: List[DisplayStateInfo] = Field(..., description="Lista de estados de documentos disponibles")