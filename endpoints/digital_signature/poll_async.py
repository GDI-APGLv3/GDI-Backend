
from shared.logging import get_logger
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel
from typing import Optional

from auth import get_current_user
from models.schemas import AuthenticatedUser
from shared.dependencies import get_tenant_schema
from endpoints.digital_signature.poll import _poll_rate_limit_ok
from services.documents.signing.async_poll_status import get_async_poll_status

log = get_logger(__name__)
router = APIRouter()


class AsyncPollResponse(BaseModel):
    session_id: str
    status: str
    official_number: Optional[str] = None
    auto_link_results: list = []
    reason: Optional[str] = None
    failure_reason: Optional[str] = None


@router.get(
    "/signing/async-poll/{session_id}",
    response_model=AsyncPollResponse,
    summary="Estado de sesión de firma asíncrona",
    description=(
        "Polleado por el frontend luego de recibir 202 del endpoint super-sign. "
        "Retorna el estado actual de la sesión en la cola de firma async. "
        "El campo official_number se completa cuando status='signed'."
    ),
    dependencies=[Depends(get_current_user)],
)
async def poll_async_signing(
    session_id: UUID = Path(..., description="UUID de la sesión devuelto en el 202"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
) -> AsyncPollResponse:
    session_id_str = str(session_id)

    status = await get_async_poll_status(
        session_id_str,
        str(current_user.user_id),
        schema_name=schema_name,
    )

    if status is None:
        raise HTTPException(status_code=404, detail="Sesión de firma no encontrada")

    if not _poll_rate_limit_ok(str(current_user.user_id), session_id_str):
        raise HTTPException(status_code=429, detail="too_many_poll_requests")

    return AsyncPollResponse(**status)
