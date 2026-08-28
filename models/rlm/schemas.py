
from pydantic import BaseModel, Field, model_validator
from typing import Optional, Dict, Any, Literal


class CreateRecordRequest(BaseModel):
    registry_code: str = Field(
        ...,
        description="Código del registro (ej: ARQ, LUM, ORD)",
        example="ARQ"
    )
    data: Dict[str, Any] = Field(
        default_factory=dict,
        description="Campos enriquecidos del legajo"
    )
    display_name: str = Field(..., min_length=1, max_length=200, description="Nombre identificador del legajo")


class UpdateRecordRequest(BaseModel):
    state: Optional[str] = Field(None, description="Nuevo estado del legajo")
    display_name: Optional[str] = Field(None, min_length=1, max_length=200, description="Nombre identificador del legajo")
    reason: Optional[str] = Field(None, description="Motivo del cambio")

    @model_validator(mode='after')
    def at_least_one_field(self):
        if self.state is None and self.display_name is None:
            raise ValueError("Debe enviar al menos 'state' o 'display_name'")
        return self


class UpdateFieldRequest(BaseModel):
    value: Optional[Any] = Field(None, description="Valor del campo")
    expiration_date: Optional[str] = Field(None, description="Fecha de vencimiento (ISO 8601)")
    document_id: Optional[str] = Field(None, description="ID del documento vinculado")
    notes: Optional[str] = Field(None, description="Notas del campo")
    document_reference: Optional[str] = Field(None, description="Referencia del documento vinculado")
    document_resume: Optional[str] = Field(None, description="Resumen del documento vinculado")


class VerifyFieldRequest(BaseModel):
    document_id: str = Field(
        ...,
        description="ID del documento oficial que respalda la verificación"
    )
    notes: Optional[str] = Field(None, description="Notas de verificación")


class LinkDocumentRequest(BaseModel):
    document_id: str = Field(..., description="ID del documento oficial")
    field_name: Optional[str] = Field(None, description="Nombre del campo asociado")
    notes: Optional[str] = Field(None, description="Notas sobre la vinculación")


class LinkCaseRequest(BaseModel):
    case_id: str = Field(..., description="ID del expediente")
    notes: Optional[str] = Field(None, description="Notas sobre la vinculación")


class CreateRelationRequest(BaseModel):
    target_record_id: str = Field(..., description="ID del legajo destino")
    relation_type: Literal["parent", "child", "related", "replaces", "sibling", "cousin"] = Field(
        ...,
        description="Tipo de relación"
    )
    notes: Optional[str] = Field(None, description="Notas sobre la relación")


class RecordResponse(BaseModel):
    success: bool
    data: Dict[str, Any]
    message: str


class RecordListResponse(BaseModel):
    success: bool
    data: Dict[str, Any]
    message: str


class RegistryListResponse(BaseModel):
    success: bool
    data: Dict[str, Any]
    message: str


class RegistryDetailResponse(BaseModel):
    success: bool
    data: Dict[str, Any]
    message: str


class FieldResponse(BaseModel):
    success: bool
    data: Dict[str, Any]
    message: str


class HistoryResponse(BaseModel):
    success: bool
    data: Dict[str, Any]
    message: str


class LinkResponse(BaseModel):
    success: bool
    data: Dict[str, Any]
    message: str


class RelationResponse(BaseModel):
    success: bool
    data: Dict[str, Any]
    message: str
