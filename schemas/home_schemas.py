
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


def build_sign_href(document_id: str) -> str:
    return f"/documentos-firma?documentId={document_id}"


def build_memo_href(document_id: str) -> str:
    return f"/ccoo/{document_id}?type=memo"


def build_note_href(document_id: str) -> str:
    return f"/ccoo/{document_id}"


def build_case_href(case_id: str) -> str:
    return f"/expedientes/{case_id}"


class ActorInfo(BaseModel):
    name: Optional[str] = Field(None, example="Juan Pérez")
    photo_url: Optional[str] = Field(None, example="https://example.com/photo.jpg")
    sector_label: Optional[str] = Field(None, example="ADGEN#LEGAL", description="acronym#acronym cuando el remitente es un sector (memo/nota)")


class SignItem(BaseModel):
    key: str = Field(..., example="sign:550e8400-e29b-41d4-a716-446655440000")
    document_id: str
    reference: Optional[str] = None
    document_number: Optional[str] = None
    document_type_acronym: Optional[str] = None
    document_type_name: Optional[str] = None
    signer_role: str = Field(..., example="signer", description="'numerator' | 'signer'")
    sent_to_sign_at: Optional[str] = None
    short_ai_summary: Optional[str] = Field(
        None, description="Resumen corto del borrador; el Home lo muestra al pasar el mouse."
    )
    creator: ActorInfo
    href: str = Field(..., example="/documentos-firma?documentId=550e8400-e29b-41d4-a716-446655440000")


class MemoItem(BaseModel):
    key: str = Field(..., example="memo:550e8400-e29b-41d4-a716-446655440000")
    document_id: str
    official_number: Optional[str] = None
    reference: Optional[str] = None
    ai_summary: Optional[str] = None
    short_ai_summary: Optional[str] = None
    signed_at: Optional[datetime] = None
    creator: ActorInfo
    href: str


class NoteItem(BaseModel):
    key: str = Field(..., example="note:550e8400-e29b-41d4-a716-446655440000")
    document_id: str
    official_number: Optional[str] = None
    reference: Optional[str] = None
    ai_summary: Optional[str] = None
    short_ai_summary: Optional[str] = None
    signed_at: Optional[datetime] = None
    sender: ActorInfo
    href: str


class ActionableResponse(BaseModel):
    sign: List[SignItem] = Field(default_factory=list)
    memo: List[MemoItem] = Field(default_factory=list)
    note: List[NoteItem] = Field(default_factory=list)


class ActionableCountBySource(BaseModel):
    sign: int = 0
    memo: int = 0
    note: int = 0


class CountResponse(BaseModel):
    actionable_total: int = Field(..., example=5)
    by_source: ActionableCountBySource


class ResponsibleItem(BaseModel):
    key: str = Field(..., example="responsible:550e8400-e29b-41d4-a716-446655440000")
    movement_id: str
    case_id: str
    case_number: Optional[str] = None
    case_reference: Optional[str] = None
    case_type: Optional[str] = None
    reason: Optional[str] = None
    created_at: datetime
    actor: ActorInfo
    href: str


class MentionItem(BaseModel):
    key: str = Field(..., example="mention:550e8400-e29b-41d4-a716-446655440000")
    movement_id: str
    case_id: str
    case_number: Optional[str] = None
    case_reference: Optional[str] = None
    case_type: Optional[str] = None
    reason: Optional[str] = None
    created_at: datetime
    actor: ActorInfo
    can_view: bool = Field(..., description="false = sin acceso al expediente; front muestra candado y no navega")
    href: Optional[str] = Field(None, description="null si can_view=false")


class FailedSignatureItem(BaseModel):
    key: str = Field(..., example="signature_failed:550e8400-e29b-41d4-a716-446655440000")
    session_id: str
    document_id: str
    document_reference: Optional[str] = None
    reason: Optional[str] = Field(None, description="Código máquina, para soporte. No mostrarlo crudo.")
    message: str = Field(..., description="Qué pasó, en criollo", example="Se venció la reserva del número mientras se firmaba tu documento.")
    next_step: str = Field(..., description="Qué hacer", example="El documento quedó en tus Documentos: podés reintentar la firma.")
    created_at: datetime
    href: str


class CaseGroup(BaseModel):
    case_id: str
    case_number: Optional[str] = None
    case_reference: Optional[str] = None
    case_type: Optional[str] = None
    short_ai_summary: Optional[str] = None
    new_count: int = Field(..., example=3)
    last_move_at: datetime
    href: str


class CaseMovementsPage(BaseModel):
    items: List[CaseGroup] = Field(default_factory=list)
    next_cursor: Optional[str] = Field(None, description="Cursor opaco (last_move_at|case_id) para la próxima página, o null si no hay más")


class CasesResponse(BaseModel):
    scope: str = Field(..., example="mine")
    responsible: List[ResponsibleItem] = Field(default_factory=list)
    mention: List[MentionItem] = Field(default_factory=list)
    failed_signatures: List[FailedSignatureItem] = Field(
        default_factory=list,
        description="GDI-218: firmas que fallaron definitivamente y el usuario todavía no descartó",
    )
    case_movements: CaseMovementsPage


class UnassignedUnownedItem(BaseModel):
    case_id: str
    case_number: Optional[str] = None
    case_reference: Optional[str] = None
    case_type: Optional[str] = None
    created_at: datetime
    ai_summary: Optional[str] = Field(None, description="cases.ai_summary o short_ai_summary, para el ResumeToggle del front")
    href: str


class UnassignedTaskItem(BaseModel):
    task_id: str
    case_id: str
    case_number: Optional[str] = None
    case_reference: Optional[str] = None
    case_type: Optional[str] = None
    reason: Optional[str] = None
    created_at: datetime
    ai_summary: Optional[str] = None
    href: str


class UnassignedUnownedBlock(BaseModel):
    items: List[UnassignedUnownedItem] = Field(default_factory=list)
    total: int = 0


class UnassignedTasksBlock(BaseModel):
    items: List[UnassignedTaskItem] = Field(default_factory=list)
    total: int = 0


class UnassignedResponse(BaseModel):
    unowned: UnassignedUnownedBlock
    tasks: UnassignedTasksBlock


class DismissRequest(BaseModel):
    key: str = Field(..., pattern=r"^(responsible|mention|seen:signature_failed|signature_failed):.+", example="mention:550e8400-e29b-41d4-a716-446655440000")
