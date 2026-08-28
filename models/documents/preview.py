
from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional

class DocumentPreviewResponse(BaseModel):
    success: bool = Field(..., description="Indica si la previsualización se generó exitosamente")
    message: str = Field(..., description="Mensaje descriptivo")
    document_id: str = Field(..., description="UUID del documento")
    preview_url: Optional[str] = Field(None, description="URL del PDF de previsualización generado")
    document_data: Dict[str, Any] = Field(..., description="Datos del documento para UI")
    pdf_generation: Dict[str, Any] = Field(..., description="Información de la generación del PDF")

class DocumentPreviewInfo(BaseModel):
    document_id: str = Field(..., description="UUID del documento")
    reference: str = Field(..., description="Referencia del documento")
    document_type: Dict[str, str] = Field(..., description="Información del tipo de documento")
    display_status: str = Field(..., description="Estado visible para el usuario")
    content: str = Field(..., description="Contenido HTML extraído de forma híbrida")
    creator: Optional[Dict[str, Any]] = Field(None, description="Datos completos del creador del documento")
    signers: List[Dict[str, Any]] = Field(..., description="Lista de firmantes con datos reales de la BD")
    document_generate_id: Optional[str] = Field(None, description="UUID del PDF generado si existe")

class PreviewInfoResponse(BaseModel):
    success: bool = Field(True, description="Indica si la operación fue exitosa")
    message: str = Field(..., description="Mensaje descriptivo")
    document_id: str = Field(..., description="UUID del documento")
    document_data: Dict[str, Any] = Field(..., description="Datos completos del documento para preview")
    
    class Config:
        json_schema_extra = {
            "example": {
                "success": True,
                "message": "Información del documento obtenida exitosamente",
                "document_id": "123e4567-e89b-12d3-a456-426614174000",
                "document_data": {
                    "document_id": "123e4567-e89b-12d3-a456-426614174000",
                    "display_status": "En edición",
                    "document_type": {
                        "acronym": "ME",
                        "name": "Memorándum"
                    },
                    "signers": [],
                    "document_generate_id": None
                }
            }
        }