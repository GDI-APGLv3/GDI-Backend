import base64
import hashlib
import hmac
import time

REPLAY_TOLERANCE_SECONDS = 300


def build_webhook_hmac_header(secret: str, *, method: str, path: str, body_bytes: bytes) -> str:
    ts = int(time.time())
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    payload = f"{ts}|{method}|{path}|{body_hash}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    sig_b64 = base64.b64encode(digest).decode("ascii")
    return f"t={ts},v1={sig_b64}"
