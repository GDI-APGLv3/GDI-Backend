"""
Modelos relacionados con el rechazo de documentos.
"""

from pydantic import BaseModel, Field, ConfigDict
from typing import Dict, Any

class RejectDocumentRequest(BaseModel):
    """Datos necesarios para rechazar un documento"""
    reason: str = Field(..., min_length=5, max_length=500, description="Motivo del rechazo (mínimo 5 caracteres)")
    
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "reason": "El contenido del documento presenta errores en las fechas y montos especificados. Necesita corrección antes de proceder con la firma."
                },
                {
                    "reason": "Falta información crucial sobre el presupuesto asignado. Requiere completar los datos financieros para su aprobación."
                }
            ]
        }
    )

class RejectDocumentResponse(BaseModel):
    """Respuesta al endpoint de rechazo de documento"""
    success: bool = Field(..., description="Indica si el rechazo fue procesado exitosamente")
    message: str = Field(..., description="Mensaje descriptivo del resultado")
    document_id: str = Field(..., description="UUID del documento rechazado")
    rejected_by: str = Field(..., description="UUID del usuario que rechazó")
    rejected_by_name: str = Field(..., description="Nombre completo del usuario que rechazó")
    rejection_id: str = Field(..., description="UUID del registro de rechazo")
    rejected_at: str = Field(..., description="Timestamp del rechazo en formato ISO")
    reason: str = Field(..., description="Motivo del rechazo registrado")
    new_status: str = Field(..., description="Nuevo estado del documento")
    display_status: str = Field(..., description="Nuevo display status para frontend")
    reset_info: Dict[str, Any] = Field(..., description="Información sobre el reseteo del estado del documento")
    
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "success": True,
                    "message": "Documento rechazado correctamente. El documento ha sido devuelto al estado de edición.",
                    "document_id": "550e8400-e29b-41d4-a716-446655440000",
                    "rejected_by": "ea77e311-5bc3-467e-900b-41d9fc1503a7",
                    "rejected_by_name": "Ana García",
                    "rejection_id": "123e4567-e89b-12d3-a456-426614174000",
                    "rejected_at": "2025-10-01T14:30:45.123Z",
                    "reason": "El contenido del documento presenta errores en las fechas y montos especificados.",
                    "new_status": "rejected",
                    "display_status": "En edición",
                    "reset_info": {
                        "sent_to_sign_at_cleared": True,
                        "sent_by_cleared": True,
                        "document_generate_id_cleared": True,
                        "all_signers_reset_to_pending": True,
                        "signers_preserved": 3,
                        "audit_data_saved": True,
                        "description": "Documento devuelto al estado inicial de edición, conservando lista de firmantes"
                    }
                }
            ]
        }
    )