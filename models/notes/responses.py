"""
Modelos de respuesta para el módulo de NOTAS.
"""

from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Any
from datetime import datetime


class PaginationInfo(BaseModel):
    """Información de paginación"""
    page: int = Field(..., description="Página actual (1-indexed)")
    page_size: int = Field(..., description="Elementos por página")
    total: int = Field(..., description="Total de elementos")
    total_pages: int = Field(..., description="Total de páginas")

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
    """Información del sector emisor"""
    sector_id: str = Field(..., description="UUID del sector")
    acronym: str = Field(..., description="Acrónimo del sector")
    department_name: str = Field(..., description="Nombre del departamento")


class ReadStatus(BaseModel):
    """Estado de lectura de una nota"""
    opened: bool = Field(..., description="Si fue abierta por el sector")
    opened_at: Optional[str] = Field(None, description="Fecha de apertura (ISO format)")


class RecipientSummary(BaseModel):
    """Resumen de un recipient para listas"""
    sector_id: str = Field(..., description="UUID del sector")
    type: str = Field(..., description="Tipo: TO, CC, BCC")
    acronym: str = Field(..., description="Acrónimo del sector")
    department: str = Field(..., description="Nombre del departamento")


class NoteSummary(BaseModel):
    """Resumen de nota para listas (sent/received)"""
    document_id: str = Field(..., description="UUID del documento")
    official_number: str = Field(..., description="Número oficial de la nota")
    reference: str = Field(..., description="Asunto de la nota")
    signed_at: Optional[str] = Field(None, description="Fecha de firma (ISO format)")
    ai_summary: Optional[str] = Field(None, description="Resumen generado por IA")
    document_type: str = Field(..., description="Tipo de documento (NOTA)")


class NoteSentSummary(NoteSummary):
    """Resumen de nota enviada (incluye recipients y openings)"""
    recipients: List[RecipientSummary] = Field(
        default_factory=list,
        description="Lista de destinatarios"
    )
    openings_count: int = Field(..., description="Cantidad de aperturas registradas")


class NoteReceivedSummary(NoteSummary):
    """Resumen de nota recibida (incluye sender y read status)"""
    recipient_type: str = Field(..., description="Tipo de recipient (TO, CC)")
    sender: SenderInfo = Field(..., description="Información del sector emisor")
    read_status: ReadStatus = Field(..., description="Estado de lectura")


class NoteSentListResponse(BaseModel):
    """Respuesta para lista de notas enviadas"""
    notes: List[NoteSentSummary] = Field(..., description="Lista de notas enviadas")
    pagination: PaginationInfo = Field(..., description="Información de paginación")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "notes": [
                    {
                        "document_id": "uuid-1",
                        "official_number": "NOTA-2026-000001-MUNI",
                        "reference": "Solicitud de información",
                        "signed_at": "2026-02-03T10:00:00",
                        "ai_summary": "Nota solicitando información sobre...",
                        "document_type": "NOTA",
                        "recipients": [
                            {"sector_id": "uuid-2", "type": "TO", "acronym": "PRIV", "department": "Hacienda"}
                        ],
                        "openings_count": 1
                    }
                ],
                "pagination": {"page": 1, "page_size": 20, "total": 1, "total_pages": 1}
            }
        }
    )


class NoteReceivedListResponse(BaseModel):
    """Respuesta para lista de notas recibidas"""
    notes: List[NoteReceivedSummary] = Field(..., description="Lista de notas recibidas")
    pagination: PaginationInfo = Field(..., description="Información de paginación")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "notes": [
                    {
                        "document_id": "uuid-1",
                        "official_number": "NOTA-2026-000001-MUNI",
                        "reference": "Solicitud de información",
                        "signed_at": "2026-02-03T10:00:00",
                        "ai_summary": "Nota solicitando información sobre...",
                        "document_type": "NOTA",
                        "recipient_type": "TO",
                        "sender": {
                            "sector_id": "uuid-3",
                            "acronym": "PRIV",
                            "department_name": "Intendencia"
                        },
                        "read_status": {"opened": True, "opened_at": "2026-02-03T11:00:00"}
                    }
                ],
                "pagination": {"page": 1, "page_size": 20, "total": 1, "total_pages": 1}
            }
        }
    )


# ============================================================================
# ARCHIVADO DE NOTAS
# ============================================================================


class ArchiveNoteRequest(BaseModel):
    """Request para archivar/desarchivar una nota"""
    archived: bool = Field(..., description="True para archivar, False para desarchivar")
    sector_id: str = Field(..., description="UUID del sector desde el cual se archiva")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "archived": True,
                "sector_id": "uuid-sector"
            }
        }
    )


class ArchiveNoteResponse(BaseModel):
    """Respuesta al archivar/desarchivar una nota"""
    document_id: str = Field(..., description="UUID del documento")
    sector_id: str = Field(..., description="UUID del sector")
    is_archived: bool = Field(..., description="Nuevo estado de archivado")
    archived_at: Optional[str] = Field(None, description="Fecha de archivado (ISO format)")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "document_id": "uuid-doc",
                "sector_id": "uuid-sector",
                "is_archived": True,
                "archived_at": "2026-02-05T10:30:00"
            }
        }
    )


class NoteArchivedSummary(NoteSummary):
    """Resumen de nota archivada (incluye sender, read status y archive info)"""
    recipient_type: str = Field(..., description="Tipo de recipient (TO, CC)")
    sender: SenderInfo = Field(..., description="Información del sector emisor")
    read_status: ReadStatus = Field(..., description="Estado de lectura")
    is_archived: bool = Field(True, description="Si está archivada (siempre True en esta lista)")
    archived_at: Optional[str] = Field(None, description="Fecha de archivado")


class NoteArchivedListResponse(BaseModel):
    """Respuesta para lista de notas archivadas"""
    notes: List[NoteArchivedSummary] = Field(..., description="Lista de notas archivadas")
    pagination: PaginationInfo = Field(..., description="Información de paginación")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "notes": [
                    {
                        "document_id": "uuid-1",
                        "official_number": "NOTA-2026-000001-MUNI",
                        "reference": "Solicitud de información",
                        "signed_at": "2026-02-03T10:00:00",
                        "ai_summary": "Nota solicitando información sobre...",
                        "document_type": "NOTA",
                        "recipient_type": "TO",
                        "sender": {
                            "sector_id": "uuid-3",
                            "acronym": "PRIV",
                            "department_name": "Intendencia"
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


class DocumentTypeInfo(BaseModel):
    """Información del tipo de documento"""
    name: str = Field(..., description="Nombre completo")
    acronym: str = Field(..., description="Acrónimo")


class MyAccessInfo(BaseModel):
    """Información de acceso del usuario actual"""
    is_sender: bool = Field(..., description="Si es el emisor")
    recipient_type: Optional[str] = Field(None, description="Tipo de recipient si aplica")
    sector_id: Optional[str] = Field(None, description="UUID del sector con el que se accede a la nota")
    first_open: bool = Field(..., description="Si es la primera apertura")
    opened_at: Optional[str] = Field(None, description="Fecha de apertura")
    is_archived: bool = Field(False, description="Si la nota está archivada para este sector")
    archived_at: Optional[str] = Field(None, description="Fecha de archivado")


class OpeningInfo(BaseModel):
    """Información de una apertura de nota"""
    sector_id: str = Field(..., description="UUID del sector")
    sector_acronym: str = Field(..., description="Acrónimo del sector")
    user_id: str = Field(..., description="UUID del usuario")
    user_name: str = Field(..., description="Nombre del usuario")
    opened_at: Optional[str] = Field(None, description="Fecha de apertura")


class RecipientDetailInfo(BaseModel):
    """Información detallada de recipient"""
    sector_id: str = Field(..., description="UUID del sector")
    acronym: str = Field(..., description="Acrónimo del sector")
    department_name: str = Field(..., description="Nombre del departamento")
    department_acronym: str = Field("", description="Acrónimo del departamento")


class RecipientsDetail(BaseModel):
    """Recipients con detalle completo"""
    to: List[RecipientDetailInfo] = Field(default_factory=list)
    cc: List[RecipientDetailInfo] = Field(default_factory=list)
    bcc: Optional[List[RecipientDetailInfo]] = Field(None, description="Solo visible para sender")
    is_sender: bool = Field(..., description="Si el usuario actual es el emisor")
    my_recipient_type: Optional[str] = Field(None)


class ProposedCaseInfo(BaseModel):
    """Información de un expediente propuesto para vincular"""
    case_id: str = Field(..., description="UUID del expediente")
    case_number: str = Field(..., description="Número del expediente")
    reference: Optional[str] = Field(None, description="Asunto del expediente")
    proposing_date: Optional[str] = Field(None, description="Fecha de propuesta")


class NoteDetail(BaseModel):
    """Detalle completo de una nota"""
    document_id: str = Field(..., description="UUID del documento")
    official_number: str = Field(..., description="Número oficial")
    reference: str = Field(..., description="Asunto")
    content: Any = Field(..., description="Contenido HTML del documento")
    signed_at: Optional[str] = Field(None, description="Fecha de firma")
    ai_summary: Optional[str] = Field(None, description="Resumen IA")
    signers: Any = Field(None, description="Información de firmantes")
    document_type: DocumentTypeInfo = Field(..., description="Tipo de documento")
    department_name: str = Field(..., description="Nombre del departamento emisor")
    recipients: RecipientsDetail = Field(..., description="Destinatarios")
    my_access: MyAccessInfo = Field(..., description="Información de acceso del usuario")
    openings: Optional[List[OpeningInfo]] = Field(
        None,
        description="Aperturas registradas (solo visible para sender)"
    )
    proposed_cases: Optional[List[ProposedCaseInfo]] = Field(
        None,
        description="Expedientes propuestos para vincular"
    )


class NoteDetailResponse(NoteDetail):
    """Respuesta para detalle de nota"""
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "document_id": "uuid-1",
                "official_number": "NOTA-2026-000001-MUNI",
                "reference": "Solicitud de información",
                "content": {"html": "<p>Contenido...</p>"},
                "signed_at": "2026-02-03T10:00:00",
                "ai_summary": "Esta nota solicita información sobre...",
                "signers": [{"user_id": "uuid", "full_name": "Juan Pérez"}],
                "document_type": {"name": "Nota", "acronym": "NOTA"},
                "department_name": "Intendencia",
                "recipients": {
                    "to": [{"sector_id": "uuid-2", "acronym": "PRIV", "department_name": "Hacienda"}],
                    "cc": [],
                    "bcc": None,
                    "is_sender": False,
                    "my_recipient_type": "TO"
                },
                "my_access": {
                    "is_sender": False,
                    "recipient_type": "TO",
                    "first_open": True,
                    "opened_at": "2026-02-03T11:00:00"
                },
                "openings": None
            }
        }
    )
