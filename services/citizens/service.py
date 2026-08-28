import uuid
from typing import Optional

from shared.logging import get_logger
from shared.exceptions import ValidationError
from database import get_conn, fetch_one
from services.citizens.queries import (
    upsert_citizen_query,
    get_citizen_by_id_query,
    get_citizen_by_country_id_query,
    update_citizen_estado_query,
)

logger = get_logger(__name__)

ESTADOS_VALIDOS = frozenset({"pendiente", "validado", "bloqueado"})
AUTH_SOURCE_TAD = "tad"


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


async def upsert_citizen(
    full_name: str,
    country_id: str,
    estado: str = "pendiente",
    *,
    schema_name: str,
) -> dict:
    full_name = (full_name or "").strip()
    country_id = (country_id or "").strip()
    estado = (estado or "pendiente").strip()

    import nh3
    full_name = nh3.clean(full_name, tags=set(), attributes={}).strip()

    if not full_name:
        raise ValidationError("full_name es requerido")
    if not country_id:
        raise ValidationError("country_id es requerido")
    if estado not in ESTADOS_VALIDOS:
        raise ValidationError(f"estado invalido: {estado!r} (validos: {sorted(ESTADOS_VALIDOS)})")

    async with get_conn(schema_name=schema_name, auth_source=AUTH_SOURCE_TAD) as conn:
        row = await conn.fetchrow(upsert_citizen_query(), full_name, country_id, estado)

    logger.info(f"[Citizens] upsert country_id={country_id!r} estado={estado!r} schema={schema_name}")
    return dict(row)


async def get_citizen(id_or_country_id: str, *, schema_name: str) -> Optional[dict]:
    ref = (id_or_country_id or "").strip()
    if not ref:
        return None

    if _is_uuid(ref):
        row = await fetch_one(get_citizen_by_id_query(), ref, schema_name=schema_name)
    else:
        row = await fetch_one(get_citizen_by_country_id_query(), ref, schema_name=schema_name)

    return dict(row) if row else None


async def set_citizen_estado(citizen_id: str, estado: str, *, schema_name: str) -> Optional[dict]:
    if not _is_uuid(citizen_id):
        raise ValidationError("citizen_id debe ser un UUID valido")
    estado = (estado or "").strip()
    if estado not in ESTADOS_VALIDOS:
        raise ValidationError(f"estado invalido: {estado!r} (validos: {sorted(ESTADOS_VALIDOS)})")

    async with get_conn(schema_name=schema_name, auth_source=AUTH_SOURCE_TAD) as conn:
        row = await conn.fetchrow(update_citizen_estado_query(), citizen_id, estado, "api")

    if row:
        logger.info(f"[Citizens] set_estado id={citizen_id} estado={estado!r} schema={schema_name}")
    return dict(row) if row else None
