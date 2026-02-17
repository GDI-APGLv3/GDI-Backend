"""
Modelos relacionados con la creación de documentos.
"""

from pydantic import BaseModel, Field, ConfigDict, field_validator
from datetime import datetime
from typing import Optional, List, Dict


class NoteRecipientsInput(BaseModel):
    """
    Destinatarios para documentos tipo NOTA.

    Los recipients se definen SOLO al crear el documento (POST /documents),
    no se pueden modificar despues en el endpoint de guardado (PATCH /documents/{id}/save).

    - **to**: Destinatarios principales (obligatorio al menos uno)
    - **cc**: Destinatarios en copia (opcional)
    - **bcc**: Destinatarios en copia oculta (opcional, solo visible para sender)
    """
    to: List[str] = Field(
        default_factory=list,
        description="UUIDs de sectores destinatarios principales (TO). Obligatorio al menos uno."
    )
    cc: List[str] = Field(
        default_factory=list,
        description="UUIDs de sectores en copia (CC). Opcional."
    )
    bcc: List[str] = Field(
        default_factory=list,
        description="UUIDs de sectores en copia oculta (BCC). Solo visible para el sender. Opcional."
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "to": ["550e8400-e29b-41d4-a716-446655440001"],
                "cc": ["550e8400-e29b-41d4-a716-446655440002"],
                "bcc": []
            },
            "description": "Recipients se definen al CREAR el documento, no al guardarlo."
        }
    )


class CreateDocumentRequest(BaseModel):
    """
    Modelo para solicitud de creacion de documento.

    ## Flujo de Documentos
    1. POST /documents (este request) - Crear documento
    2. PATCH /documents/{id}/save - Guardar contenido y firmantes
    3. POST /documents/{id}/start-signing-process - Enviar a firma
    4. POST /documents/{id}/super-sign - Firmar

    ## Para NOTAS
    Los recipients (TO, CC, BCC) se definen **aqui al crear**, NO al guardar.
    """
    document_type_acronym: str = Field(
        ...,
        description="Acronimo del tipo de documento (IF, ME, OF, NOTA, etc.)",
        json_schema_extra={"examples": ["IF", "ME", "OF", "NOTA", "DI"]}
    )
    reference: str = Field(
        ...,
        min_length=1,
        max_length=250,
        description="Asunto o referencia del documento (maximo 250 caracteres)"
    )
    recipients: Optional[NoteRecipientsInput] = Field(
        None,
        description="Destinatarios (OBLIGATORIO para tipo NOTA, ignorado para otros tipos). Requiere al menos un destinatario TO."
    )
    # creator_id eliminado - se obtiene del usuario autenticado (current_user)

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "summary": "Documento comun (IF, ME, OF, etc.)",
                    "value": {
                        "document_type_acronym": "IF",
                        "reference": "Informe de situacion patrimonial"
                    }
                },
                {
                    "summary": "NOTA con destinatarios",
                    "value": {
                        "document_type_acronym": "NOTA",
                        "reference": "Convocatoria a reunion de coordinacion",
                        "recipients": {
                            "to": ["550e8400-e29b-41d4-a716-446655440001"],
                            "cc": ["550e8400-e29b-41d4-a716-446655440002"],
                            "bcc": []
                        }
                    }
                }
            ]
        }
    )

class CreateDocumentResponse(BaseModel):
    """Respuesta a la creación de un documento"""
    document_id: str = Field(..., description="UUID del documento creado")
    status: str = Field(..., description="Estado del documento creado (draft)")
    message: str = Field(..., description="Mensaje descriptivo del resultado de la operación")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "document_id": "550e8400-e29b-41d4-a716-446655440000",
                "status": "draft",
                "message": "Documento creado exitosamente"
            }
        }
    )