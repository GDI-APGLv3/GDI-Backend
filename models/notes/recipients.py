
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional


class RecipientInfo(BaseModel):
    sector_id: str = Field(..., description="UUID del sector destinatario")
    acronym: str = Field(..., description="Acrónimo del sector (ej: HAC, LEGAL)")
    department_name: str = Field(..., description="Nombre del departamento")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "sector_id": "550e8400-e29b-41d4-a716-446655440001",
                "acronym": "PRIV",
                "department_name": "Secretaría de Hacienda"
            }
        }
    )


class VisibleRecipientsResponse(BaseModel):
    to: List[RecipientInfo] = Field(default_factory=list, description="Destinatarios principales")
    cc: List[RecipientInfo] = Field(default_factory=list, description="Destinatarios en copia")
    bcc: Optional[List[RecipientInfo]] = Field(
        None,
        description="Destinatarios en copia oculta (solo visible para el sender)"
    )
    is_sender: bool = Field(..., description="True si el usuario solicitante es el emisor")
    my_recipient_type: Optional[str] = Field(
        None,
        description="Tipo de recipient del usuario (TO, CC, BCC) o None si es sender"
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "to": [
                    {"sector_id": "uuid-1", "acronym": "PRIV", "department_name": "Hacienda"}
                ],
                "cc": [
                    {"sector_id": "uuid-2", "acronym": "PRIV", "department_name": "Legal"}
                ],
                "bcc": [],
                "is_sender": True,
                "my_recipient_type": None
            }
        }
    )
