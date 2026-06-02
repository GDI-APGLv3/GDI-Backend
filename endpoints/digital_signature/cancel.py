"""POST /digital-signature/cancel — cancelacion explicita desde Frontend."""
import logging
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from auth import get_current_user
from models.schemas import AuthenticatedUser
from database import fetch_one, execute
from services.documents.signing.r2_lock import release_signing_lock_R2_fail
from services.documents.signing.audit_logger import log_signature_event
from services.documents.signing.providers.autofirma import AutoFirmaProvider
from shared.dependencies import get_tenant_schema

log = logging.getLogger(__name__)
router = APIRouter()


class CancelRequest(BaseModel):
    session_id: str


@router.post("/digital-signature/cancel")
async def cancel_signing(
    body: CancelRequest,
    request: Request,
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
) -> dict:
    session_id = body.session_id
    if not session_id.isalnum():
        raise HTTPException(status_code=400, detail="session_id_invalid")

    row = await fetch_one(
        """
        SELECT session_id, file_id, schema_name, user_id::text, document_id::text,
               is_numerator, number, status
        FROM public.digital_signature_sessions
        WHERE session_id = $1
        """,
        session_id,
        schema_name="public",
    )

    if not row:
        raise HTTPException(status_code=404, detail="session_not_found")

    session = dict(row)
    if session["user_id"] != str(request.state.tenant_user_id):
        raise HTTPException(status_code=403, detail="not_session_owner")

    if session["status"] != "pending":
        return {"status": session["status"]}

    # Cancelar provider (limpiar Redis)
    provider = AutoFirmaProvider()
    await run_in_threadpool(
        provider.cancel_signing,
        session_id=session_id,
        schema_name=session["schema_name"],
        file_id=session.get("file_id"),
    )

    # Release lock R2
    await release_signing_lock_R2_fail(
        schema_name=session["schema_name"],
        doc_id=session["document_id"],
    )

    # Marcar cancelled
    await execute(
        """
        UPDATE public.digital_signature_sessions
        SET status = 'cancelled', cancelled_at = NOW(), updated_at = NOW()
        WHERE session_id = $1
        """,
        session_id,
        schema_name="public",
    )

    try:
        await log_signature_event(
            schema_name=session["schema_name"],
            document_id=session["document_id"],
            user_id=session["user_id"],
            signature_method="digital_token",
            result="fail",
            failure_reason="cancelled_by_user",
            session_id=session_id,
        )
    except Exception as e:
        log.warning(f"cancel audit_log soft-fail: {e}")

    return {"status": "cancelled"}
