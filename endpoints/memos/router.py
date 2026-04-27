"""
Router principal para el modulo de MEMOS.
"""

from fastapi import APIRouter
from .received import router as received_router
from .sent import router as sent_router
from .archived import router as archived_router
from .archive import router as archive_router
from .unread_count import router as unread_count_router
from .detail import router as detail_router

# Router principal de memos
router = APIRouter(
    prefix="/memos",
    tags=["Memos"]
)

# Incluir sub-routers
# IMPORTANTE: archived, archive y unread-count DEBEN ir ANTES de detail para que
# las rutas /archived, /{id}/archive y /unread-count no sean capturadas por /{id}
router.include_router(received_router)
router.include_router(sent_router)
router.include_router(archived_router)
router.include_router(archive_router)
router.include_router(unread_count_router)
router.include_router(detail_router)
