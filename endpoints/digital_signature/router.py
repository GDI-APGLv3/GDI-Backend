from fastapi import APIRouter

from .storage import router as storage_router
from .poll import router as poll_router
from .cancel import router as cancel_router
from .poll_async import router as poll_async_router
from .batch import router as batch_router
from .version_info import router as version_info_router

digital_signature_router = APIRouter()
digital_signature_router.include_router(storage_router)
digital_signature_router.include_router(poll_router)
digital_signature_router.include_router(cancel_router)
digital_signature_router.include_router(poll_async_router)
digital_signature_router.include_router(batch_router)
digital_signature_router.include_router(version_info_router)
