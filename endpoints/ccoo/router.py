"""
Router principal para el modulo de CCOO (Comunicaciones Oficiales).
"""

from fastapi import APIRouter
from .received import router as received_router
from .sent import router as sent_router
from .archived import router as archived_router

# Router principal de CCOO
router = APIRouter(
    prefix="/ccoo",
    tags=["CCOO"]
)

# Incluir sub-routers
router.include_router(received_router)
router.include_router(sent_router)
router.include_router(archived_router)
