"""Endpoint para crear nuevos expedientes"""

from shared.logging import get_logger
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from auth import get_current_user
from services.case_service import CaseService
from services.cases.creation import create_case_with_cover_service
from shared.validation import validate_uuid
from shared.exceptions import (
    ValidationError,
    NotFoundError,
    BusinessLogicError,
    exception_to_http_exception
)
from shared.utils import get_authenticated_user
from shared.dependencies import get_tenant_schema
from services.cache import get_cached
from config.constants import (
    CASE_CREATED_SUCCESS_MESSAGE,
    CASE_CREATION_ERROR,
    CASE_TEMPLATES_FOUND_MESSAGE,
    CASE_TEMPLATES_ERROR,
    CACHE_TTL_TEMPLATES,
)

logger = get_logger(__name__)

# Inicializar router
router = APIRouter(tags=["expedientes"])

class CaseTemplate(BaseModel):
    """Modelo de plantilla de expediente"""
    id: str = Field(..., example="550e8400-e29b-41d4-a716-446655440000")
    name: str = Field(..., example="Expediente Administrativo")
    acronym: str = Field(..., example="EXP-ADM")
    description: Optional[str] = Field(None, example="Expediente de habilitacion comercial")
    filing_department_name: Optional[str] = Field(None, example="Legal y Técnica")
    filing_department_acronym: Optional[str] = Field(None, example="LEGAL")
    filing_department_color: Optional[str] = Field(None, example="#7222C3")


class TemplatesData(BaseModel):
    """Datos de respuesta de plantillas"""
    templates: List[CaseTemplate] = Field(..., example=[
        {
            "id": "550e8400-e29b-41d4-a716-446655440000",
            "name": "Expediente Administrativo",
            "acronym": "EXP-ADM"
        },
        {
            "id": "550e8400-e29b-41d4-a716-446655440001",
            "name": "Expediente Judicial",
            "acronym": "EXP-JUD"
        }
    ])
    total: int = Field(..., example=2)


class TemplatesResponse(BaseModel):
    """Respuesta para endpoint de plantillas"""
    success: bool = Field(..., example=True)
    data: TemplatesData
    message: str = Field(..., example="Se encontraron 2 plantillas")


class CreateCaseRequest(BaseModel):
    """Modelo para solicitud de creación de expediente"""
    case_template_id: str = Field(..., description="ID de la plantilla de expediente", example="eaeef535-0754-45d6-8b62-ee7803c62edf")
    reference: str = Field(..., min_length=5, max_length=250, description="Descripción/referencia del expediente", example="Referencia de Prueba")
    owner_sector_id: Optional[str] = Field(None, description="ID del sector propietario (opcional, usa sector del usuario por defecto)", example="")

class CreateCaseResponse(BaseModel):
    """Modelo para respuesta de creación de expediente"""
    success: bool
    data: dict
    message: str

@router.post("/", response_model=CreateCaseResponse)
async def create_case(
    request: CreateCaseRequest,
    current_user: dict = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """Crear nuevo expediente con carátula automática."""
    try:
        logger.info(f"Creating case - User: {current_user.user_id[:8]}, Template: {request.case_template_id[:8]}")

        # Validar usuario autenticado
        db_user_id = await get_authenticated_user(current_user.user_id, schema_name=schema_name)

        result = await create_case_with_cover_service(
            case_template_id=request.case_template_id,
            reference=request.reference,
            user_id=db_user_id,
            owner_sector_id=request.owner_sector_id,
            schema_name=schema_name
        )
        
        logger.info(f"Case created successfully: {result['case']['case_number']} - {result['cover']['official_number']}")
        
        return CreateCaseResponse(
            success=True,
            data=result,
            message=CASE_CREATED_SUCCESS_MESSAGE.format(
                case_number=result['case']['case_number'],
                cover_number=result['cover']['official_number']
            )
        )

    except (ValidationError, NotFoundError) as e:
        logger.error(f"Error creating case: {str(e)}", exc_info=True)
        raise exception_to_http_exception(e)
    except Exception as e:
        logger.error(f"Unexpected error creating case: {str(e)}", exc_info=True)
        raise exception_to_http_exception(e)

@router.get("/templates", response_model=TemplatesResponse)
async def get_case_templates(
    current_user: dict = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
) -> TemplatesResponse:
    """Obtener plantillas de expedientes disponibles."""
    try:
        logger.info(f"Fetching case templates for user: {current_user.user_id[:8]}")

        # Validar usuario autenticado
        db_user_id = await get_authenticated_user(current_user.user_id, schema_name=schema_name)

        templates = await get_cached(
            cache_key=f"case_templates:all:{schema_name}",
            fetch_func=lambda: CaseService.get_available_templates(db_user_id, schema_name=schema_name),
            ttl=CACHE_TTL_TEMPLATES
        )
        
        logger.info(f"Found {len(templates)} case templates")
        
        return TemplatesResponse(
            success=True,
            data=TemplatesData(
                templates=templates,
                total=len(templates)
            ),
            message=CASE_TEMPLATES_FOUND_MESSAGE.format(total=len(templates))
        )

    except BusinessLogicError as e:
        logger.error(f"Business logic error fetching templates: {str(e)}")
        raise exception_to_http_exception(e)
    except Exception as e:
        logger.error(f"Unexpected error fetching templates: {str(e)}", exc_info=True)
        raise exception_to_http_exception(
            BusinessLogicError("Error al obtener las plantillas de expedientes")
        )