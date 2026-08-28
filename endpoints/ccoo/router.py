
from fastapi import APIRouter
from .received import router as received_router
from .sent import router as sent_router
from .archived import router as archived_router

router = APIRouter(
    prefix="/ccoo",
    tags=["CCOO"]
)

router.include_router(received_router)
router.include_router(sent_router)
router.include_router(archived_router)
