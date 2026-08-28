import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from database import execute, fetch_one
from shared.logging import get_logger

logger = get_logger(__name__)

IDEMPOTENCY_TTL_HOURS = 24

IDEMPOTENCY_KEY_MAX_LENGTH = 255


class IdempotencyOutcome(str, Enum):
    PROCEED = "proceed"
    REPLAY = "replay"
    CONFLICT = "conflict"


@dataclass
class IdempotencyDecision:
    outcome: IdempotencyOutcome
    response: Optional[dict] = None
    message: Optional[str] = None


def fingerprint(body_bytes: bytes) -> str:
    return hashlib.sha256(body_bytes).hexdigest()


def validate_key(raw_key: Optional[str]) -> Optional[str]:
    if raw_key is None:
        return None
    key = raw_key.strip()
    if not key:
        raise ValueError("Idempotency-Key no puede estar vacio")
    if len(key) > IDEMPOTENCY_KEY_MAX_LENGTH:
        raise ValueError(
            f"Idempotency-Key no puede superar {IDEMPOTENCY_KEY_MAX_LENGTH} caracteres"
        )
    return key


async def resolve_api_key_id(api_key: str) -> Optional[str]:
    row = await fetch_one(
        "SELECT id FROM public.api_keys WHERE api_key_hash = $1",
        hashlib.sha256(api_key.encode()).hexdigest(),
        schema_name="public",
    )
    return str(row["id"]) if row else None


async def begin(
    *,
    api_key_id: str,
    key: str,
    schema_name: str,
    citizen_id: str,
    request_fingerprint: str,
) -> IdempotencyDecision:
    now = datetime.now(timezone.utc)

    await execute(
        """
        DELETE FROM public.tad_idempotency_keys
        WHERE api_key_id = $1::uuid AND expires_at < $2
        """,
        api_key_id, now,
        schema_name="public",
    )

    inserted = await fetch_one(
        """
        INSERT INTO public.tad_idempotency_keys
            (api_key_id, idempotency_key, schema_name, citizen_id,
             request_fingerprint, status, expires_at)
        VALUES ($1::uuid, $2, $3, $4::uuid, $5, 'in_flight', $6)
        ON CONFLICT (api_key_id, idempotency_key) DO NOTHING
        RETURNING idempotency_key
        """,
        api_key_id, key, schema_name, citizen_id, request_fingerprint,
        now + timedelta(hours=IDEMPOTENCY_TTL_HOURS),
        schema_name="public",
    )
    if inserted:
        return IdempotencyDecision(IdempotencyOutcome.PROCEED)

    existing = await fetch_one(
        """
        SELECT status, request_fingerprint, response_json
        FROM public.tad_idempotency_keys
        WHERE api_key_id = $1::uuid AND idempotency_key = $2
        """,
        api_key_id, key,
        schema_name="public",
    )
    if not existing:
        logger.warning(
            "[TAD Idempotency] key=%s... desaparecio entre INSERT y SELECT, se procesa",
            key[:12],
        )
        return IdempotencyDecision(IdempotencyOutcome.PROCEED)

    if existing["request_fingerprint"] != request_fingerprint:
        logger.warning(
            "[TAD Idempotency] key=%s... reusada con otro cuerpo (schema=%s)",
            key[:12], schema_name,
        )
        return IdempotencyDecision(
            IdempotencyOutcome.CONFLICT,
            message=(
                "El header 'Idempotency-Key' ya se uso para un documento con otro "
                "contenido. Cada solicitud distinta necesita su propia clave."
            ),
        )

    if existing["status"] == "in_flight":
        return IdempotencyDecision(
            IdempotencyOutcome.CONFLICT,
            message=(
                "Ya hay una solicitud en curso con este 'Idempotency-Key'. "
                "Reintentar en unos segundos: el resultado llega por webhook."
            ),
        )

    response = existing["response_json"]
    if isinstance(response, str):
        response = json.loads(response)
    return IdempotencyDecision(IdempotencyOutcome.REPLAY, response=response)


async def complete(
    *, api_key_id: str, key: str, document_id: str, response: dict[str, Any]
) -> None:
    try:
        await execute(
            """
            UPDATE public.tad_idempotency_keys
               SET status = 'completed',
                   document_id = $3::uuid,
                   response_json = $4,
                   completed_at = NOW()
             WHERE api_key_id = $1::uuid AND idempotency_key = $2
            """,
            api_key_id, key, document_id, response,
            schema_name="public",
        )
    except Exception as exc:  # noqa: BLE001 -- ver docstring
        logger.error(
            "[TAD Idempotency] no se pudo completar key=%s... doc=%s: %s — "
            "un reintento con esta key va a crear un documento nuevo",
            key[:12], document_id[:8], exc,
        )


async def release(*, api_key_id: str, key: str) -> None:
    try:
        await execute(
            """
            DELETE FROM public.tad_idempotency_keys
            WHERE api_key_id = $1::uuid AND idempotency_key = $2
              AND status = 'in_flight'
            """,
            api_key_id, key,
            schema_name="public",
        )
    except Exception as exc:  # noqa: BLE001 -- mismo criterio que complete()
        logger.error(
            "[TAD Idempotency] no se pudo liberar key=%s...: %s — queda tomada "
            "hasta que venza (%sh)",
            key[:12], exc, IDEMPOTENCY_TTL_HOURS,
        )
