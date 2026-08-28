
from fastapi import APIRouter
from .list_sectors import router as list_sectors_router
from models.tags import Tags

router = APIRouter(prefix="/api/v1/sectors", tags=[Tags.SECTORS])

router.include_router(list_sectors_router)