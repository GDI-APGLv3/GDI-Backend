
from fastapi import APIRouter
from .count import router as count_router
from .actionable import router as actionable_router
from .cases import router as cases_router
from .unassigned import router as unassigned_router
from .dismiss import router as dismiss_router

home_router = APIRouter(
    prefix="/home",
    tags=["home"],
    responses={
        404: {"description": "No encontrado"},
        403: {"description": "Sin permisos"},
        401: {"description": "No autenticado"},
        500: {"description": "Error interno del servidor"},
    },
)

home_router.include_router(count_router)
home_router.include_router(actionable_router)
home_router.include_router(cases_router)
home_router.include_router(unassigned_router)
home_router.include_router(dismiss_router)
