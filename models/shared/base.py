"""
Modelos base y compartidos utilizados por múltiples módulos del sistema.
"""

from pydantic import BaseModel, Field
from datetime import datetime
from shared.config import PaginationConfig
from typing import Optional, Literal

class BaseResponse(BaseModel):
    """Respuesta base que usan todos los endpoints"""
    success: bool = Field(..., description="Indica si la operación fue exitosa")
    message: str = Field(..., description="Mensaje descriptivo del resultado")
    timestamp: datetime = Field(default_factory=datetime.now, description="Timestamp de la respuesta")

class PaginationRequest(BaseModel):
    """Modelo para parámetros de paginación utilizados en endpoints con listas"""
    page: int = Field(1, ge=1, description="Número de página (inicia en 1)")
    page_size: int = Field(
        PaginationConfig.DEFAULT_PAGE_SIZE, 
        ge=PaginationConfig.MIN_PAGE_SIZE, 
        le=PaginationConfig.MAX_PAGE_SIZE, 
        description=f"Número de elementos por página (máximo {PaginationConfig.MAX_PAGE_SIZE})"
    )

class PaginationResponse(BaseModel):
    """Información de paginación incluida en respuestas de listas"""
    total: int = Field(..., description="Total de elementos encontrados")
    page: int = Field(..., description="Número de página actual")
    page_size: int = Field(..., description="Número de elementos por página")
    total_pages: int = Field(..., description="Número total de páginas")

class DocumentTypeInfo(BaseModel):
    """
    Información de un tipo de documento (modelo canónico compartido).

    Usado en: models/documents/types.py, models/documents/metadata.py,
    models/users/user_documents.py, models/documents/editing.py
    """
    name: str = Field(..., description="Nombre completo del tipo de documento")
    acronym: str = Field(..., description="Acrónimo o código del tipo de documento")
    type: Optional[Literal['HTML', 'Importado', 'NOTA', 'MEMO']] = Field(
        default='HTML',
        description="Tipo de fuente del documento: HTML (editor), Importado (PDF externo), NOTA (con recipients) o MEMO (memo interno)"
    )
    trust: bool = Field(default=True, description="true = gobierno (confiable), false = externo (requiere validacion)")

class UserBasicInfo(BaseModel):
    """Información básica de un usuario (reutilizada en múltiples contextos)"""
    user_id: str = Field(..., description="UUID del usuario")
    full_name: str = Field(..., description="Nombre completo del usuario")
    profile_picture_id: Optional[str] = Field(None, description="UUID de la foto de perfil")