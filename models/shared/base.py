
from pydantic import BaseModel, Field
from datetime import datetime
from shared.config import PaginationConfig
from typing import Optional, Literal

class BaseResponse(BaseModel):
    success: bool = Field(..., description="Indica si la operación fue exitosa")
    message: str = Field(..., description="Mensaje descriptivo del resultado")
    timestamp: datetime = Field(default_factory=datetime.now, description="Timestamp de la respuesta")

class PaginationRequest(BaseModel):
    page: int = Field(1, ge=1, description="Número de página (inicia en 1)")
    page_size: int = Field(
        PaginationConfig.DEFAULT_PAGE_SIZE, 
        ge=PaginationConfig.MIN_PAGE_SIZE, 
        le=PaginationConfig.MAX_PAGE_SIZE, 
        description=f"Número de elementos por página (máximo {PaginationConfig.MAX_PAGE_SIZE})"
    )

class PaginationResponse(BaseModel):
    total: int = Field(..., description="Total de elementos encontrados")
    page: int = Field(..., description="Número de página actual")
    page_size: int = Field(..., description="Número de elementos por página")
    total_pages: int = Field(..., description="Número total de páginas")

class DocumentTypeInfo(BaseModel):
    name: str = Field(..., description="Nombre completo del tipo de documento")
    acronym: str = Field(..., description="Acrónimo o código del tipo de documento")
    type: Optional[Literal['HTML', 'Importado', 'NOTA', 'MEMO']] = Field(
        default='HTML',
        description="Tipo de fuente del documento: HTML (editor), Importado (PDF externo), NOTA (con recipients) o MEMO (memo interno)"
    )
    has_fields: bool = Field(
        default=False,
        description="true = el tipo tiene formulario controlado (capacidad FFCC: fila en document_type_fields). El frontend resuelve el panel de formulario por este flag, no por 'type'."
    )
    trust: bool = Field(default=True, description="true = gobierno (confiable), false = externo (requiere validacion)")
    is_reserved: bool = Field(default=False, description="true = tipo de documento reservado (GDI-069): solo firmante o creador-draft pueden verlo")
    is_public: bool = Field(default=False, description="true = tipo de documento público (GDI-098): al firmarse, el PDF se copia sin auth al bucket público (visibility='publico')")

class UserBasicInfo(BaseModel):
    user_id: str = Field(..., description="UUID del usuario")
    full_name: str = Field(..., description="Nombre completo del usuario")
    profile_picture_id: Optional[str] = Field(None, description="UUID de la foto de perfil")