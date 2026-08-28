
from fastapi import APIRouter
from .registries import router as registries_router
from .records import router as records_router
from .fields import router as fields_router
from .documents import router as documents_router
from .cases import router as cases_router
from .relations import router as relations_router

rlm_router = APIRouter(
    prefix="/api/v1",
    tags=["rlm"],
    responses={
        404: {"description": "No encontrado"},
        403: {"description": "Sin permisos"},
        401: {"description": "No autenticado"},
        500: {"description": "Error interno del servidor"}
    }
)

rlm_router.include_router(registries_router)
rlm_router.include_router(records_router)
rlm_router.include_router(fields_router)
rlm_router.include_router(documents_router)
rlm_router.include_router(cases_router)
rlm_router.include_router(relations_router)
