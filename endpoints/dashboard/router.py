"""
Router principal para todos los endpoints de Dashboard
Registra y configura todas las rutas del sistema de dashboard
"""

from fastapi import APIRouter
from .feed import router as feed_router
from .stats import router as stats_router

# Router principal para dashboard
dashboard_router = APIRouter(
    prefix="/api/v1/dashboard",
    tags=["dashboard"],
    responses={
        404: {"description": "No encontrado"},
        403: {"description": "Sin permisos"},
        401: {"description": "No autenticado"},
        500: {"description": "Error interno del servidor"}
    }
)

# Registrar todos los sub-routers
dashboard_router.include_router(feed_router)
dashboard_router.include_router(stats_router)
