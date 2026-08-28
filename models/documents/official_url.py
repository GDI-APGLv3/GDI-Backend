
from pydantic import BaseModel, Field
from typing import Optional


class OfficialDocumentUrlData(BaseModel):
    pdf_url: str = Field(..., description="URL temporal de descarga del PDF (presigned R2, expira segun CF_R2_SIGN_EXPIRATION)")
    document_id: str = Field(..., description="UUID del documento")
    official_number: str = Field(..., description="Número oficial del documento")
    expires_in: Optional[str] = Field("10 minutos", description="Tiempo de expiración de la URL (derivado de CF_R2_SIGN_EXPIRATION, default 600s)")


class OfficialDocumentUrlResponse(BaseModel):
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
                    "expires_in": "10 minutos"
                },
                "message": "URL de descarga obtenida exitosamente"
            }
        }
