
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List


class NoteRecipientsInput(BaseModel):
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