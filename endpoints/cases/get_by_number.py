"""
Endpoint para obtener un expediente por número exacto.
Devuelve exactamente los mismos datos que list_cases pero para un expediente específico.
"""

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, Field
from typing import Dict, Any, Optional
from auth import get_current_user
from models.schemas import AuthenticatedUser
from services.case_service import CaseService
from shared.exceptions import exception_to_http_exception, NotFoundError
from shared.utils import get_authenticated_user, get_user_global_search_flags
from shared.dependencies import get_tenant_schema
from config.constants import CASE_BY_NUMBER_SUCCESS, CASE_BY_NUMBER_NOT_FOUND, CASE_BY_NUMBER_ERROR
from shared.logging import get_logger
logger = get_logger(__name__)

router = APIRouter()


# ============================================================================
# RESPONSE MODELS
# ============================================================================

class CaseByNumberResponse(BaseModel):
    """Respuesta de búsqueda de expediente por número"""
    success: bool = Field(True, examples=[True])
    data: Optional[Dict[str, Any]] = Field(default=None, description="Datos del expediente encontrado")
    message: str = Field(..., examples=["Expediente encontrado"])


# ============================================================================
# ENDPOINTS
# ============================================================================

@router.get("/number/{case_number}", response_model=CaseByNumberResponse)
async def get_case_by_number(
    request: Request,
    case_number: str = Path(..., description="Número exacto del expediente", examples=["EXP-2025-001-SMG"]),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
) -> CaseByNumberResponse:
    """
    Obtener un expediente por su número exacto sin restricciones de usuario.
    Cualquier usuario autenticado puede buscar cualquier expediente.

    Args:
        case_number: Número exacto del expediente (ej: "EXP-2025-001")
        current_user: Usuario autenticado

    Returns:
        Dict con los mismos datos que list_cases pero para un expediente específico:
        - case: Datos del expediente encontrado (mismo formato que list_cases)
        - found: True si se encontró

    Raises:
        NotFoundError: Si no se encuentra el expediente
    """
    try:
        logger.info(f"Searching for case by number: {case_number}")

        # Validar usuario autenticado
        db_user_id = get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)

        case_service = CaseService()

        # Verificar flag de busqueda global de expedientes
        flags = get_user_global_search_flags(db_user_id, schema_name=schema_name)

        if flags["can_global_search_cases"]:
            # Busqueda global sin restricciones
            result = case_service.get_case_by_exact_number_unrestricted(case_number=case_number, schema_name=schema_name)
        else:
            # Busqueda restringida a sectores del usuario
            result = case_service.get_case_by_exact_number_unrestricted(
                case_number=case_number, user_id=db_user_id, schema_name=schema_name
            )
        
        # Si no se encontró el caso
        if not result:
            logger.warning(f"Case not found: {case_number}")
            raise NotFoundError(CASE_BY_NUMBER_NOT_FOUND.format(case_number=case_number))
        
        logger.info(f"Case found: {case_number}")
        
        return CaseByNumberResponse(
            success=True,
            data=result,
            message=CASE_BY_NUMBER_SUCCESS.format(case_number=case_number)
        )
        
    except Exception as e:
        logger.error(f"Error in get_case_by_number endpoint: {str(e)}")
        raise exception_to_http_exception(e)