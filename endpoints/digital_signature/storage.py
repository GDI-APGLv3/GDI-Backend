"""
POST /digital-signature/storage — compatible con protocolo AutoFirma (@firma).
Endpoint PUBLICO: AutoFirma no envia JWT.

op=put: AutoFirma sube el XML con el PDF o la firma resultante (o "CANCEL")
op=get: AutoFirma descarga el XML envoltorio para obtener el PDF a firmar
"""
import base64
import json
import logging
from urllib.parse import unquote_plus

from fastapi import APIRouter, Request, Response, HTTPException

from services.cache import redis_client

log = logging.getLogger(__name__)
router = APIRouter()

REDIS_KEY_PREFIX = "firma:storage"
TTL_SECONDS = 240


def _redis_scan_key(redis_client, pattern: str) -> str | None:
    """Busca la primera key que coincide con pattern usando SCAN (O(1) por iteracion)."""
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


def _resolve_schema_for_id(item_id: str) -> str | None:
    """Busca schema_name para un file_id o session_id via Redis meta."""
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

    # Validar alfanumerico (requisito AutoFirma)
    if not item_id.isalnum():
        raise HTTPException(status_code=400, detail="id_must_be_alphanumeric")

    schema_name = _resolve_schema_for_id(item_id)
    if not schema_name:
        # Fallback: intentar con scan mas amplio si item_id es file_id (DATA*)
        if redis_client:
            found_key = _redis_scan_key(redis_client, f"firma:storage:*:{item_id}")
            if found_key:
                parts = found_key.split(":")
                schema_name = parts[2] if len(parts) > 2 else None

    if not schema_name:
        log.warning(f"storage.unknown_session item_id={item_id} op={op} — rechazado")
        raise HTTPException(status_code=404, detail="session_not_found")

    key = f"{REDIS_KEY_PREFIX}:{schema_name}:{item_id}"

    if op == "put":
        dat = form.get("dat", "")
        if redis_client:
            redis_client.setex(key, TTL_SECONDS, dat)
        log.info(f"storage.put item_id={item_id} len={len(dat)}")
        return Response("OK", media_type="text/plain")

    if op == "get":
        if not redis_client:
            return Response("", media_type="text/plain")
        value = redis_client.get(key)
        if value is None or value == "":
            return Response("", media_type="text/plain")
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        # XML → base64 encoded (AutoFirma lo espera asi)
        if value.startswith("<?xml") or value.startswith("<"):
            b64 = base64.b64encode(value.encode("utf-8")).decode("ascii")
            return Response(b64, media_type="text/plain; charset=utf-8")
        # Firma o CANCEL → tal cual
        return Response(value, media_type="text/plain; charset=utf-8")

    raise HTTPException(status_code=400, detail=f"invalid_op:{op}")
