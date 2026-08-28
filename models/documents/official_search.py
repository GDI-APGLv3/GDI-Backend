
from pydantic import BaseModel, Field
from typing import Optional
from models.users.user_documents import UserDocumentInfo

class OfficialDocumentSearchResponse(BaseModel):
    found: bool = Field(..., description="Indica si se encontró el documento")
    document: Optional[UserDocumentInfo] = Field(None, description="Documento oficial encontrado (null si no existe)")
    search_term: str = Field(..., description="Término de búsqueda utilizado")