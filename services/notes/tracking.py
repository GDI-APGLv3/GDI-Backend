
from typing import Dict, Any
from shared.logging import get_logger
from shared.exceptions import NotFoundError, AuthorizationError
from database import fetch_all, fetch_one, get_conn
from .queries import (
    insert_note_opening_query,
    get_opening_by_document_user_query,
    get_openings_by_document_query,
    get_note_detail_query,
    check_user_is_recipient_query,
    check_user_is_sender_query,
    get_note_recipient_info_query,
)
from .recipients import get_visible_recipients, check_sector_access

logger = get_logger(__name__)


async def record_opening(
    document_id: str,
    user_id: str,
    sector_id: str,
    *,
    schema_name: str,
) -> Dict[str, Any]:
    async with get_conn(schema_name=schema_name) as conn:
        recipient_row = await conn.fetchrow(check_user_is_recipient_query(), document_id, sector_id)

        if not recipient_row:
            is_sender_row = await conn.fetchrow(check_user_is_sender_query(), document_id, sector_id)
            if is_sender_row and is_sender_row["is_sender"]:
                logger.debug(f"[{schema_name}] Sender {sector_id} abrió nota {document_id}, no se registra")
                return {"recorded": False, "opened_at": None, "reason": "sender"}
            raise AuthorizationError("No tienes acceso a esta nota")

        result = await conn.fetchrow(insert_note_opening_query(), document_id, sector_id, user_id)

        opening = await conn.fetchrow(get_opening_by_document_user_query(), document_id, user_id)

    if result:
        logger.info(f"[{schema_name}] Usuario {user_id} abrió nota {document_id} por primera vez")
    else:
        logger.debug(f"[{schema_name}] Usuario {user_id} reabrió nota {document_id}")

    return {
        "recorded": bool(result),
        "opened_at": opening["opened_at"].isoformat() if opening else None,
    }


async def get_note_detail(
    document_id: str,
    requesting_user_id: str,
    requesting_sector_id: str,
    *,
    register_opening: bool = True,
    schema_name: str,
) -> Dict[str, Any]:
    note = await fetch_one(get_note_detail_query(), document_id, schema_name=schema_name)
    if not note:
        raise NotFoundError(f"Nota {document_id} no encontrada")

    recipients = await get_visible_recipients(document_id, requesting_sector_id, schema_name=schema_name)

    if register_opening:
        opening_result = await record_opening(
            document_id, requesting_user_id, requesting_sector_id, schema_name=schema_name
        )
    else:
        opening_result = {"recorded": False, "opened_at": None, "reason": "view_only"}

    openings = None
    if recipients["is_sender"]:
        openings_raw = await fetch_all(
            get_openings_by_document_query(), document_id, schema_name=schema_name
        )
        openings = [
            {
                "sector_id": str(o["sector_id"]),
                "sector_acronym": o["sector_acronym"],
                "sector_color": o.get("sector_color"),
                "user_id": str(o["user_id"]),
                "user_name": o["user_name"],
                "profile_picture_url": o.get("profile_picture_url"),
                "seal_name": o.get("seal_name"),
                "opened_at": o["opened_at"].isoformat() if o["opened_at"] else None,
            }
            for o in openings_raw
        ]

    is_archived = False
    archived_at = None
    if not recipients["is_sender"]:
        recipient_info = await fetch_one(
            get_note_recipient_info_query(), document_id, requesting_sector_id, schema_name=schema_name
        )
        if recipient_info:
            is_archived = recipient_info["is_archived"]
            archived_at = (
                recipient_info["archived_at"].isoformat() if recipient_info["archived_at"] else None
            )

    result = {
        "document_id": str(note["id"]),
        "official_number": note["official_number"],
        "reference": note["reference"],
        "content": note["content"],
        "signed_at": note["signed_at"].isoformat() if note["signed_at"] else None,
        "ai_summary": note["ai_summary"],
        "signers": note["signers"],
        "document_type": {
            "name": note["document_type_name"],
            "acronym": note["document_type_acronym"],
        },
        "department_name": note["department_name"],
        "recipients": recipients,
        "my_access": {
            "is_sender": recipients["is_sender"],
            "recipient_type": recipients["my_recipient_type"],
            "sector_id": requesting_sector_id,
            "first_open": opening_result["recorded"] if opening_result else False,
            "opened_at": opening_result["opened_at"] if opening_result else None,
            "is_archived": is_archived,
            "archived_at": archived_at,
        },
    }

    if openings is not None:
        result["openings"] = openings

    try:
        from services.documents.lifecycle.editing import _fetch_proposed_cases
        proposed_cases = await _fetch_proposed_cases(document_id, schema_name=schema_name)
        if proposed_cases:
            result["proposed_cases"] = proposed_cases
    except Exception as e:
        logger.warning(f"[{schema_name}] Error al obtener proposed_cases para nota {document_id}: {e}")

    logger.info(
        f"[{schema_name}] Nota {document_id} accedida por sector {requesting_sector_id} "
        f"(is_sender={recipients['is_sender']}, type={recipients['my_recipient_type']})"
    )
    return result


async def get_note_detail_multi_sector(
    document_id: str,
    requesting_user_id: str,
    user_permissions: list[dict],
    *,
    schema_name: str,
) -> Dict[str, Any]:
    viewable_permissions = [p for p in user_permissions if p.get("can_view")]
    if not viewable_permissions:
        raise AuthorizationError("No tienes sectores con permiso de visualización.")

    access_sector_id = None
    can_register_opening = False

    for perm in viewable_permissions:
        sector_id = perm["sector_id"]
        access = await check_sector_access(document_id, sector_id, schema_name=schema_name)

        if access["has_access"]:
            access_sector_id = sector_id
            can_register_opening = perm.get("can_view", False)
            logger.debug(
                f"[{schema_name}] Acceso encontrado para nota {document_id} via sector {sector_id} "
                f"(can_view={can_register_opening})"
            )
            break

    if not access_sector_id:
        raise AuthorizationError(
            "No tienes acceso a esta nota. Solo el emisor y los destinatarios pueden verla."
        )

    return await get_note_detail(
        document_id=document_id,
        requesting_user_id=requesting_user_id,
        requesting_sector_id=access_sector_id,
        register_opening=can_register_opening,
        schema_name=schema_name,
    )
