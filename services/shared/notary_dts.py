
import base64
import os

import httpx

from shared.logging import get_logger
from shared.exceptions import (
    NotaryBusinessError,
    NotaryHashMismatchError,
    NotaryTimeoutError,
    NotaryUnavailableError,
)

logger = get_logger(__name__)

_NOTARY_URL = os.getenv("NOTARY_URL", "")
_NOTARY_API_KEY = os.getenv("NOTARY_API_KEY", "")
_NOTARY_TIMEOUT = float(os.getenv("NOTARY_TIMEOUT_DTS", "30"))


async def call_notary_timestamp_pdf(
    pdf_bytes: bytes,
    *,
    schema_name: str,
    expected_sha256: str | None = None,
) -> bytes:
    from services.shared.notary_breaker import (
        check_breaker_before_call,
        record_notary_failure,
        record_notary_success,
    )
    await check_breaker_before_call()

    logger.info(
        "notary_dts.timestamp_pdf schema=%s size=%d bytes",
        schema_name, len(pdf_bytes),
    )

    files = {
        "pdf_file": ("document.pdf", pdf_bytes, "application/pdf"),
    }
    if expected_sha256:
        files["expected_sha256"] = (None, expected_sha256)
    headers = {"x-api-key": _NOTARY_API_KEY}
    try:
        from services.notary_internal_hmac import build_internal_hmac_header
        hmac_value = build_internal_hmac_header(method="POST", path="/timestamp-pdf", body=pdf_bytes)
        if hmac_value:
            headers["X-Internal-Sign"] = hmac_value
    except Exception as _hmac_err:
        logger.warning(f"No se pudo generar X-Internal-Sign para /timestamp-pdf (soft-fail): {_hmac_err}")

    try:
        async with httpx.AsyncClient(timeout=_NOTARY_TIMEOUT) as client:
            response = await client.post(
                f"{_NOTARY_URL}/timestamp-pdf",
                files=files,
                headers=headers,
            )
    except httpx.TimeoutException as exc:
        err = NotaryTimeoutError(
            f"Timeout /timestamp-pdf (>{_NOTARY_TIMEOUT}s): {exc}"
        )
        logger.error("notary_dts.timeout schema=%s: %s", schema_name, err)
        await record_notary_failure(err)
        raise err
    except httpx.RequestError as exc:
        err = NotaryUnavailableError(f"Conexión /timestamp-pdf: {exc}")
        logger.error("notary_dts.conn_error schema=%s: %s", schema_name, err)
        await record_notary_failure(err)
        raise err

    if response.status_code == 200:
        await record_notary_success()
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("application/json"):
            data = response.json()
            if "timestamped_pdf_b64" in data:
                return base64.b64decode(data["timestamped_pdf_b64"])
            return response.content
        return response.content

    if response.status_code == 400:
        body_text = response.text[:400]
        if "UNSIGNED_PDF" in body_text:
            raise NotaryBusinessError(
                f"Notary /timestamp-pdf: PDF sin firma electrónica (UNSIGNED_PDF) — "
                f"schema={schema_name}. {body_text}"
            )
        raise NotaryBusinessError(
            f"Notary /timestamp-pdf 400: {body_text}"
        )

    if response.status_code == 409:
        body_text = response.text[:400]
        if "PDF_HASH_MISMATCH" in body_text:
            raise NotaryHashMismatchError(
                f"Notary /timestamp-pdf: hash mismatch — el PDF en R2 fue "
                f"sobreescrito o corrompido. schema={schema_name}. {body_text}"
            )
        raise NotaryBusinessError(
            f"Notary /timestamp-pdf 409: {body_text}"
        )

    body_text = response.text[:400]
    err = NotaryUnavailableError(
        f"Notary /timestamp-pdf {response.status_code}: {body_text}"
    )
    logger.error("notary_dts.server_error schema=%s: %s", schema_name, err)
    await record_notary_failure(err)
    raise err
