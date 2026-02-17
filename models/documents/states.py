"""
Modelos Pydantic para estados de documentos.
Define los esquemas para información de estados de visualización.
"""

from pydantic import BaseModel, Field
from typing import List

class DisplayStateInfo(BaseModel):
    """Información de un estado de visualización de documento."""
    display_state: str = Field(..., description="Nombre del estado de visualización")

class DocumentStatesResponse(BaseModel):
    """Response para listado de estados de documentos."""
    display_states: List[DisplayStateInfo] = Field(..., description="Lista de estados de documentos disponibles")