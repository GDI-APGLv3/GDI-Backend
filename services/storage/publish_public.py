
import asyncio
import time
from threading import Lock
from typing import Optional

from shared.logging import get_logger
from services.r2_client import r2_copy, r2_put, R2KeyNotFound

log = get_logger(__name__)

_visibility_cache: dict[tuple[str, int], tuple[bool, float]] = {}
_cache_lock = Lock()
_CACHE_TTL_SECONDS = 300


async def _get_document_type_visibility(*, schema_name: str, document_type_id: int) -> bool:
    from database import fetch_one

    cache_key = (schema_name, document_type_id)
    now = time.time()

    with _cache_lock:
        cached = _visibility_cache.get(cache_key)
        if cached is not None and (now - cached[1]) < _CACHE_TTL_SECONDS:
            return cached[0]

    row = await fetch_one(
        "SELECT visibility FROM document_types WHERE id = $1",
        document_type_id,
        schema_name=schema_name,
    )
    is_public = bool(row and row.get("visibility") == "publico")

    with _cache_lock:
        _visibility_cache[cache_key] = (is_public, now)

    return is_public


async def _resolve_document_type_id(
    *, schema_name: str, document_id: str
) -> tuple[Optional[int], str]:
    from database import fetch_one

    row = await fetch_one(
        """
        SELECT d.document_type_id, o.pdf_location
        FROM official_documents o
        LEFT JOIN document_draft d ON d.id = o.id
        WHERE o.id = $1
        """,
        document_id,
        schema_name=schema_name,
    )
    type_id = row["document_type_id"] if row and row.get("document_type_id") is not None else None
    pdf_location = (row.get("pdf_location") if row else None) or "oficial"
    return type_id, pdf_location


async def maybe_publish_official_pdf(
    *,
    schema_name: str,
    official_number: str,
    document_id: Optional[str] = None,
    document_type_id: Optional[int] = None,
    signed_pdf_bytes: Optional[bytes] = None,
) -> bool:
    try:
        if not document_id:
            log.warning(
                "publish_public.skip_no_document_id schema=%s num=%s — "
                "GDI-134 requiere document_id (key pública = {uuid}.pdf)",
                schema_name, official_number,
            )
            return True

        resolved_type_id, resolved_pdf_location = await _resolve_document_type_id(
            schema_name=schema_name, document_id=document_id
        )
        if resolved_type_id is None:
            resolved_type_id = document_type_id
        if resolved_type_id is None:
            log.warning(
                "publish_public.type_not_found schema=%s num=%s doc=%s",
                schema_name, official_number, document_id,
            )
            return True

        is_public = await _get_document_type_visibility(
            schema_name=schema_name, document_type_id=resolved_type_id
        )
        if not is_public:
            return True

        src_key = f"{official_number}.pdf"
        dst_key = f"{document_id}.pdf"
        return await _copy_with_retry(
            schema_name=schema_name,
            src_key=src_key,
            dst_key=dst_key,
            official_number=official_number,
            signed_pdf_bytes=signed_pdf_bytes,
            src_bucket=resolved_pdf_location,
        )
    except Exception as e:
        log.error(
            "publish_public.failed schema=%s num=%s error=%s",
            schema_name, official_number, e,
        )
        return False


async def _copy_with_retry(
    *,
    schema_name: str,
    src_key: str,
    dst_key: str,
    official_number: str,
    signed_pdf_bytes: Optional[bytes],
    src_bucket: str = "oficial",
) -> bool:
    backoffs = (0.5, 1.0)
    for attempt in range(3):
        try:
            await r2_copy(
                schema_name=schema_name,
                src=src_key,
                dst=dst_key,
                src_bucket=src_bucket,
                dst_bucket="publico",
            )
            log.info(
                "publish_public.ok schema=%s num=%s bucket=publico key=%s",
                schema_name, official_number, dst_key,
            )
            return True
        except (R2KeyNotFound, ValueError):
            if signed_pdf_bytes:
                await r2_put(
                    schema_name=schema_name,
                    key=dst_key,
                    body=signed_pdf_bytes,
                    content_type="application/pdf",
                    bucket="publico",
                )
                log.info(
                    "publish_public.ok_fallback_put schema=%s num=%s key=%s",
                    schema_name, official_number, dst_key,
                )
                return True
            if attempt == 2:
                log.error(
                    "publish_public.failed_key_not_found schema=%s num=%s key=%s",
                    schema_name, official_number, dst_key,
                )
                return False
        except Exception as e:
            if attempt == 2:
                log.error(
                    "publish_public.failed schema=%s num=%s error=%s",
                    schema_name, official_number, e,
                )
                return False
        if attempt < 2:
            await asyncio.sleep(backoffs[attempt])
    return False
