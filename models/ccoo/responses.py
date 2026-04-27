"""
Modelos de respuesta para el modulo de CCOO (Comunicaciones Oficiales).
Formato unificado que combina Notas y Memos.
"""

from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional


class CcooSender(BaseModel):
    """Informacion del emisor (sector para NOTA, usuario para MEMO)"""
    label: str = Field(..., description="Nombre principal del emisor (acronym sector o full_name usuario)")
    detail: str = Field(..., description="Detalle adicional (department name o sector acronym)")
    type: str = Field(..., description="Tipo de emisor: 'sector' (NOTA) o 'user' (MEMO)")


class CcooReadStatus(BaseModel):
    """Estado de lectura de una comunicacion"""
    opened: bool = Field(..., description="Si fue abierta/leida")
    opened_at: Optional[str] = Field(None, description="Fecha de apertura (ISO format)")


class CcooPagination(BaseModel):
    """Informacion de paginacion"""
    page: int = Field(..., description="Pagina actual (1-indexed)")
    page_size: int = Field(..., description="Elementos por pagina")
    total: int = Field(..., description="Total de elementos")
    total_pages: int = Field(..., description="Total de paginas")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "page": 1,
                "page_size": 10,
                "total": 25,
                "total_pages": 3
            }
        }
    )


class CcooReceivedItem(BaseModel):
    """Item de CCOO recibida (bandeja de entrada unificada)"""
    document_id: str = Field(..., description="UUID del documento")
    official_number: str = Field(..., description="Numero oficial (ej: NOTA-2026-000001-MUNI)")
    reference: str = Field(..., description="Asunto de la comunicacion")
    signed_at: Optional[str] = Field(None, description="Fecha de firma (ISO format)")
    ai_summary: Optional[str] = Field(None, description="Resumen generado por IA")
    document_type: str = Field(..., description="Tipo de documento (NOTA, MEMO)")
    recipient_type: str = Field(..., description="Tipo de destinatario (TO, CC, BCC)")
    ccoo_type: str = Field(..., description="Tipo de CCOO: 'NOTA' o 'MEMO'")
    sender: CcooSender = Field(..., description="Informacion del emisor")
    read_status: CcooReadStatus = Field(..., description="Estado de lectura")


class CcooSentItem(BaseModel):
    """Item de CCOO enviada (bandeja de enviados unificada)"""
    document_id: str = Field(..., description="UUID del documento")
    official_number: str = Field(..., description="Numero oficial")
    reference: str = Field(..., description="Asunto de la comunicacion")
    signed_at: Optional[str] = Field(None, description="Fecha de firma (ISO format)")
    ai_summary: Optional[str] = Field(None, description="Resumen generado por IA")
    document_type: str = Field(..., description="Tipo de documento (NOTA, MEMO)")
    ccoo_type: str = Field(..., description="Tipo de CCOO: 'NOTA' o 'MEMO'")
    recipients_label: str = Field(..., description="Primer destinatario (acronym sector o full_name)")
    recipients_count: int = Field(..., description="Total de destinatarios")
    openings_count: int = Field(..., description="Cantidad de aperturas registradas")


class CcooArchivedItem(CcooReceivedItem):
    """Item de CCOO archivada (incluye fecha de archivado)"""
    archived_at: Optional[str] = Field(None, description="Fecha de archivado (ISO format)")


class CcooReceivedListResponse(BaseModel):
    """Respuesta para lista de CCOO recibidas"""
    items: List[CcooReceivedItem] = Field(..., description="Lista de comunicaciones recibidas")
    pagination: CcooPagination = Field(..., description="Informacion de paginacion")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "items": [
                    {
                        "document_id": "uuid-1",
                        "official_number": "NOTA-2026-000001-MUNI",
                        "reference": "Solicitud de informacion",
                        "signed_at": "2026-02-03T10:00:00",
                        "ai_summary": "Nota solicitando informacion sobre...",
                        "document_type": "NOTA",
                        "recipient_type": "TO",
                        "ccoo_type": "NOTA",
                        "sender": {"label": "PRIV", "detail": "Intendencia", "type": "sector"},
                        "read_status": {"opened": True, "opened_at": "2026-02-03T11:00:00"}
                    },
                    {
                        "document_id": "uuid-2",
                        "official_number": "MEMO-2026-000001-MUNI",
                        "reference": "Aviso importante",
                        "signed_at": "2026-02-03T09:00:00",
                        "ai_summary": "Memo informando sobre...",
                        "document_type": "MEMO",
                        "recipient_type": "TO",
                        "ccoo_type": "MEMO",
                        "sender": {"label": "Juan Perez", "detail": "PRIV", "type": "user"},
                        "read_status": {"opened": False, "opened_at": None}
                    }
                ],
                "pagination": {"page": 1, "page_size": 10, "total": 25, "total_pages": 3}
            }
        }
    )


class CcooSentListResponse(BaseModel):
    """Respuesta para lista de CCOO enviadas"""
    items: List[CcooSentItem] = Field(..., description="Lista de comunicaciones enviadas")
    pagination: CcooPagination = Field(..., description="Informacion de paginacion")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "items": [
                    {
                        "document_id": "uuid-1",
                        "official_number": "NOTA-2026-000001-MUNI",
                        "reference": "Solicitud de informacion",
                        "signed_at": "2026-02-03T10:00:00",
                        "ai_summary": "Nota solicitando...",
                        "document_type": "NOTA",
                        "ccoo_type": "NOTA",
                        "recipients_label": "MESA",
                        "recipients_count": 3,
                        "openings_count": 1
                    }
                ],
                "pagination": {"page": 1, "page_size": 10, "total": 5, "total_pages": 1}
            }
        }
    )


class CcooArchivedListResponse(BaseModel):
    """Respuesta para lista de CCOO archivadas"""
    items: List[CcooArchivedItem] = Field(..., description="Lista de comunicaciones archivadas")
    pagination: CcooPagination = Field(..., description="Informacion de paginacion")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "items": [
                    {
                        "document_id": "uuid-1",
                        "official_number": "NOTA-2026-000001-MUNI",
                        "reference": "Solicitud de informacion",
                        "signed_at": "2026-02-03T10:00:00",
                        "ai_summary": "Nota solicitando...",
                        "document_type": "NOTA",
                        "recipient_type": "TO",
                        "ccoo_type": "NOTA",
                        "sender": {"label": "PRIV", "detail": "Intendencia", "type": "sector"},
                        "read_status": {"opened": True, "opened_at": "2026-02-03T11:00:00"},
                        "archived_at": "2026-02-04T15:00:00"
                    }
                ],
                "pagination": {"page": 1, "page_size": 10, "total": 2, "total_pages": 1}
            }
        }
    )
