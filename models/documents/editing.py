"""
Modelos relacionados con la edición de documentos.
"""

from pydantic import BaseModel, Field, ConfigDict, field_validator
from datetime import datetime
from typing import Optional, List, Dict
from ..shared.base import DocumentTypeInfo
from .creation import NoteRecipientsInput

class DocumentSignerDetail(BaseModel):
    """Información de un firmante para el editor de documentos"""
    user_id: str = Field(..., description="UUID del usuario firmante")
    user_name: str = Field(..., description="Nombre completo del firmante")
    email: Optional[str] = Field(None, description="Email del firmante")
    signing_order: Optional[int] = Field(None, description="Orden de firma")
    is_numerator: bool = Field(..., description="Indica si este firmante es el numerador")
    profile_picture_url: Optional[str] = Field(None, description="URL de la foto de perfil del firmante")
    department_sector: Optional[str] = Field(None, description="Departamento y sector del firmante en formato 'DEPT#SECTOR'")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_id": "ea77e311-5bc3-467e-900b-41d9fc1503a7",
                "user_name": "Marta López",
                "email": "marta.lopez@municipio.cl",
                "signing_order": 1,
                "is_numerator": True,
                "profile_picture_url": "https://s.gravatar.com/avatar/abc123.jpg"
            }
        }
    )

class ProposedCaseInfo(BaseModel):
    """Información de un expediente propuesto para vinculación"""
    case_id: str = Field(..., description="UUID del expediente")
    case_number: str = Field(..., description="Número del expediente")
    reference: Optional[str] = Field(None, description="Referencia o asunto del expediente")
    proposing_date: Optional[datetime] = Field(None, description="Fecha de propuesta de vinculación")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "case_id": "550e8400-e29b-41d4-a716-446655440010",
                "case_number": "EXP-2025-0001234",
                "reference": "Expediente sobre licencia comercial",
                "proposing_date": "2025-02-05T14:30:45.123Z"
            }
        }
    )

class DocumentSigner(BaseModel):
    """Modelo para representar un firmante del documento"""
    user_id: Optional[str] = Field(None, description="UUID del usuario firmante (null si es email pendiente)")
    email: Optional[str] = Field(None, description="Email del firmante cuando user_id es null")
    is_numerator: bool = Field(False, description="Indica si este firmante es el numerador del documento")
    
    @field_validator('email')
    @classmethod
    def validate_user_identification(cls, v, info):
        """Valida que se proporcione user_id o email, pero no ambos"""
        # Obtener user_id de forma segura
        user_id = info.data.get('user_id') if info.data else None
        
        # Normalizar valores vacíos a None
        if user_id == "null" or user_id == "":
            user_id = None
        if v == "null" or v == "":
            v = None
            
        # Caso 1: Ni user_id ni email
        if not user_id and not v:
            raise ValueError('Se debe proporcionar user_id o email')
        
        # Caso 2: Ambos proporcionados y no vacíos
        if user_id and v:
            raise ValueError('No se puede proporcionar user_id y email al mismo tiempo')
            
        return v
    
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "user_id": "ea77e311-5bc3-467e-900b-41d9fc1503a7",
                    "email": None,
                    "is_numerator": True
                },
                {
                    "user_id": None,
                    "email": "invitado@ejemplo.com",
                    "is_numerator": False
                }
            ]
        }
    )

class SaveDocumentRequest(BaseModel):
    """Modelo para solicitud de guardado de cambios en documento"""
    reference: Optional[str] = Field(None, max_length=250, description="Nuevo asunto o referencia del documento (máximo 250 caracteres)")
    content: Optional[str] = Field(None, description="Contenido HTML del documento (se almacena como JSON)")
    signers: Optional[List[DocumentSigner]] = Field(None, description="Lista de firmantes del documento")
    recipients: Optional[NoteRecipientsInput] = Field(
        None,
        description="Destinatarios para NOTA (TO, CC, BCC). Solo aplica a documentos tipo NOTA. Se ignora para otros tipos."
    )
    proposed_case_ids: Optional[List[str]] = Field(
        None,
        description="Lista de UUIDs de expedientes a los que se propone vincular este documento. Un documento puede estar propuesto a múltiples expedientes diferentes."
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "content": "<p>Este es el contenido del documento con formato HTML para pruebas...</p>",
                "reference": "Referencia de Prueba",
                "signers": [
                    {
                        "is_numerator": True,
                        "user_id": "457c52a4-9305-4e8a-9642-0b9380a4768a"
                    }
                ],
                "recipients": {
                    "to": ["550e8400-e29b-41d4-a716-446655440001"],
                    "cc": [],
                    "bcc": []
                },
                "proposed_case_ids": [
                    "550e8400-e29b-41d4-a716-446655440010",
                    "550e8400-e29b-41d4-a716-446655440011"
                ]
            }
        }
    )

class SaveDocumentResponse(BaseModel):
    """Respuesta al guardado de cambios en documento"""
    success: bool = Field(..., description="Indica si la operación fue exitosa")
    message: str = Field(..., description="Mensaje descriptivo del resultado de la operación")
    document_id: str = Field(..., description="UUID del documento actualizado")
    last_modified_at: Optional[str] = Field(None, description="Fecha y hora de la última modificación en formato ISO")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "message": "Documento guardado exitosamente",
                "document_id": "550e8400-e29b-41d4-a716-446655440000",
                "last_modified_at": "2025-09-26T15:30:45.123Z"
            }
        }
    )

class DocumentSignerInfo(BaseModel):
    """Información completa de un firmante del documento"""
    user_id: str = Field(..., description="UUID del usuario firmante")
    full_name: str = Field(..., description="Nombre completo del firmante")
    is_numerator: bool = Field(..., description="Indica si este firmante es el numerador")
    profile_picture_id: Optional[str] = Field(None, description="UUID de la foto de perfil del firmante")
    seal_name: Optional[str] = Field(None, description="Nombre del sello del firmante")
    department_acronym: Optional[str] = Field(None, description="Acrónimo del departamento del firmante")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_id": "ea77e311-5bc3-467e-900b-41d9fc1503a7",
                "full_name": "Marta López",
                "is_numerator": True,
                "profile_picture_id": "ae45f212-5bc3-467e-900b-41d9fc150123",
                "seal_name": "Asistente Director",
                "department_acronym": "DGOBR"
            }
        }
    )

class RejectionInfo(BaseModel):
    """Información sobre el rechazo de un documento"""
    reason: str = Field(..., description="Motivo del rechazo")
    rejected_at: str = Field(..., description="Fecha y hora del rechazo en formato ISO")
    rejected_by: str = Field(..., description="UUID del usuario que rechazó")
    rejected_by_name: str = Field(..., description="Nombre completo del usuario que rechazó")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "reason": "El documento presenta errores en las fechas y montos especificados",
                "rejected_at": "2025-10-07T14:30:45.123Z",
                "rejected_by": "ea77e311-5bc3-467e-900b-41d9fc1503a7",
                "rejected_by_name": "Ana García"
            }
        }
    )

class DocumentDetailResponse(BaseModel):
    """Respuesta completa con todos los datos del documento editable"""
    document_id: str = Field(..., description="UUID del documento")
    reference: str = Field(..., description="Asunto o referencia del documento")
    content: Optional[str] = Field(None, description="Contenido HTML del documento")
    status: str = Field(..., description="Estado técnico del documento ('draft' o 'rejected')")
    document_type: DocumentTypeInfo = Field(..., description="Información del tipo de documento")
    created_by: str = Field(..., description="UUID del usuario creador")
    creator_name: str = Field(..., description="Nombre completo del usuario creador")
    creator_profile_picture_url: Optional[str] = Field(None, description="URL de la foto de perfil del creador")
    creator_department_sector: Optional[str] = Field(None, description="Departamento y sector del creador en formato 'DEPT#SECTOR'")
    signers: List[DocumentSignerDetail] = Field(default=[], description="Lista de firmantes del documento")
    rejection_info: Optional[RejectionInfo] = Field(None, description="Información del rechazo (solo si el documento fue rechazado)")
    created_at: Optional[str] = Field(None, description="Fecha y hora de creación en formato ISO")
    updated_at: Optional[str] = Field(None, description="Fecha y hora de última modificación en formato ISO")
    # Campos para documentos importados
    is_imported: bool = Field(default=False, description="Indica si el documento es importado (PDF subido)")
    pdf_url: Optional[str] = Field(None, description="URL del PDF para documentos importados")
    # Campo para resumen generado por IA
    resume: Optional[str] = Field(None, description="Resumen del documento generado por IA")
    # Campo para destinatarios de NOTA
    recipients: Optional[Dict[str, List]] = Field(
        None,
        description="Destinatarios para documentos tipo NOTA. Contiene to, cc, bcc con datos de cada sector"
    )
    # Campo para expedientes propuestos
    proposed_cases: Optional[List[ProposedCaseInfo]] = Field(
        None,
        description="Lista de expedientes a los que se propone vincular este documento"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    # Ejemplo 1: Documento en draft (sin rechazo)
                    "document_id": "550e8400-e29b-41d4-a716-446655440000",
                    "reference": "Solicitud de declaración de interés municipal",
                    "content": "<h1>Título del documento</h1><p>Contenido con formato...</p>",
                    "status": "draft",
                    "document_type": {
                        "acronym": "IF",
                        "name": "Informe"
                    },
                    "created_by": "ea77e311-5bc3-467e-900b-41d9fc1503a7",
                    "creator_name": "Juan Pérez",
                    "signers": [
                        {
                            "user_id": "ea77e311-5bc3-467e-900b-41d9fc1503a7",
                            "user_name": "Marta López",
                            "email": "marta.lopez@municipio.cl",
                            "signing_order": 1,
                            "is_numerator": True,
                            "profile_picture_url": "https://s.gravatar.com/avatar/abc123.jpg"
                        }
                    ],
                    "rejection_info": None,
                    "created_at": "2025-10-07T10:00:00.000Z",
                    "updated_at": "2025-10-07T15:30:45.000Z"
                }
            ]
        }
    )