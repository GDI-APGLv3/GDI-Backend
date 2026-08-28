
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Dict, Any


class DeleteDocumentResponse(BaseModel):
    success: bool = Field(..., description="Indica si la eliminación fue procesada exitosamente")
    message: str = Field(..., description="Mensaje descriptivo del resultado")
    document_id: str = Field(..., description="UUID del documento eliminado")
    deleted_by: str = Field(..., description="UUID del usuario que eliminó")
    deleted_by_name: str = Field(..., description="Nombre completo del usuario que eliminó")
    deleted_at: str = Field(..., description="Timestamp de la eliminación en formato ISO")
    previous_status: str = Field(..., description="Estado del documento antes de eliminarlo")
    unlinked_cases: int = Field(default=0, description="Cantidad de expedientes de los que se desvinculó")
    pdf_cleanup: Optional[Dict[str, Any]] = Field(default=None, description="Información sobre limpieza del PDF en R2")

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "success": True,
                    "message": "Documento eliminado exitosamente",
                    "document_id": "550e8400-e29b-41d4-a716-446655440000",
                    "deleted_by": "ea77e311-5bc3-467e-900b-41d9fc1503a7",
                    "deleted_by_name": "Ana García",
                    "deleted_at": "2025-10-01T14:30:45.123Z",
                    "previous_status": "draft",
                    "unlinked_cases": 1,
                    "pdf_cleanup": {
                        "attempted": True,
                        "success": True,
                        "error": None,
                        "note": "PDF cleanup is best-effort (soft-fail)"
                    }
                }
            ]
        }
    )
