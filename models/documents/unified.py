from typing import Union, Literal
from pydantic import BaseModel, Field
from datetime import datetime
from .editing import DocumentDetailResponse
from .signing import DocumentSignatureDetailsResponse


class UnifiedDocumentDetailsResponse(BaseModel):

    state_category: Literal["editing", "signing"] = Field(
        ...,
        description="Categoría del estado del documento que indica el tipo de respuesta"
    )

    document_id: str = Field(
        ...,
        description="UUID del documento"
    )

    status: str = Field(
        ...,
        description="Estado actual del documento (draft, rejected, sent_to_sign, signed)"
    )

    details: Union[DocumentDetailResponse, DocumentSignatureDetailsResponse] = Field(
        ...,
        description="Detalles del documento. La estructura depende de state_category"
    )

    timestamp: datetime = Field(
        default_factory=datetime.now,
        description="Timestamp de cuando se generó esta respuesta"
    )

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "state_category": "editing",
                    "document_id": "550e8400-e29b-41d4-a716-446655440000",
                    "status": "draft",
                    "timestamp": "2025-11-01T10:30:45.123456",
                    "details": {
                        "document_id": "550e8400-e29b-41d4-a716-446655440000",
                        "reference": "Informe de actividades",
                        "content": "<p>Contenido HTML...</p>",
                        "status": "draft",
                        "document_type": {
                            "name": "Informe",
                            "acronym": "IF"
                        },
                        "signers": []
                    }
                },
                {
                    "state_category": "signing",
                    "document_id": "660e8400-e29b-41d4-a716-446655440111",
                    "status": "sent_to_sign",
                    "timestamp": "2025-11-01T10:30:45.123456",
                    "details": {
                        "document": {
                            "document_id": "660e8400-e29b-41d4-a716-446655440111",
                            "reference": "Memorándum urgente",
                            "status": "sent_to_sign",
                            "pdf_url": "https://..."
                        },
                        "signature_progress": {
                            "completed": 2,
                            "total": 3
                        },
                        "can_sign": True
                    }
                }
            ]
        }
