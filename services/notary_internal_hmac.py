import base64
import hashlib
import hmac
import secrets
from shared.logging import get_logger
import os
import time

log = get_logger(__name__)

_SECRET_STR = os.environ.get("NOTARY_INTERNAL_HMAC_SECRET", "")
_SECRET = _SECRET_STR.encode("utf-8") if _SECRET_STR else b""

if not _SECRET and os.getenv("FLY_APP_NAME"):
    log.error(
        "NOTARY_INTERNAL_HMAC_SECRET no configurado en Fly.io. "
        "Las llamadas a Notary irán SIN firma HMAC. Setear con: "
        "flyctl secrets set NOTARY_INTERNAL_HMAC_SECRET=<valor> -a <app>"
    )


def build_internal_hmac_header(*, method: str, path: str, body: bytes = b"") -> str:
    if not _SECRET:
        log.debug("NOTARY_INTERNAL_HMAC_SECRET no configurado, omitiendo X-Internal-Sign")
        return ""

    ts = int(time.time())
    nonce = secrets.token_hex(8)
    body_sha256 = hashlib.sha256(body).hexdigest()
    payload = f"{ts}|{nonce}|{method}|{path}|{body_sha256}".encode("utf-8")
    digest = hmac.new(_SECRET, payload, hashlib.sha256).digest()
    sig_b64 = base64.b64encode(digest).decode("ascii")
    header_value = f"t={ts},n={nonce},v2={sig_b64}"
    log.debug("X-Internal-Sign (v2) generado: t=%d, path=%s", ts, path)
    return header_value
