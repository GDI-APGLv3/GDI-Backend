
from pydantic import BaseModel, Field
from typing import List, Optional

from models.shared.base import DocumentTypeInfo

class SignerInfo(BaseModel):
    user_id: Optional[str] = Field(None, description="UUID del firmante (None si es firmante ciudadano TAD)")
    citizen_id: Optional[str] = Field(None, description="UUID del ciudadano firmante (GDI-130 TAD, None si es usuario GDI)")
    full_name: str = Field(..., description="Nombre completo del firmante")
    profile_picture_url: Optional[str] = Field(None, description="URL de la imagen de perfil del firmante")
    signed: bool = Field(..., description="Indica si ya firmó")
    is_numerator: bool = Field(..., description="Indica si es el numerador")

class UserDocumentInfo(BaseModel):
    id: str = Field(..., description="UUID del documento")
    reference: str = Field(..., description="Referencia del documento")
    display_status: str = Field(..., description="Estado visible para el usuario")
    updated_at: Optional[str] = Field(None, description="Fecha de última modificación (ISO format)")
    document_type: DocumentTypeInfo = Field(..., description="Información del tipo de documento")
    user_role: str = Field(..., description="Rol del usuario en el documento (creator/signer/numerator)")
    last_editor_name: Optional[str] = Field(None, description="Nombre del último editor")
    last_editor_profile_picture_url: Optional[str] = Field(None, description="URL de la imagen de perfil del último editor")
    last_editor_citizen_id: Optional[str] = Field(None, description="UUID del ciudadano (GDI-130 TAD) si el ultimo editor no es un usuario GDI")
    last_editor_citizen_country_id: Optional[str] = Field(None, description="CUIL/DNI del ciudadano (GDI-130 TAD), solo si last_editor_citizen_id esta presente")
    official_number: Optional[str] = Field(None, description="Número oficial del documento (para documentos oficializados)")
    creator_sector: Optional[str] = Field(None, description="Sector del creador en formato DEPT#SECTOR")
    sent_by_name: Optional[str] = Field(None, description="Nombre del usuario que envió el documento a firmar")
    short_resume: Optional[str] = Field(None, description="Resumen corto IA (1-2 oraciones)")
    resume: Optional[str] = Field(None, description="Resumen largo IA (párrafos completos)")
    linked_cases: Optional[List[dict]] = Field(default_factory=list, description="Expedientes vinculados [{case_id, case_number}]")
    linked_records: Optional[List[dict]] = Field(default_factory=list, description="Legajos vinculados [{record_id, record_number}]")
    signers: List[SignerInfo] = Field(default_factory=list, description="Lista de firmantes del documento con su estado")

class UserDocumentsResponse(BaseModel):
    total: Optional[int] = Field(None, description="GDI-369: siempre None. Ver has_next.")
    page: int = Field(..., description="Número de página actual")
    page_size: int = Field(..., description="Número de documentos por página")
    total_pages: Optional[int] = Field(None, description="GDI-369: siempre None. Ver has_next.")
    has_next: bool = Field(..., description="Indica si hay página siguiente")
    has_previous: bool = Field(..., description="Indica si hay página anterior")
    documents: List[UserDocumentInfo] = Field(..., description="Lista de documentos del usuario")