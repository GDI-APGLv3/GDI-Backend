"""
Modelos de respuesta para el modulo de MEMOS.
Adaptados de NOTAS con cambios: sector -> user_id, sender -> user info.
"""

from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Any
from datetime import datetime


class PaginationInfo(BaseModel):
    """Informacion de paginacion"""
    page: int = Field(..., description="Pagina actual (1-indexed)")
    page_size: int = Field(..., description="Elementos por pagina")
    total: int = Field(..., description="Total de elementos")
    total_pages: int = Field(..., description="Total de paginas")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "page": 1,
                "page_size": 20,
                "total": 45,
                "total_pages": 3
            }
        }
    )


class SenderInfo(BaseModel):
    """Informacion del usuario emisor"""
    user_id: str = Field(..., description="UUID del usuario emisor")
    full_name: str = Field(..., description="Nombre completo del emisor")
    sector_acronym: str = Field("", description="Acronimo del sector del emisor (snapshot)")


class ReadStatus(BaseModel):
    """Estado de lectura de un memo"""
    opened: bool = Field(..., description="Si fue abierto por el usuario")
    opened_at: Optional[str] = Field(None, description="Fecha de apertura (ISO format)")


class RecipientSummary(BaseModel):
    """Resumen de un recipient para listas"""
    user_id: str = Field(..., description="UUID del usuario")
    type: str = Field(..., description="Tipo: TO, CC, BCC")
    full_name: str = Field(..., description="Nombre completo del usuario")
    sector_acronym: str = Field("", description="Acronimo del sector")


class MemoSummary(BaseModel):
    """Resumen de memo para listas (sent/received)"""
    document_id: str = Field(..., description="UUID del documento")
    official_number: str = Field(..., description="Numero oficial del memo")
    reference: str = Field(..., description="Asunto del memo")
    signed_at: Optional[str] = Field(None, description="Fecha de firma (ISO format)")
    ai_summary: Optional[str] = Field(None, description="Resumen generado por IA")
    document_type: str = Field(..., description="Tipo de documento (MEMO)")


class MemoSentSummary(MemoSummary):
    """Resumen de memo enviado (incluye recipients y openings)"""
    recipients: List[RecipientSummary] = Field(
        default_factory=list,
        description="Lista de destinatarios"
    )
    openings_count: int = Field(..., description="Cantidad de aperturas registradas")


class MemoReceivedSummary(MemoSummary):
    """Resumen de memo recibido (incluye sender y read status)"""
    recipient_type: str = Field(..., description="Tipo de recipient (TO, CC)")
    sender: SenderInfo = Field(..., description="Informacion del usuario emisor")
    read_status: ReadStatus = Field(..., description="Estado de lectura")


class MemoSentListResponse(BaseModel):
    """Respuesta para lista de memos enviados"""
    memos: List[MemoSentSummary] = Field(..., description="Lista de memos enviados")
    pagination: PaginationInfo = Field(..., description="Informacion de paginacion")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "memos": [
                    {
                        "document_id": "uuid-1",
                        "official_number": "MEMO-2026-000001-MUNI",
                        "reference": "Solicitud de informacion",
                        "signed_at": "2026-02-03T10:00:00",
                        "ai_summary": "Memo solicitando informacion sobre...",
                        "document_type": "MEMO",
                        "recipients": [
                            {"user_id": "uuid-2", "type": "TO", "full_name": "Maria Garcia", "sector_acronym": "MESA"}
                        ],
                        "openings_count": 1
                    }
                ],
                "pagination": {"page": 1, "page_size": 20, "total": 1, "total_pages": 1}
            }
        }
    )


class MemoReceivedListResponse(BaseModel):
    """Respuesta para lista de memos recibidos"""
    memos: List[MemoReceivedSummary] = Field(..., description="Lista de memos recibidos")
    pagination: PaginationInfo = Field(..., description="Informacion de paginacion")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "memos": [
                    {
                        "document_id": "uuid-1",
                        "official_number": "MEMO-2026-000001-MUNI",
                        "reference": "Solicitud de informacion",
                        "signed_at": "2026-02-03T10:00:00",
                        "ai_summary": "Memo solicitando informacion sobre...",
                        "document_type": "MEMO",
                        "recipient_type": "TO",
                        "sender": {
                            "user_id": "uuid-3",
                            "full_name": "Juan Perez",
                            "sector_acronym": "PRIV"
                        },
                        "read_status": {"opened": True, "opened_at": "2026-02-03T11:00:00"}
                    }
                ],
                "pagination": {"page": 1, "page_size": 20, "total": 1, "total_pages": 1}
            }
        }
    )


# ============================================================================
# ARCHIVADO DE MEMOS
# ============================================================================


class ArchiveMemoRequest(BaseModel):
    """Request para archivar/desarchivar un memo"""
    archived: bool = Field(..., description="True para archivar, False para desarchivar")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "archived": True
            }
        }
    )


class ArchiveMemoResponse(BaseModel):
    """Respuesta al archivar/desarchivar un memo"""
    document_id: str = Field(..., description="UUID del documento")
    user_id: str = Field(..., description="UUID del usuario")
    is_archived: bool = Field(..., description="Nuevo estado de archivado")
    archived_at: Optional[str] = Field(None, description="Fecha de archivado (ISO format)")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "document_id": "uuid-doc",
                "user_id": "uuid-user",
                "is_archived": True,
                "archived_at": "2026-02-05T10:30:00"
            }
        }
    )


class MemoArchivedSummary(MemoSummary):
    """Resumen de memo archivado (incluye sender, read status y archive info)"""
    recipient_type: str = Field(..., description="Tipo de recipient (TO, CC)")
    sender: SenderInfo = Field(..., description="Informacion del usuario emisor")
    read_status: ReadStatus = Field(..., description="Estado de lectura")
    is_archived: bool = Field(True, description="Si esta archivado (siempre True en esta lista)")
    archived_at: Optional[str] = Field(None, description="Fecha de archivado")


class MemoArchivedListResponse(BaseModel):
    """Respuesta para lista de memos archivados"""
    memos: List[MemoArchivedSummary] = Field(..., description="Lista de memos archivados")
    pagination: PaginationInfo = Field(..., description="Informacion de paginacion")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "memos": [
                    {
                        "document_id": "uuid-1",
                        "official_number": "MEMO-2026-000001-MUNI",
                        "reference": "Solicitud de informacion",
                        "signed_at": "2026-02-03T10:00:00",
                        "ai_summary": "Memo solicitando informacion sobre...",
                        "document_type": "MEMO",
                        "recipient_type": "TO",
                        "sender": {
                            "user_id": "uuid-3",
                            "full_name": "Juan Perez",
                            "sector_acronym": "PRIV"
                        },
                        "read_status": {"opened": True, "opened_at": "2026-02-03T11:00:00"},
                        "is_archived": True,
                        "archived_at": "2026-02-04T15:00:00"
                    }
                ],
                "pagination": {"page": 1, "page_size": 20, "total": 1, "total_pages": 1}
            }
        }
    )


# ============================================================================
# CONTADOR NO LEIDOS
# ============================================================================


class MemoUnreadCountResponse(BaseModel):
    """Respuesta para contador de memos no leidos"""
    unread_count: int = Field(..., description="Cantidad de memos no leidos")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "unread_count": 3
            }
        }
    )


# ============================================================================
# DETALLE DE MEMO
# ============================================================================


class DocumentTypeInfo(BaseModel):
    """Informacion del tipo de documento"""
    name: str = Field(..., description="Nombre completo")
    acronym: str = Field(..., description="Acronimo")


class MyAccessInfo(BaseModel):
    """Informacion de acceso del usuario actual"""
    is_sender: bool = Field(..., description="Si es el emisor")
    recipient_type: Optional[str] = Field(None, description="Tipo de recipient si aplica")
    user_id: Optional[str] = Field(None, description="UUID del usuario")
    first_open: bool = Field(..., description="Si es la primera apertura")
    opened_at: Optional[str] = Field(None, description="Fecha de apertura")
    is_archived: bool = Field(False, description="Si el memo esta archivado para este usuario")
    archived_at: Optional[str] = Field(None, description="Fecha de archivado")


class OpeningInfo(BaseModel):
    """Informacion de una apertura de memo"""
    user_id: str = Field(..., description="UUID del usuario")
    full_name: str = Field(..., description="Nombre del usuario")
    sector_acronym: str = Field("", description="Acronimo del sector")
    recipient_type: str = Field(..., description="Tipo de recipient (TO, CC, BCC)")
    opened_at: Optional[str] = Field(None, description="Fecha de apertura")


class RecipientDetailInfo(BaseModel):
    """Informacion detallada de recipient"""
    user_id: str = Field(..., description="UUID del usuario")
    full_name: str = Field(..., description="Nombre completo del usuario")
    sector_acronym: str = Field("", description="Acronimo del sector")


class RecipientsDetail(BaseModel):
    """Recipients con detalle completo"""
    to: List[RecipientDetailInfo] = Field(default_factory=list)
    cc: List[RecipientDetailInfo] = Field(default_factory=list)
    bcc: Optional[List[RecipientDetailInfo]] = Field(None, description="Solo visible para sender")
    is_sender: bool = Field(..., description="Si el usuario actual es el emisor")
    my_recipient_type: Optional[str] = Field(None)


class ProposedCaseInfo(BaseModel):
    """Informacion de un expediente propuesto para vincular"""
    case_id: str = Field(..., description="UUID del expediente")
    case_number: str = Field(..., description="Numero del expediente")
    reference: Optional[str] = Field(None, description="Asunto del expediente")
    proposing_date: Optional[str] = Field(None, description="Fecha de propuesta")


class MemoDetail(BaseModel):
    """Detalle completo de un memo"""
    document_id: str = Field(..., description="UUID del documento")
    official_number: str = Field(..., description="Numero oficial")
    reference: str = Field(..., description="Asunto")
    content: Any = Field(..., description="Contenido HTML del documento")
    signed_at: Optional[str] = Field(None, description="Fecha de firma")
    ai_summary: Optional[str] = Field(None, description="Resumen IA")
    signers: Any = Field(None, description="Informacion de firmantes")
    document_type: DocumentTypeInfo = Field(..., description="Tipo de documento")
    department_name: str = Field(..., description="Nombre del departamento emisor")
    recipients: RecipientsDetail = Field(..., description="Destinatarios")
    my_access: MyAccessInfo = Field(..., description="Informacion de acceso del usuario")
    openings: Optional[List[OpeningInfo]] = Field(
        None,
        description="Aperturas registradas (solo visible para sender)"
    )
    proposed_cases: Optional[List[ProposedCaseInfo]] = Field(
        None,
        description="Expedientes propuestos para vincular"
    )


class MemoDetailResponse(MemoDetail):
    """Respuesta para detalle de memo"""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "document_id": "uuid-1",
                "official_number": "MEMO-2026-000001-MUNI",
                "reference": "Solicitud de informacion",
                "content": {"html": "<p>Contenido...</p>"},
                "signed_at": "2026-02-03T10:00:00",
                "ai_summary": "Este memo solicita informacion sobre...",
                "signers": [{"user_id": "uuid", "full_name": "Juan Perez"}],
                "document_type": {"name": "Memo", "acronym": "MEMO"},
                "department_name": "Intendencia",
                "recipients": {
                    "to": [{"user_id": "uuid-2", "full_name": "Maria Garcia", "sector_acronym": "MESA"}],
                    "cc": [],
                    "bcc": None,
                    "is_sender": False,
                    "my_recipient_type": "TO"
                },
                "my_access": {
                    "is_sender": False,
                    "recipient_type": "TO",
                    "user_id": "uuid-2",
                    "first_open": True,
                    "opened_at": "2026-02-03T11:00:00",
                    "is_archived": False,
                    "archived_at": None
                },
                "openings": None
            }
        }
    )
