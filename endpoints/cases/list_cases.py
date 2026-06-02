"""Endpoint para listar expedientes del usuario autenticado."""

from shared.logging import get_logger
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

from auth import get_current_user
from models.schemas import AuthenticatedUser
from services.case_service import CaseService
from shared.exceptions import exception_to_http_exception, ValidationError, BusinessLogicError
from shared.utils import get_authenticated_user
from shared.dependencies import get_tenant_schema
from config.constants import (
    CASE_LIST_SUCCESS_MESSAGE,
    DEFAULT_PAGE,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    USER_UNAUTHENTICATED_ERROR
)

logger = get_logger(__name__)
router = APIRouter(tags=["expedientes"])


class CaseType(BaseModel):
    """Tipo de expediente"""
    name: str = Field(..., example="Expediente Administrativo")
    acronym: str = Field(..., example="EXP-ADM")


class Sector(BaseModel):
    """Sector con departamento"""
    acronym: str = Field(..., example="SECOBRA")
    department: str = Field(..., example="Secretaría de Obras Públicas")
    sector_color: Optional[str] = Field(None, example="#FF6B6B")


class CaseItem(BaseModel):
    """Datos de un expediente en la lista"""
    id: str = Field(..., example="550e8400-e29b-41d4-a716-446655440000")
    case_number: str = Field(..., example="EXP-2024-00001-SMG-ADGEN")
    reference: str = Field(..., example="Expediente de prueba")
    last_modified_at: datetime = Field(..., example="2024-12-09T10:30:00")
    case_type: CaseType
    access_reason: str = Field(..., example="ADMINSECTOR", description="Nivel de acceso: ADMINSECTOR, ASSIGNEDSECTOR, VIEW")
    admin_sector: Optional[Sector] = Field(None, description="Sector administrativo propietario")
    assigned_sectors: List[Sector] = Field(default_factory=list, description="Sectores asignados al expediente")
    short_ai_summary: Optional[str] = Field(None, description="Resumen corto generado por IA")
    ai_summary: Optional[str] = Field(None, description="Resumen completo generado por IA")
    is_favorite: bool = Field(False, description="Indica si el usuario marcó este expediente como favorito")


class CaseListData(BaseModel):
    """Estructura de datos para lista de expedientes"""
    cases: List[CaseItem] = Field(..., example=[
        {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "case_number": "EXP-2024-00001-SMG-ADGEN",
            "reference": "Expediente de prueba 1",
            "last_modified_at": "2024-12-09T10:30:00",
            "case_type": {
                "name": "Expediente Administrativo",
                "acronym": "EXP-ADM"
            },
            "access_reason": "ADMINSECTOR",
            "admin_sector": {
                "acronym": "SECOBRA",
                "department": "Secretaría de Obras Públicas"
            },
            "assigned_sectors": [
                {
                    "acronym": "SECLEGAL",
                    "department": "Secretaría Legal"
                }
            ]
        }
    ])
    total: int = Field(..., example=25, description="Total de expedientes encontrados")
    page: int = Field(..., example=1, description="Página actual")
    page_size: int = Field(..., example=10, description="Elementos por página")
    total_pages: int = Field(..., example=3, description="Total de páginas")


class CaseListResponse(BaseModel):
    """Modelo de respuesta para lista de expedientes"""
    success: bool = Field(..., example=True)
    data: CaseListData
    message: str = Field(..., example="Se encontraron 25 expedientes")


@router.get("/", response_model=CaseListResponse)
async def list_cases(
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
    page: int = Query(DEFAULT_PAGE, ge=1, description="Número de página"),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Elementos por página"),
    status: Optional[str] = Query(None, description="Filtrar por estado: active, inactive, archived"),
    search: Optional[str] = Query(None, description="Buscar en referencia y número del expediente"),
    date_filter: Optional[str] = Query(None, description="Filtro de fecha: hoy, ayer, ultimos_7_dias, ultimos_30_dias"),
    date_from: Optional[str] = Query(None, description="Fecha de inicio (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Fecha de fin (YYYY-MM-DD)"),
    sector_filter: Optional[str] = Query(None, description="UUID del sector asignado"),
    trata_filter: Optional[str] = Query(None, description="UUID del sector administrativo (trata)"),
    view: Optional[str] = Query(None, description="Vista: asignado|admin|actuante|favoritos"),
    sort_order: str = Query(default="desc", description="Orden de resultados: asc | desc")
) -> CaseListResponse:
    """Lista expedientes del usuario autenticado con filtros avanzados."""
    try:
        # Validar usuario autenticado primero (antes de logging)
        tenant_user_id = getattr(request.state, 'tenant_user_id', None)
        if not tenant_user_id:
            raise ValidationError(USER_UNAUTHENTICATED_ERROR)

        logger.info(f"Listing cases - User: {tenant_user_id[:8]}, Page: {page}, View: {view}")

        # Obtener usuario validado
        db_user_id = await get_authenticated_user(tenant_user_id, schema_name=schema_name)

        # Sanitizar sort_order antes de pasar al service
        sort_order_safe = "asc" if sort_order.lower() == "asc" else "desc"

        result = await CaseService.get_cases_by_user(
            user_id=db_user_id,
            page=page,
            page_size=page_size,
            status_filter=status,
            search_filter=search,
            date_filter=date_filter,
            date_from=date_from,
            date_to=date_to,
            sector_filter=sector_filter,
            trata_filter=trata_filter,
            view=view,
            sort_order=sort_order_safe,
            schema_name=schema_name
        )
        
        logger.info(f"Found {result['total']} cases")
        
        return CaseListResponse(
            success=True,
            data=result,
            message=CASE_LIST_SUCCESS_MESSAGE.format(total=result['total'])
        )
        
    except (ValidationError, BusinessLogicError) as e:
        logger.error(f"Error listing cases: {str(e)}")
        raise exception_to_http_exception(e)


@router.get("/search", response_model=CaseListResponse)
async def search_cases(
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
    page: int = Query(DEFAULT_PAGE, ge=1, description="Número de página"),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="Elementos por página"),
    search: Optional[str] = Query(None, description="Buscar en referencia y número del expediente"),
    status: Optional[str] = Query(None, description="Filtrar por estado: active, inactive, archived"),
    date_filter: Optional[str] = Query(None, description="Filtro de fecha: hoy, ayer, ultimos_7_dias, ultimos_30_dias"),
    sector_filter: Optional[str] = Query(None, description="Acrónimo/UUID del sector"),
) -> CaseListResponse:
    """
    Buscar expedientes del usuario autenticado.

    URL canónica equivalente al endpoint REST del Gateway (SET B)
    `GET /api/v1/cases/search`. Reusa el MISMO service que `list_cases`
    (`CaseService.get_cases_by_user`). Accesible por JWT y por API Key.

    Es un subconjunto de filtros de `GET /api/v1/cases/`; para filtros
    avanzados (view, trata_filter, date_from/to, sort_order) usar ese endpoint.
    """
    try:
        tenant_user_id = getattr(request.state, 'tenant_user_id', None)
        if not tenant_user_id:
            raise ValidationError(USER_UNAUTHENTICATED_ERROR)

        logger.info(f"Searching cases - User: {tenant_user_id[:8]}, Page: {page}, search={search}")

        db_user_id = await get_authenticated_user(tenant_user_id, schema_name=schema_name)

        result = await CaseService.get_cases_by_user(
            user_id=db_user_id,
            page=page,
            page_size=page_size,
            status_filter=status,
            search_filter=search,
            date_filter=date_filter,
            sector_filter=sector_filter,
            schema_name=schema_name
        )

        logger.info(f"Found {result['total']} cases")

        return CaseListResponse(
            success=True,
            data=result,
            message=CASE_LIST_SUCCESS_MESSAGE.format(total=result['total'])
        )

    except (ValidationError, BusinessLogicError) as e:
        logger.error(f"Error searching cases: {str(e)}")
        raise exception_to_http_exception(e)