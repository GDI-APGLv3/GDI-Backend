"""
Router principal para el módulo de NOTAS.
"""

from fastapi import APIRouter
from .received import router as received_router
from .sent import router as sent_router
from .archived import router as archived_router
from .archive import router as archive_router
from .detail import router as detail_router

# Router principal de notas
router = APIRouter(
    prefix="/notes",
    tags=["Notas"]
)

# Incluir sub-routers
# IMPORTANTE: archived y archive DEBEN ir ANTES de detail para que
# las rutas /archived y /{id}/archive no sean capturadas por /{id}
router.include_router(received_router)
router.include_router(sent_router)
router.include_router(archived_router)
router.include_router(archive_router)
router.include_router(detail_router)
