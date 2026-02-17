"""
Schemas Pydantic para el módulo de Dashboard (Feed de actividad).
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


# =============================================================================
# SCHEMAS DE FEED
# =============================================================================

class FeedCase(BaseModel):
    """Información del expediente en el feed"""
    id: str = Field(..., example="550e8400-e29b-41d4-a716-446655440000")
    case_number: str = Field(..., example="EE-2026-000017")
    reference: str = Field(..., example="Habilitación Comercial - Panadería Don Luis")
    case_type: str = Field(..., example="HABI")
    case_type_name: str = Field(..., example="Habilitación Comercial")
    ai_summary: Optional[str] = Field(None, example="Expediente de habilitación comercial...")


class FeedUser(BaseModel):
    """Información del usuario que realizó el movimiento"""
    name: str = Field(..., example="Juan Pérez")
    photo_url: Optional[str] = Field(None, example="https://example.com/photo.jpg")
    sector: str = Field(..., example="ADGEN#LEGAL")


class FeedDocument(BaseModel):
    """Información del documento de soporte (si existe)"""
    official_number: str = Field(..., example="INF-2026-00001234")
    reference: str = Field(..., example="Informe de factibilidad")
    ai_summary: Optional[str] = Field(None, example="El informe indica que...")


class FeedMovement(BaseModel):
    """Un movimiento individual en el feed"""
    movement_id: str = Field(..., example="550e8400-e29b-41d4-a716-446655440001")
    movement_type: str = Field(..., example="creation", description="Tipo: creation, transfer, assignment, document_link, status_change, subsanacion")
    message: str = Field(..., example="Juan Pérez creó el expediente")
    reason: str = Field(..., example="Expediente de habilitación comercial")
    created_at: datetime = Field(..., example="2026-01-27T10:30:00")
    is_active: bool = Field(..., example=True)
    case: FeedCase
    user: FeedUser
    supporting_document: Optional[FeedDocument] = None


class FeedData(BaseModel):
    """Estructura de datos para el feed"""
    items: List[FeedMovement] = Field(default_factory=list)
    total: int = Field(..., example=150, description="Total de movimientos encontrados")
    page: int = Field(..., example=1, description="Página actual")
    page_size: int = Field(..., example=20, description="Elementos por página")
    total_pages: int = Field(..., example=8, description="Total de páginas")


class FeedResponse(BaseModel):
    """Respuesta del endpoint de feed"""
    success: bool = Field(..., example=True)
    data: FeedData
    message: str = Field(..., example="Se encontraron 150 movimientos")


# =============================================================================
# SCHEMAS DE STATS
# =============================================================================

class StatsData(BaseModel):
    """Estructura de estadísticas del dashboard"""
    cases_in_sector: int = Field(..., example=5, description="Expedientes activos en el sector del usuario")


class StatsResponse(BaseModel):
    """Respuesta del endpoint de stats"""
    success: bool = Field(..., example=True)
    data: StatsData
    message: str = Field(..., example="Estadísticas obtenidas correctamente")
