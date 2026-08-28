from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

class PDFRequest(BaseModel):
    urlLogo: Optional[str] = Field(None, description="URL pública de la imagen del logo.")
    NameAcronyType: str = Field(..., description="Acrónimo del nombre del tipo (ej. 'GDI', 'ACME').")
    TypeDocument: str = Field(..., description="Tipo de documento (ej. 'INFORME DE PRUEBA').")
    Reference: str = Field(..., description="Referencia del documento (ej. 'REF-001-2025').")
    frase_anual: Optional[str] = Field(None, description="Frase anual institucional opcional.")
    Text: Dict[str, Any] = Field(..., description="Contenido principal del documento en formato JSON.")

class CaseRequest(BaseModel):
    urlLogo: Optional[str] = Field(None, description="URL pública de la imagen del logo.")
    NameAcronyType: str = Field(..., description="Acrónimo del nombre del tipo (ej. 'GDI', 'ACME').")
    document_type: str = Field(..., description="Tipo de documento para la carátula.")
    reference: str = Field(..., description="Referencia del documento.")
    frase_anual: Optional[str] = Field(None, description="Frase anual institucional opcional.")
    case_number: str = Field(..., description="Número de expediente.")
    acrony_case_type: str = Field(..., description="Acrónimo del tipo de expediente.")
    case_type: str = Field(..., description="Tipo de expediente.")
    case_motive: str = Field(..., description="Motivo del expediente.")
    initiating_division: str = Field(..., description="Repartición iniciadora.")
    creator: str = Field(..., description="Nombre del caratulador.")

class MoveRequest(BaseModel):
    urlLogo: Optional[str] = Field(None, description="URL pública de la imagen del logo.")
    NameAcronyType: str = Field(..., description="Acrónimo del nombre del tipo (ej. 'GDI', 'ACME').")
    document_type: str = Field(..., description="Tipo de documento.")
    reference: str = Field(..., description="Referencia del documento.")
    frase_anual: Optional[str] = Field(None, description="Frase anual institucional opcional.")
    tipo_movimiento: str = Field(..., description="Tipo de movimiento.")
    area_requiriente: str = Field(..., description="Área requiriente (DE).")
    area_receptora: str = Field(..., description="Área receptora (A).")
    motivo: str = Field(..., description="Motivo del movimiento.")

class ImportRequest(BaseModel):
    urlLogo: Optional[str] = Field(None, description="URL pública de la imagen del logo.")
    NameAcronyType: str = Field(..., description="Acrónimo del nombre del tipo (ej. 'GDI', 'ACME').")
    document_type: str = Field(..., description="Tipo de documento.")
    reference: str = Field(..., description="Referencia del documento.")
    frase_anual: Optional[str] = Field(None, description="Frase anual institucional opcional.")
    cantidad_paginas: int = Field(..., description="Cantidad de páginas del PDF importado.")

class NoteRequest(BaseModel):
    urlLogo: Optional[str] = Field(None, description="URL pública de la imagen del logo.")
    NameAcronyType: str = Field(..., description="Acrónimo del nombre del tipo (ej. 'GDI').")
    document_type: str = Field(..., description="Tipo de documento (ej. 'NOTA').")
    reference: str = Field(..., description="Referencia del documento.")
    frase_anual: Optional[str] = Field(None, description="Frase anual institucional opcional.")
    para: Optional[str] = Field(None, description="Destinatario principal (PARA). Si vacío, no aparece.")
    cc: Optional[str] = Field(None, description="Destinatarios en copia (CC). Si vacío, no aparece.")
    Text: Dict[str, Any] = Field(..., description="Contenido HTML del documento.")

class IFRLMRequest(BaseModel):
    urlLogo: Optional[str] = Field(None, description="URL publica del logo.")
    NameAcronyType: str = Field(..., description="Acronimo del tipo.")
    document_type: str = Field(..., description="Tipo de documento.")
    reference: str = Field(..., description="Referencia del documento.")
    frase_anual: Optional[str] = Field(None, description="Frase anual institucional.")
    record_number: str = Field(..., description="Numero del legajo.")
    registry_name: str = Field(..., description="Nombre de la familia del registro.")
    state: str = Field(..., description="Estado actual del legajo.")
    snapshot_html: str = Field(..., description="HTML del snapshot del legajo.")