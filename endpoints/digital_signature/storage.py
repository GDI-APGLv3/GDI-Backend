import base64
import json
import re
from shared.logging import get_logger
from urllib.parse import unquote_plus

from fastapi import APIRouter, Request, Response, HTTPException

from services.cache import redis_client
from shared.ip_rate_limit import (
    IpRateLimitExceeded,
    check_ip_rate_limit,
    get_client_ip,
)

log = get_logger(__name__)
router = APIRouter()

REDIS_KEY_PREFIX = "firma:storage"
from config.constants import (
    DIGITAL_SIGNATURE_SESSION_TTL_SECONDS as TTL_SECONDS,
    DIGITAL_SIGNATURE_STORAGE_MAX_PER_MINUTE,
    DIGITAL_SIGNATURE_STORAGE_MAX_MISSES_PER_MINUTE,
)


def clave_firmador_visto(schema_name: str, manifest_id: str) -> str:
    return f"firma:firmador_visto:{schema_name}:{manifest_id}"


def _redis_scan_key(redis_client, pattern: str) -> str | None:
    cursor = 0
    while True:
        cursor, keys = redis_client.scan(cursor, match=pattern, count=100)
        if keys:
            return keys[0].decode() if isinstance(keys[0], bytes) else keys[0]
        if cursor == 0:
            break
    return None


def _parse_urlencoded(body: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in body.decode("utf-8", errors="replace").split("&"):
        idx = pair.find("=")
        if idx < 0:
            continue
        out[unquote_plus(pair[:idx])] = unquote_plus(pair[idx + 1:])
    return out


HEADER_VERSION = "X-FirmadorGDI-Version"

_VERSION_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}$")


async def _registrar_version_del_firmador(request, item_id: str) -> None:
    version = (request.headers.get(HEADER_VERSION) or "").strip()
    if not version or not _VERSION_RE.match(version):
        return

    try:
        from database import execute

        await execute(
            """
            UPDATE public.digital_signature_sessions
            SET client_version = $1
            WHERE (session_id = $2 OR file_id = $2)
              AND client_version IS DISTINCT FROM $1
            """,
            version, item_id,
            schema_name="public",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("storage.version_no_registrada item=%s: %s", item_id[:12], exc)


def _resolve_schema_for_id(item_id: str) -> str | None:
    if not redis_client:
        return None
    try:
        key = _redis_scan_key(redis_client, f"firma:storage:meta:*:{item_id}")
        if key:
            data = redis_client.get(key)
            if data:
                parsed = json.loads(data)
                return parsed.get("schema_name")
    except Exception as e:
        log.warning(f"storage._resolve_schema error: {e}")
    return None


@router.post("/digital-signature/storage")
async def storage_handler(request: Request) -> Response:
    body = await request.body()
    form = _parse_urlencoded(body)

    op = form.get("op")
    item_id = form.get("id", "")
    if not op or not item_id:
        raise HTTPException(status_code=400, detail="missing_op_or_id")

    if not item_id.isalnum():
        raise HTTPException(status_code=400, detail="id_must_be_alphanumeric")

    client_ip = get_client_ip(request)
    try:
        check_ip_rate_limit(
            client_ip,
            bucket_name="storage",
            limit=DIGITAL_SIGNATURE_STORAGE_MAX_PER_MINUTE,
        )
    except IpRateLimitExceeded as e:
        log.warning("storage.rate_limited ip=%s op=%s", client_ip, op)
        raise HTTPException(
            status_code=429,
            detail="rate_limit_exceeded",
            headers={"Retry-After": str(e.retry_after)},
        )

    schema_name = _resolve_schema_for_id(item_id)
    if not schema_name:
        if redis_client:
            found_key = _redis_scan_key(redis_client, f"firma:storage:*:{item_id}")
            if found_key:
                parts = found_key.split(":")
                schema_name = parts[2] if len(parts) > 2 else None

    if not schema_name:
        log.warning(
            "storage.unknown_session ip=%s item_id=%s... op=%s — rechazado",
            client_ip, item_id[:12], op,
        )
        try:
            check_ip_rate_limit(
                client_ip,
                bucket_name="storage_miss",
                limit=DIGITAL_SIGNATURE_STORAGE_MAX_MISSES_PER_MINUTE,
            )
        except IpRateLimitExceeded as e:
            log.warning("storage.miss_rate_limited ip=%s — tanteo de ids", client_ip)
            raise HTTPException(
                status_code=429,
                detail="rate_limit_exceeded",
                headers={"Retry-After": str(e.retry_after)},
            )
        raise HTTPException(status_code=404, detail="session_not_found")

    key = f"{REDIS_KEY_PREFIX}:{schema_name}:{item_id}"

    await _registrar_version_del_firmador(request, item_id)

    if op == "put":
        dat = form.get("dat", "")
        if redis_client:
            redis_client.setex(key, TTL_SECONDS, dat)
        log.info(f"storage.put item_id={item_id[:12]}... len={len(dat)}")
        return Response("OK", media_type="text/plain")

    if op == "get":
        if not redis_client:
            return Response("", media_type="text/plain")

        if item_id.startswith("MAN"):
            try:
                redis_client.setex(
                    clave_firmador_visto(schema_name, item_id), TTL_SECONDS, "1"
                )
            except Exception as exc:
                log.warning("storage.firmador_visto soft-fail item=%s: %s", item_id[:12], exc)

        value = redis_client.get(key)
        if value is None or value == "":
            return Response("", media_type="text/plain")
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        if value.startswith("<?xml") or value.startswith("<"):
            b64 = base64.b64encode(value.encode("utf-8")).decode("ascii")
            return Response(b64, media_type="text/plain; charset=utf-8")
        return Response(value, media_type="text/plain; charset=utf-8")

    raise HTTPException(status_code=400, detail=f"invalid_op:{op}")
