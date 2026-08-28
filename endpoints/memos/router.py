
from fastapi import APIRouter
from .received import router as received_router
from .sent import router as sent_router
from .archived import router as archived_router
from .archive import router as archive_router
from .unread_count import router as unread_count_router
from .detail import router as detail_router

router = APIRouter(
    prefix="/memos",
    tags=["Memos"]
)

router.include_router(received_router)
router.include_router(sent_router)
router.include_router(archived_router)
router.include_router(archive_router)
router.include_router(unread_count_router)
router.include_router(detail_router)
