"""
Router principal para todos los endpoints de expedientes
Registra y configura todas las rutas del sistema de expedientes
"""

from fastapi import APIRouter
from .list_cases import router as list_router
from .create_case import router as create_router
from .get_case_detail import router as detail_router
from .transfer_case import router as transfer_router
from .link_document import router as link_document_router
from .prepare_actions import router as prepare_actions_router
from .get_by_number import router as get_by_number_router
from .subsanar_document import router as subsanar_router
from .proposed_documents import router as proposed_documents_router

# Router principal para expedientes
cases_router = APIRouter(
    prefix="/api/v1/cases",
    tags=["expedientes"],
    responses={
        404: {"description": "No encontrado"},
        403: {"description": "Sin permisos"},
        401: {"description": "No autenticado"},
        500: {"description": "Error interno del servidor"}
    }
)

# Registrar todos los sub-routers
cases_router.include_router(list_router)
cases_router.include_router(create_router)
cases_router.include_router(detail_router)
cases_router.include_router(transfer_router)
cases_router.include_router(link_document_router)
cases_router.include_router(prepare_actions_router)
cases_router.include_router(get_by_number_router)
cases_router.include_router(subsanar_router)
cases_router.include_router(proposed_documents_router)
