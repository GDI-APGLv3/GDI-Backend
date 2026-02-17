"""
Modelos Pydantic para búsqueda de documentos oficiales.
Define los esquemas para buscar documentos oficiales por número exacto.
Reutiliza exactamente la misma estructura que UserDocumentInfo.
"""

from pydantic import BaseModel, Field
from typing import Optional
from models.users.user_documents import UserDocumentInfo

class OfficialDocumentSearchResponse(BaseModel):
    """
    Response para búsqueda de documento oficial por número exacto.
    Devuelve documento único o null si no se encuentra.
    Usa exactamente la misma estructura que UserDocumentInfo.
    """
    found: bool = Field(..., description="Indica si se encontró el documento")
    document: Optional[UserDocumentInfo] = Field(None, description="Documento oficial encontrado (null si no existe)")
    search_term: str = Field(..., description="Término de búsqueda utilizado")