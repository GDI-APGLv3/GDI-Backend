
from typing import Any, Dict, Optional

from database import execute, fetch_one
from shared.logging import get_logger

log = get_logger(__name__)


async def get_async_poll_status(
    session_id: str,
    user_id: str,
    *,
    schema_name: str,
) -> Optional[Dict[str, Any]]:
    row = await fetch_one(
        """
        SELECT
            session_id::text,
            schema_name,
            document_id::text,
            user_id::text,
            status,
            failure_reason,
            payload
        FROM public.signing_sessions
        WHERE session_id = $1::uuid
          -- GDI-266: 'digital_complete' es el cierre de una firma con token,
          -- que el worker termina despues de que el navegador se fue. Entra acá
          -- porque el tracker del front necesita poder seguirlo igual que a
          -- cualquier firma en curso — si no, el usuario cierra el modal y no
          -- se entera de nada hasta que refresque el listado.
          AND job_type   IN ('sign', 'sign_common', 'digital_complete')
        """,
        session_id,
        schema_name="public",
    )

    if (
        not row
        or row["schema_name"] != schema_name
        or str(row["user_id"]).lower() != str(user_id).lower()
    ):
        return None

    internal_status = row["status"]
    public_status: str
    if internal_status == "pending":
        public_status = "queued"
    elif internal_status == "processing":
        public_status = "processing"
    elif internal_status == "signed":
        public_status = "signed"
    elif internal_status == "failed":
        public_status = "failed"
    elif internal_status == "expired":
        public_status = "expired"
    else:
        public_status = internal_status

    healed_official_number: Optional[str] = None
    if public_status in ("queued", "processing"):
        doc = await fetch_one(
            """
            SELECT status, document_number
            FROM document_draft
            WHERE id = $1::uuid
            """,
            row["document_id"],
            schema_name=row["schema_name"],
        )
        if doc and doc["status"] == "signed" and doc["document_number"]:
            healed_official_number = doc["document_number"]
            public_status = "signed"
            log.warning(
                "async-poll self-healing: sesión %s en '%s' con doc firmado (%s) "
                "— reconciliando a signed",
                session_id[:8], internal_status, healed_official_number,
            )
            try:
                await execute(
                    """
                    UPDATE public.signing_sessions
                    SET status     = 'signed',
                        updated_at = NOW(),
                        payload    = COALESCE(payload, '{}'::jsonb)
                                     || jsonb_build_object('official_number', $1::text)
                    WHERE session_id = $2::uuid
                      AND status IN ('pending', 'processing')
                    """,
                    healed_official_number,
                    session_id,
                    schema_name="public",
                )
            except Exception as reconcile_err:
                log.error(
                    "async-poll self-healing: no se pudo reconciliar sesión %s: %s",
                    session_id[:8], reconcile_err,
                )

    payload = row["payload"] or {}
    payload_dict: dict = payload if isinstance(payload, dict) else {}

    official_number: Optional[str] = None
    auto_link_results: list = []
    if public_status == "signed":
        official_number = healed_official_number or payload_dict.get("official_number")
        _raw_alr = payload_dict.get("auto_link_results", [])
        auto_link_results = _raw_alr if isinstance(_raw_alr, list) else []

    failure_reason: Optional[str] = row.get("failure_reason")

    return {
        "session_id": session_id,
        "status": public_status,
        "official_number": official_number,
        "auto_link_results": auto_link_results,
        "reason": failure_reason,
        "failure_reason": failure_reason,
    }
