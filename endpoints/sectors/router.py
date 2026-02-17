"""
Router principal para endpoints de sectores.
"""

from fastapi import APIRouter
from .list_sectors import router as list_sectors_router
from models.tags import Tags

router = APIRouter(prefix="/api/v1/sectors", tags=[Tags.SECTORS])

# Incluir sub-routers
router.include_router(list_sectors_router)