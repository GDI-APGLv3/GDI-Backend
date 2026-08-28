
from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
from datetime import datetime

class StartSigningRequest(BaseModel):
    user_id: str = Field(..., description="UUID del usuario que inicia la firma")

class SignDocumentResponse(BaseModel):
    success: bool = Field(..., description="Indica si la firma fue exitosa")
    message: str = Field(..., description="Mensaje descriptivo de la operación")
    document_id: str = Field(..., description="UUID del documento firmado")
    signature_id: str = Field(..., description="UUID de la firma registrada")
    document_status: str = Field(..., description="Nuevo estado del documento")
    signed_at: Optional[str] = Field(None, description="Timestamp de la firma")

class SignerInfo(BaseModel):
    user_id: Optional[str] = Field(None, description="UUID del firmante (None si es ciudadano)")
    citizen_id: Optional[str] = Field(None, description="GDI-130: UUID del ciudadano firmante (None si es empleado)")
    country_id: Optional[str] = Field(None, description="GDI-130: CUIL/DNI del ciudadano firmante")
    user_name: str = Field(..., description="Nombre completo del firmante")
    email: str = Field(..., description="Email del firmante")
    profile_picture_url: Optional[str] = Field(None, description="URL de la foto de perfil del firmante")
    signing_order: int = Field(..., description="Orden de firma")
    is_numerator: bool = Field(False, description="Indica si es numerador")
    has_signed: bool = Field(False, description="Indica si ya firmó")
    signed_at: Optional[str] = Field(None, description="Timestamp de la firma")
    is_current_user: bool = Field(False, description="Indica si es el usuario actual")
    seal_name: Optional[str] = Field(None, description="Sello del firmante (para citizen: 'CIUDADANO · {CUIL}')")
    department_acronym: Optional[str] = Field(None, description="Acrónimo del departamento del firmante")
    sector_acronym: Optional[str] = Field(None, description="Acrónimo del sector del firmante")
    sector_color: Optional[str] = Field(None, description="Color del sector del firmante (sectors.primary_color)")

class DocumentSigningDetails(BaseModel):
    document_id: str = Field(..., description="UUID del documento")
    reference: str = Field(..., description="Referencia del documento")
    content: str = Field("", description="Contenido HTML del documento")
    status: str = Field(..., description="Estado actual del documento")
    document_type: Dict[str, Any] = Field(..., description="Información del tipo de documento")
    creator_name: str = Field(..., description="Nombre del creador")
    creator_profile_picture_url: Optional[str] = Field(None, description="URL de la foto de perfil del creador")
    creator_department_sector: Optional[str] = Field(None, description="Departamento y sector del creador en formato 'DEPT#SECTOR'")
    created_by: Optional[str] = Field(None, description="UUID del creador del documento")
    creator_seal_name: Optional[str] = Field(None, description="Sello del creador")
    created_at: Optional[str] = Field(None, description="Fecha de creación")
    resume: Optional[str] = Field(None, description="Resumen del documento generado por IA")
    short_resume: Optional[str] = Field(None, description="Resumen corto del documento generado por IA (1-2 oraciones)")
    has_embeddings: Optional[bool] = Field(None, description="True si el documento está indexado para RAG (tiene embeddings)")
    official_number: Optional[str] = Field(None, description="Número oficial del documento (solo si ya fue numerado/firmado)")

class SigningProgressInfo(BaseModel):
    completed: int = Field(..., description="Número de firmas completadas")
    total: int = Field(..., description="Total de firmas requeridas")
    signatures: List[SignerInfo] = Field(default_factory=list, description="Lista de firmantes")

class CurrentSignerInfo(BaseModel):
    user_id: str = Field(..., description="UUID del firmante actual")
    user_name: str = Field(..., description="Nombre del firmante actual")
    email: str = Field(..., description="Email del firmante actual")
    profile_picture_url: Optional[str] = Field(None, description="URL de la foto de perfil del firmante actual")
    signing_order: int = Field(..., description="Orden de firma")
    is_numerator: bool = Field(False, description="Indica si es numerador")
    already_signed: bool = Field(False, description="Indica si ya firmó")

class ProposedCaseInfo(BaseModel):
    case_id: str = Field(..., description="UUID del expediente")
    case_number: str = Field(..., description="Número del expediente")
    reference: Optional[str] = Field(None, description="Referencia/asunto del expediente")
    proposing_date: Optional[datetime] = Field(None, description="Fecha de propuesta de vinculación")
    is_reserved: bool = Field(False, description="GDI-069: expediente de tipo reservado (reference enmascarada)")

class LinkedCaseInfo(BaseModel):
    case_id: str = Field(..., description="UUID del expediente")
    case_number: str = Field(..., description="Número del expediente")
    reference: Optional[str] = Field(None, description="Referencia/asunto del expediente (NULL si is_reserved)")
    is_reserved: bool = Field(False, description="GDI-069: expediente de tipo reservado (reference enmascarada; el front debe pintar 🔒)")
    order_number: Optional[int] = Field(None, description="Número de orden del documento en el expediente")
    linking_date: Optional[str] = Field(None, description="Fecha de vinculación (ISO)")

class DocumentSignatureDetailsResponse(BaseModel):
    document: DocumentSigningDetails = Field(..., description="Información del documento")
    current_signer: Optional[CurrentSignerInfo] = Field(None, description="GDI-384: firmante actual, o null si quien consulta no es firmante del documento")
    signature_progress: SigningProgressInfo = Field(..., description="Progreso de firmas")
    can_sign: bool = Field(..., description="Indica si el usuario puede firmar")
    pdf_url: Optional[str] = Field(None, description="URL del PDF del documento desde Legal Orchestrator")
    message: Optional[str] = Field(None, description="Mensaje específico sobre el estado del usuario respecto al documento")
    is_sector_viewer: bool = Field(False, description="True si usuario solo puede ver por permisos de sector")
    recipients: Optional[Dict[str, Any]] = Field(None, description="Destinatarios para documentos NOTA")
    proposed_cases: Optional[List[ProposedCaseInfo]] = Field(None, description="Expedientes propuestos para vincular")
    auto_link_on_sign: bool = Field(False, description="True si alguna propuesta activa se vinculará automáticamente al firmar (MEJORA-2)")
    linked_cases: Optional[List[LinkedCaseInfo]] = Field(None, description="Expedientes vinculados reales (case_official_documents) (MEJORA-3)")
    signature_policy: Optional[str] = Field(None, description="Política de firma: electronic | digital_all | digital_num")

class NumeratorInfo(BaseModel):
    user_id: str = Field(..., description="UUID del numerador")
    name: str = Field(..., description="Nombre del numerador")
    email: str = Field(..., description="Email del numerador")

class StartSigningProcessResponse(BaseModel):
    success: bool = Field(..., description="Indica si el proceso se inició exitosamente")
    message: str = Field(..., description="Mensaje descriptivo")

class SignNumeratorResponse(BaseModel):
    success: bool = Field(..., description="Indica si la firma y numeración fue exitosa")
    message: str = Field(..., description="Mensaje descriptivo del resultado")
    document_id: str = Field(..., description="UUID del documento firmado")
    numerator_id: str = Field(..., description="UUID del numerador que firmó")
    official_number: Optional[str] = Field(None, description="Número oficial asignado (solo si fue exitoso)")
    signed_pdf_url: Optional[str] = Field(None, description="URL del PDF firmado (solo si fue exitoso)")
    signed_at: Optional[datetime] = Field(None, description="Timestamp de la firma (solo si fue exitosa)")
    retry_needed: Optional[bool] = Field(None, description="Indica si se necesita reintentar (solo si falló)")
    error_details: Optional[str] = Field(None, description="Detalles del error (solo si falló)")