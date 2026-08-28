
from pydantic import BaseModel, Field
from typing import List

class DisplayStateInfo(BaseModel):
    display_state: str = Field(..., description="Nombre del estado de visualización")

class DocumentStatesResponse(BaseModel):
    display_states: List[DisplayStateInfo] = Field(..., description="Lista de estados de documentos disponibles")