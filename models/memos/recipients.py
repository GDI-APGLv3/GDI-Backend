"""
Modelos relacionados con recipients de MEMOS.
A diferencia de NOTAS (sector-based), MEMOS usa user_id y nombre de usuario.
"""

from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional


class MemoRecipientInfo(BaseModel):
    """Informacion de un recipient de MEMO"""
    user_id: str = Field(..., description="UUID del usuario destinatario")
    name: str = Field(..., description="Nombre completo del usuario")
    sector_acronym: str = Field("", description="Acronimo del sector del usuario (snapshot)")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_id": "550e8400-e29b-41d4-a716-446655440001",
                "name": "Maria Garcia",
                "sector_acronym": "MESA"
            }
        }
    )


class MemoVisibleRecipientsResponse(BaseModel):
    """Recipients visibles segun permisos del usuario"""
    to: List[MemoRecipientInfo] = Field(default_factory=list, description="Destinatarios principales")
    cc: List[MemoRecipientInfo] = Field(default_factory=list, description="Destinatarios en copia")
    bcc: Optional[List[MemoRecipientInfo]] = Field(
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
                    {"user_id": "uuid-1", "name": "Maria Garcia", "sector_acronym": "MESA"}
                ],
                "cc": [
                    {"user_id": "uuid-2", "name": "Juan Lopez", "sector_acronym": "RRHH"}
                ],
                "bcc": [],
                "is_sender": True,
                "my_recipient_type": None
            }
        }
    )
