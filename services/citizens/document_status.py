from typing import Any, Optional

from starlette.concurrency import run_in_threadpool

from database import fetch_one
from shared.logging import get_logger

logger = get_logger(__name__)

STATUS_QUEUED = "queued"
STATUS_SIGNED = "signed"
STATUS_FAILED = "failed"


async def get_citizen_document_status(
    document_id: str, citizen_id: str, *, schema_name: str
) -> Optional[dict[str, Any]]:
    draft = await fetch_one(
        """
        SELECT dd.id, dd.status, dd.reference, dd.created_at, dd.created_by_citizen,
               dt.acronym AS document_type_acronym
        FROM document_draft dd
        LEFT JOIN document_types dt ON dd.document_type_id = dt.id
        WHERE dd.id = $1::uuid AND dd.is_deleted = false
        """,
        document_id,
        schema_name=schema_name,
    )
    if not draft or str(draft["created_by_citizen"] or "") != str(citizen_id):
        return None

    resultado: dict[str, Any] = {
        "document_id": str(draft["id"]),
        "reference": draft["reference"],
        "document_type_acronym": draft["document_type_acronym"],
        "created_at": _iso(draft["created_at"]),
        "official_number": None,
        "pdf_url": None,
        "signed_at": None,
        "failure_reason": None,
    }

    oficial = await fetch_one(
        """
        SELECT official_number, signed_at, pdf_location
        FROM official_documents
        WHERE id = $1::uuid AND signed_at IS NOT NULL
        """,
        document_id,
        schema_name=schema_name,
    )
    if oficial:
        resultado.update({
            "status": STATUS_SIGNED,
            "official_number": oficial["official_number"],
            "signed_at": _iso(oficial["signed_at"]),
            "pdf_url": await _pdf_url(
                oficial["official_number"], oficial["pdf_location"], schema_name=schema_name,
            ),
        })
        return resultado

    sesion = await fetch_one(
        """
        SELECT session_id, status, failure_reason, expires_at
        FROM public.signing_sessions
        WHERE schema_name = $1 AND document_id = $2::uuid
          AND job_type = 'sign_citizen'
        ORDER BY created_at DESC
        LIMIT 1
        """,
        schema_name, document_id,
        schema_name="public",
    )
    if sesion:
        en_curso = sesion["status"] in ("pending", "processing")
        resultado.update({
            "status": STATUS_QUEUED if en_curso else (
                STATUS_SIGNED if sesion["status"] == "signed" else STATUS_FAILED
            ),
            "session_id": str(sesion["session_id"]),
            "expires_at": _iso(sesion["expires_at"]),
        })
        if not en_curso and sesion["status"] != "signed":
            resultado["failure_reason"] = sesion["failure_reason"]
        elif sesion["status"] == "signed":
            resultado["status"] = STATUS_QUEUED
            logger.info(
                "[TAD DocStatus] sesion signed sin oficial todavia doc=%s schema=%s",
                document_id[:8], schema_name,
            )
        return resultado

    resultado.update({
        "status": STATUS_FAILED,
        "failure_reason": "signing_never_enqueued",
    })
    return resultado


async def _pdf_url(
    official_number: Optional[str], pdf_location: Optional[str], *, schema_name: str
) -> Optional[str]:
    if not official_number:
        return None
    try:
        from services.storage.cloudflare import get_tenant_r2_client

        r2 = await get_tenant_r2_client(schema_name=schema_name)
        return await run_in_threadpool(
            r2.get_oficial_url, official_number, pdf_location or "oficial",
        )
    except Exception as exc:  # noqa: BLE001 -- ver docstring
        logger.warning(
            "[TAD DocStatus] no se pudo firmar la URL de %s (schema=%s): %s",
            official_number, schema_name, exc,
        )
        return None


def _iso(value) -> Optional[str]:
    return value.isoformat() if value else None
