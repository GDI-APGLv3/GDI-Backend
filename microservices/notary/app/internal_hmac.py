import base64
import hashlib
import hmac
import logging
import time

from fastapi import HTTPException, Request

from .config import NOTARY_INTERNAL_HMAC_SECRET, HMAC_ALLOW_LEGACY_FORMAT

logger = logging.getLogger(__name__)

_MAX_CLOCK_SKEW_SECONDS = 60

_warned_disabled = False


def _get_secret() -> bytes:
    return NOTARY_INTERNAL_HMAC_SECRET.encode("utf-8") if NOTARY_INTERNAL_HMAC_SECRET else b""


def _parse_header(header_value: str) -> dict[str, str]:
    parts: dict[str, str] = {}
    for chunk in header_value.split(","):
        if "=" not in chunk:
            return {}
        key, _, value = chunk.partition("=")
        parts[key.strip()] = value.strip()
    return parts


def _malformed(detail: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail=detail,
        headers={"error_code": "INVALID_INTERNAL_SIGN_FORMAT"},
    )


async def validate_internal_hmac(request: Request, body: bytes = b"") -> None:
    global _warned_disabled
    secret = _get_secret()

    if not secret:
        if not _warned_disabled:
            logger.warning(
                "NOTARY_INTERNAL_HMAC_SECRET no configurado: validación HMAC "
                "inter-servicio DESACTIVADA (soft-launch, solo válido en "
                "test/dev). Las requests pasan sin verificar X-Internal-Sign."
            )
            _warned_disabled = True
        return

    header_value = request.headers.get("X-Internal-Sign")
    if not header_value:
        raise HTTPException(
            status_code=401,
            detail="Missing X-Internal-Sign header",
            headers={"error_code": "MISSING_INTERNAL_SIGN"},
        )

    parts = _parse_header(header_value)
    if not parts:
        raise _malformed("Malformed X-Internal-Sign header")

    ts_str = parts.get("t")
    if not ts_str:
        raise _malformed("Malformed X-Internal-Sign header")

    try:
        ts = int(ts_str)
    except ValueError:
        raise _malformed("Malformed X-Internal-Sign timestamp")

    now = int(time.time())
    if abs(now - ts) > _MAX_CLOCK_SKEW_SECONDS:
        raise HTTPException(
            status_code=401,
            detail="X-Internal-Sign timestamp expired or out of range",
            headers={"error_code": "INTERNAL_SIGN_EXPIRED"},
        )

    path = request.url.path
    method = request.method

    if "v2" in parts:
        sig_b64 = parts["v2"]
        nonce = parts.get("n", "")
        try:
            received_digest = base64.b64decode(sig_b64, validate=True)
        except Exception:
            raise _malformed("Malformed X-Internal-Sign signature")

        body_sha256 = hashlib.sha256(body).hexdigest()
        payload = f"{ts}|{nonce}|{method}|{path}|{body_sha256}".encode("utf-8")
        expected_digest = hmac.new(secret, payload, hashlib.sha256).digest()

        if not hmac.compare_digest(expected_digest, received_digest):
            raise HTTPException(
                status_code=401,
                detail="Invalid X-Internal-Sign signature",
                headers={"error_code": "INVALID_INTERNAL_SIGN"},
            )
        return

    if "v1" in parts:
        if not HMAC_ALLOW_LEGACY_FORMAT:
            raise HTTPException(
                status_code=401,
                detail="Legacy X-Internal-Sign format (v1) is disabled",
                headers={"error_code": "INTERNAL_SIGN_LEGACY_DISABLED"},
            )
        sig_b64 = parts["v1"]
        try:
            received_digest = base64.b64decode(sig_b64, validate=True)
        except Exception:
            raise _malformed("Malformed X-Internal-Sign signature")

        payload = f"{ts}|{method}|{path}".encode("utf-8")
        expected_digest = hmac.new(secret, payload, hashlib.sha256).digest()

        if not hmac.compare_digest(expected_digest, received_digest):
            raise HTTPException(
                status_code=401,
                detail="Invalid X-Internal-Sign signature",
                headers={"error_code": "INVALID_INTERNAL_SIGN"},
            )
        logger.warning(
            "notary.hmac_legacy_format_used path=%s — emisor todavía no "
            "migró a v2 (nonce + sha256(body)); HMAC_ALLOW_LEGACY_FORMAT=true",
            path,
        )
        return

    raise _malformed("Malformed X-Internal-Sign header: missing v1/v2")
