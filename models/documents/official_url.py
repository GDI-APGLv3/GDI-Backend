"""
Modelos Pydantic para obtención de URLs de documentos oficiales.
Define los esquemas de response para URLs temporales de Cloudflare R2.
"""

from pydantic import BaseModel, Field
from typing import Optional


class OfficialDocumentUrlData(BaseModel):
    """Datos de la URL del documento oficial."""
    pdf_url: str = Field(..., description="URL temporal de descarga del PDF (válida 15 minutos)")
    document_id: str = Field(..., description="UUID del documento")
    official_number: str = Field(..., description="Número oficial del documento")
    expires_in: Optional[str] = Field("15 minutes", description="Tiempo de expiración de la URL")


class OfficialDocumentUrlResponse(BaseModel):
    """Response para solicitud de URL de documento oficial."""
    success: bool = Field(True, description="Indica si la operación fue exitosa")
    data: OfficialDocumentUrlData = Field(..., description="Datos de la URL del documento")
    message: str = Field(..., description="Mensaje descriptivo de la operación")
    
    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "data": {
                    "pdf_url": "https://r2.cloudflarestorage.com/bucket/document.pdf?signature=...",
                    "document_id": "123e4567-e89b-12d3-a456-426614174000",
                    "official_number": "GDI-2024-000123",
                    "expires_in": "15 minutes"
                },
                "message": "URL de descarga obtenida exitosamente"
            }
        }
