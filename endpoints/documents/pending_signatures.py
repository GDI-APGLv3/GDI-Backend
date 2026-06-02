"""
Endpoint para listar documentos pendientes de firma del usuario autenticado.

URL canónica `GET /api/v1/documents/pending-signatures`, equivalente al endpoint
REST del Gateway (SET B) `documents.get_pending_signatures`. Endpoint THIN: delega
toda la lógica al service `get_pending_signatures_for_user`.

Accesible por JWT y por API Key (auth pluggable, Fase 1 S8-001).
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from auth import get_current_user
from models.schemas import AuthenticatedUser
from models.tags import Tags
from shared.dependencies import get_tenant_schema
from shared.utils import get_authenticated_user
from shared.exceptions import exception_to_http_exception
from shared.logging import get_logger
from services.documents.retrieval.pending_signatures import get_pending_signatures_for_user

logger = get_logger(__name__)
router = APIRouter(tags=[Tags.DOCUMENTOS])


class PendingSignatureDocumentType(BaseModel):
    acronym: Optional[str] = Field(None, example="IF")
    name: Optional[str] = Field(None, example="Informe")


class PendingSignatureCreator(BaseModel):
    name: Optional[str] = Field(None, example="Juan Pérez")
    photo_url: Optional[str] = Field(None, example="https://storage.com/p.jpg")


class PendingSignatureItem(BaseModel):
    document_id: str = Field(..., example="550e8400-e29b-41d4-a716-446655440000")
    reference: Optional[str] = Field(None, example="Informe técnico Q4")
    document_number: Optional[str] = Field(None, example="IF-2025-0000001-MUNI")
    document_type: PendingSignatureDocumentType
    signer_role: str = Field(..., example="signer")
    signing_order: int = Field(..., example=1)
    sent_to_sign_at: Optional[str] = Field(None, example="2025-12-02T10:30:00")
    creator: PendingSignatureCreator


class PendingSignaturesResponse(BaseModel):
    pending_signatures: List[PendingSignatureItem]
    total: int = Field(..., example=2)


@router.get(
    "/api/v1/documents/pending-signatures",
    response_model=PendingSignaturesResponse,
    summary="Documentos pendientes de firma (mi turno)",
    description=(
        "Lista los documentos donde es el turno del usuario autenticado para firmar. "
        "URL canónica equivalente al endpoint del Gateway (SET B) "
        "`GET /api/v1/documents/pending-signatures`."
    ),
)
async def get_pending_signatures(
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
) -> PendingSignaturesResponse:
    """Lista documentos esperando la firma del usuario autenticado."""
    try:
        db_user_id = await get_authenticated_user(
            request.state.tenant_user_id, schema_name=schema_name
        )
        logger.info(f"GET /documents/pending-signatures - user={db_user_id}")
        return await get_pending_signatures_for_user(db_user_id, schema_name=schema_name)
    except Exception as e:
        logger.error(f"Error in get_pending_signatures endpoint: {str(e)}")
        raise exception_to_http_exception(e)
