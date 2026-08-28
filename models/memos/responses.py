
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Any


class PaginationInfo(BaseModel):
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
    user_id: str = Field(..., description="UUID del usuario emisor")
    full_name: str = Field(..., description="Nombre completo del emisor")
    sector_acronym: str = Field("", description="Acronimo del sector del emisor (snapshot)")


class ReadStatus(BaseModel):
    opened: bool = Field(..., description="Si fue abierto por el usuario")
    opened_at: Optional[str] = Field(None, description="Fecha de apertura (ISO format)")


class RecipientSummary(BaseModel):
    user_id: str = Field(..., description="UUID del usuario")
    type: str = Field(..., description="Tipo: TO, CC, BCC")
    full_name: str = Field(..., description="Nombre completo del usuario")
    sector_acronym: str = Field("", description="Acronimo del sector")


class MemoSummary(BaseModel):
    document_id: str = Field(..., description="UUID del documento")
    official_number: str = Field(..., description="Numero oficial del memo")
    reference: str = Field(..., description="Asunto del memo")
    signed_at: Optional[str] = Field(None, description="Fecha de firma (ISO format)")
    ai_summary: Optional[str] = Field(None, description="Resumen generado por IA")
    document_type: str = Field(..., description="Tipo de documento (MEMO)")


class MemoSentSummary(MemoSummary):
    recipients: List[RecipientSummary] = Field(
        default_factory=list,
        description="Lista de destinatarios"
    )
    openings_count: int = Field(..., description="Cantidad de aperturas registradas")


class MemoReceivedSummary(MemoSummary):
    recipient_type: str = Field(..., description="Tipo de recipient (TO, CC)")
    sender: SenderInfo = Field(..., description="Informacion del usuario emisor")
    read_status: ReadStatus = Field(..., description="Estado de lectura")


class MemoSentListResponse(BaseModel):
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


class ArchiveMemoRequest(BaseModel):
    archived: bool = Field(..., description="True para archivar, False para desarchivar")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "archived": True
            }
        }
    )


class ArchiveMemoResponse(BaseModel):
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
    recipient_type: str = Field(..., description="Tipo de recipient (TO, CC)")
    sender: SenderInfo = Field(..., description="Informacion del usuario emisor")
    read_status: ReadStatus = Field(..., description="Estado de lectura")
    is_archived: bool = Field(True, description="Si esta archivado (siempre True en esta lista)")
    archived_at: Optional[str] = Field(None, description="Fecha de archivado")


class MemoArchivedListResponse(BaseModel):
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


class MemoUnreadCountResponse(BaseModel):
    unread_count: int = Field(..., description="Cantidad de memos no leidos")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "unread_count": 3
            }
        }
    )


class DocumentTypeInfo(BaseModel):
    name: str = Field(..., description="Nombre completo")
    acronym: str = Field(..., description="Acronimo")


class MyAccessInfo(BaseModel):
    is_sender: bool = Field(..., description="Si es el emisor")
    recipient_type: Optional[str] = Field(None, description="Tipo de recipient si aplica")
    user_id: Optional[str] = Field(None, description="UUID del usuario")
    first_open: bool = Field(..., description="Si es la primera apertura")
    opened_at: Optional[str] = Field(None, description="Fecha de apertura")
    is_archived: bool = Field(False, description="Si el memo esta archivado para este usuario")
    archived_at: Optional[str] = Field(None, description="Fecha de archivado")


class OpeningInfo(BaseModel):
    user_id: str = Field(..., description="UUID del usuario")
    full_name: str = Field(..., description="Nombre del usuario")
    sector_id: Optional[str] = Field(None, description="UUID del sector")
    sector_acronym: str = Field("", description="Acronimo del sector")
    sector_color: Optional[str] = Field(None, description="Color primario del sector (hex)")
    profile_picture_url: Optional[str] = Field(None, description="URL de foto de perfil del usuario")
    seal_name: Optional[str] = Field(None, description="Sello/cargo del usuario que abrió")
    recipient_type: str = Field(..., description="Tipo de recipient (TO, CC, BCC)")
    opened_at: Optional[str] = Field(None, description="Fecha de apertura")


class RecipientDetailInfo(BaseModel):
    user_id: str = Field(..., description="UUID del usuario")
    full_name: str = Field(..., description="Nombre completo del usuario")
    sector_acronym: str = Field("", description="Acronimo del sector")


class RecipientsDetail(BaseModel):
    to: List[RecipientDetailInfo] = Field(default_factory=list)
    cc: List[RecipientDetailInfo] = Field(default_factory=list)
    bcc: Optional[List[RecipientDetailInfo]] = Field(None, description="Solo visible para sender")
    is_sender: bool = Field(..., description="Si el usuario actual es el emisor")
    my_recipient_type: Optional[str] = Field(None)


class ProposedCaseInfo(BaseModel):
    case_id: str = Field(..., description="UUID del expediente")
    case_number: str = Field(..., description="Numero del expediente")
    reference: Optional[str] = Field(None, description="Asunto del expediente")
    proposing_date: Optional[str] = Field(None, description="Fecha de propuesta")
    is_reserved: bool = Field(False, description="GDI-069: expediente de tipo reservado (reference enmascarada)")


class MemoDetail(BaseModel):
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
