"""Router para endpoints de firma digital (AutoFirma / digital token)."""
from fastapi import APIRouter

from .storage import router as storage_router
from .poll import router as poll_router
from .cancel import router as cancel_router

digital_signature_router = APIRouter()
digital_signature_router.include_router(storage_router)
digital_signature_router.include_router(poll_router)
digital_signature_router.include_router(cancel_router)
