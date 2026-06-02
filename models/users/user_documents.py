"""
Modelos Pydantic para gestión de documentos de usuarios.
Define los esquemas para obtener documentos de un usuario con filtros.

Nota: DocumentTypeInfo importada desde models/shared/base.py (modelo canónico).
"""

from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

# Import del modelo canónico compartido
from models.shared.base import DocumentTypeInfo

class SignerInfo(BaseModel):
    """Información de un firmante de un documento."""
    user_id: str = Field(..., description="UUID del firmante")
    full_name: str = Field(..., description="Nombre completo del firmante")
    profile_picture_url: Optional[str] = Field(None, description="URL de la imagen de perfil del firmante")
    signed: bool = Field(..., description="Indica si ya firmó")
    is_numerator: bool = Field(..., description="Indica si es el numerador")

class UserDocumentInfo(BaseModel):
    """Información de un documento asociado a un usuario (solo campos necesarios para el frontend)."""
    id: str = Field(..., description="UUID del documento")
    reference: str = Field(..., description="Referencia del documento")
    display_status: str = Field(..., description="Estado visible para el usuario")
    updated_at: Optional[str] = Field(None, description="Fecha de última modificación (ISO format)")
    document_type: DocumentTypeInfo = Field(..., description="Información del tipo de documento")
    user_role: str = Field(..., description="Rol del usuario en el documento (creator/signer/numerator)")
    # Información del último editor (lo que realmente necesita el frontend)
    last_editor_name: Optional[str] = Field(None, description="Nombre del último editor")
    last_editor_profile_picture_url: Optional[str] = Field(None, description="URL de la imagen de perfil del último editor")
    # Campo de numeración oficial
    official_number: Optional[str] = Field(None, description="Número oficial del documento (para documentos oficializados)")
    # Sector del creador del documento
    creator_sector: Optional[str] = Field(None, description="Sector del creador en formato DEPT#SECTOR")
    # Nombre del usuario que envió a firmar
    sent_by_name: Optional[str] = Field(None, description="Nombre del usuario que envió el documento a firmar")
    # Resúmenes IA
    short_resume: Optional[str] = Field(None, description="Resumen corto IA (1-2 oraciones)")
    resume: Optional[str] = Field(None, description="Resumen largo IA (párrafos completos)")
    # Vínculos
    linked_cases: Optional[List[dict]] = Field(default_factory=list, description="Expedientes vinculados [{case_id, case_number}]")
    linked_records: Optional[List[dict]] = Field(default_factory=list, description="Legajos vinculados [{record_id, record_number}]")
    # Firmantes
    signers: List[SignerInfo] = Field(default_factory=list, description="Lista de firmantes del documento con su estado")

class UserDocumentsResponse(BaseModel):
    """Response para obtener documentos de un usuario con información de paginación completa."""
    total: int = Field(..., description="Número total de documentos encontrados")
    page: int = Field(..., description="Número de página actual")
    page_size: int = Field(..., description="Número de documentos por página")
    total_pages: int = Field(..., description="Número total de páginas")
    has_next: bool = Field(..., description="Indica si hay página siguiente")
    has_previous: bool = Field(..., description="Indica si hay página anterior")
    documents: List[UserDocumentInfo] = Field(..., description="Lista de documentos del usuario")