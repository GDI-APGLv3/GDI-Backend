
import asyncio
import base64
from datetime import datetime
from typing import Any, Dict, List, Optional

from database import fetch_all, fetch_one, execute, transaction, get_conn
from shared.logging import get_logger
from shared.exceptions import ValidationError, NotFoundError
from services.shared.sector_utils import get_user_sector_ids
from services.shared.viewable_cases import get_viewable_cases_cte
from services.documents.retrieval.pending_signatures import (
    get_pending_signatures_for_user,
    count_pending_signatures_for_user,
    _PENDING_SIGNATURES_COUNT_QUERY,
)
from services.shared.user_queries import get_user_sectors_query
from services.cases.responsibles import (
    MOVEMENT_TYPE_RESPONSIBLE_ADD,  # noqa: F401 (documentación del contrato de 'type')
)
from services.home.queries import (
    get_unread_memo_items_query,
    get_unread_memo_count_query,
    get_unread_notes_for_sectors_query,
    get_unread_notes_count_query,
    get_responsible_notifications_query,
    get_mention_notifications_query,
    get_failed_signature_notifications_query,
    get_case_movements_grouped_query,
    get_unassigned_unowned_query,
    get_unassigned_unowned_count_query,
    get_unassigned_tasks_query,
    get_unassigned_tasks_count_query,
    upsert_case_user_view_query,
    dismiss_case_notifications_on_view_query,
    insert_notification_dismissal_query,
    get_case_exists_query,
)
from schemas.home_schemas import (
    build_sign_href,
    build_memo_href,
    build_note_href,
    build_case_href,
)

logger = get_logger(__name__)

_DISMISS_KEY_PREFIXES = (
    "responsible:",
    "mention:",
    "signature_failed:",
    "seen:signature_failed:",
)


def _encode_cursor(last_move_at, case_id: str) -> str:
    raw = f"{last_move_at.isoformat()}|{case_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def _decode_cursor(cursor: str) -> Optional[tuple]:
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        last_move_at_str, case_id = raw.split("|", 1)
        return datetime.fromisoformat(last_move_at_str), case_id
    except Exception:
        raise ValidationError("cursor inválido")


async def _get_sign_items(
    user_id: str, limit: int, *, schema_name: str, conn=None,
) -> List[Dict[str, Any]]:
    result = await get_pending_signatures_for_user(user_id, schema_name=schema_name, limit=limit, conn=conn)
    items = []
    for row in result["pending_signatures"]:
        items.append({
            "key": f"sign:{row['document_id']}",
            "document_id": row["document_id"],
            "reference": row["reference"],
            "document_number": row["document_number"],
            "document_type_acronym": row["document_type"]["acronym"],
            "document_type_name": row["document_type"]["name"],
            "signer_role": row["signer_role"],
            "sent_to_sign_at": row["sent_to_sign_at"],
            "short_ai_summary": row.get("short_resume"),
            "creator": {
                "name": row["creator"]["name"],
                "photo_url": row["creator"]["photo_url"],
                "sector_label": None,
            },
            "href": build_sign_href(row["document_id"]),
        })
    return items


async def _get_memo_items(
    user_id: str, limit: int, *, schema_name: str, conn=None,
) -> List[Dict[str, Any]]:
    if conn is not None:
        rows = await conn.fetch(get_unread_memo_items_query(), user_id, limit)
    else:
        rows = await fetch_all(get_unread_memo_items_query(), user_id, limit, schema_name=schema_name)
    items = []
    for row in rows:
        items.append({
            "key": f"memo:{row['document_id']}",
            "document_id": str(row["document_id"]),
            "official_number": row["official_number"],
            "reference": row["reference"],
            "ai_summary": row["ai_summary"],
            "short_ai_summary": row["short_ai_summary"],
            "signed_at": row["signed_at"],
            "creator": {
                "name": row["creator_name"],
                "photo_url": row["creator_photo"],
                "sector_label": None,
            },
            "href": build_memo_href(str(row["document_id"])),
        })
    return items


async def _get_note_items(
    user_id: str, limit: int, *, schema_name: str, conn=None,
) -> List[Dict[str, Any]]:
    if conn is not None:
        sector_rows = await conn.fetch(get_user_sectors_query(), user_id, user_id)
        sector_ids = [r["sector_id"] for r in sector_rows if r["sector_id"]]
    else:
        sector_ids = await get_user_sector_ids(user_id, schema_name=schema_name)
    if not sector_ids:
        return []
    if conn is not None:
        rows = await conn.fetch(get_unread_notes_for_sectors_query(), sector_ids, limit)
    else:
        rows = await fetch_all(get_unread_notes_for_sectors_query(), sector_ids, limit, schema_name=schema_name)
    items = []
    for row in rows:
        items.append({
            "key": f"note:{row['document_id']}",
            "document_id": str(row["document_id"]),
            "official_number": row["official_number"],
            "reference": row["reference"],
            "ai_summary": row["ai_summary"],
            "short_ai_summary": row["short_ai_summary"],
            "signed_at": row["signed_at"],
            "sender": {
                "name": None,
                "photo_url": None,
                "sector_label": f"{row['department_acronym']}#{row['sector_acronym']}",
            },
            "href": build_note_href(str(row["document_id"])),
        })
    return items


async def _count_memos(user_id: str, *, schema_name: str) -> int:
    row = await fetch_one(get_unread_memo_count_query(), user_id, schema_name=schema_name)
    return int(row["total"]) if row else 0


async def _count_notes(user_id: str, *, schema_name: str) -> int:
    sector_ids = await get_user_sector_ids(user_id, schema_name=schema_name)
    if not sector_ids:
        return 0
    row = await fetch_one(get_unread_notes_count_query(), sector_ids, schema_name=schema_name)
    return int(row["total"]) if row else 0


async def get_home_count(
    user_id: str, *, schema_name: str, _force_parallel_fetch: bool = False,
) -> Dict[str, Any]:
    if _force_parallel_fetch:
        sign_count, memo_count, note_count = await asyncio.gather(
            count_pending_signatures_for_user(user_id, schema_name=schema_name),
            _count_memos(user_id, schema_name=schema_name),
            _count_notes(user_id, schema_name=schema_name),
        )
    else:
        async with get_conn(schema_name=schema_name) as conn:
            sign_row = await conn.fetchrow(_PENDING_SIGNATURES_COUNT_QUERY, user_id)
            sign_count = int(sign_row["total"]) if sign_row else 0

            memo_row = await conn.fetchrow(get_unread_memo_count_query(), user_id)
            memo_count = int(memo_row["total"]) if memo_row else 0

            sector_rows = await conn.fetch(get_user_sectors_query(), user_id, user_id)
            sector_ids = [row["sector_id"] for row in sector_rows if row["sector_id"]]
            if sector_ids:
                note_row = await conn.fetchrow(get_unread_notes_count_query(), sector_ids)
                note_count = int(note_row["total"]) if note_row else 0
            else:
                note_count = 0

    by_source = {
        "sign": sign_count,
        "memo": memo_count,
        "note": note_count,
    }
    return {
        "actionable_total": sum(by_source.values()),
        "by_source": by_source,
    }


async def get_home_actionable(
    user_id: str, limit: int, *, schema_name: str, _force_parallel_fetch: bool = False,
) -> Dict[str, Any]:
    if _force_parallel_fetch:
        sign_items, memo_items, note_items = await asyncio.gather(
            _get_sign_items(user_id, limit, schema_name=schema_name),
            _get_memo_items(user_id, limit, schema_name=schema_name),
            _get_note_items(user_id, limit, schema_name=schema_name),
        )
    else:
        async with get_conn(schema_name=schema_name) as conn:
            sign_items = await _get_sign_items(user_id, limit, schema_name=schema_name, conn=conn)
            memo_items = await _get_memo_items(user_id, limit, schema_name=schema_name, conn=conn)
            note_items = await _get_note_items(user_id, limit, schema_name=schema_name, conn=conn)

    return {"sign": sign_items, "memo": memo_items, "note": note_items}


def _build_responsible_item(row) -> Dict[str, Any]:
    return {
        "key": f"responsible:{row['movement_id']}",
        "movement_id": str(row["movement_id"]),
        "case_id": str(row["case_id"]),
        "case_number": row["case_number"],
        "case_reference": row["case_reference"],
        "case_type": row["case_type"],
        "reason": row["reason"],
        "created_at": row["created_at"],
        "actor": {
            "name": row["actor_name"],
            "photo_url": row["actor_photo"],
            "sector_label": None,
        },
        "href": build_case_href(str(row["case_id"])),
    }


def _build_failed_signature_item(row) -> Dict[str, Any]:
    from services.documents.signing.failure_reasons import motivo_humano

    qué_pasó, qué_hacer = motivo_humano(row["failure_reason"])
    return {
        "key": f"signature_failed:{row['session_id']}",
        "session_id": str(row["session_id"]),
        "document_id": str(row["document_id"]),
        "document_reference": row["document_reference"],
        "reason": row["failure_reason"],
        "message": qué_pasó,
        "next_step": qué_hacer,
        "created_at": row["updated_at"],
        "href": build_sign_href(str(row["document_id"])),
    }


def _build_mention_item(row, viewable_ids: set) -> Dict[str, Any]:
    case_id = str(row["case_id"])
    can_view = case_id in viewable_ids
    return {
        "key": f"mention:{row['movement_id']}",
        "movement_id": str(row["movement_id"]),
        "case_id": case_id,
        "case_number": row["case_number"],
        "case_reference": row["case_reference"],
        "case_type": row["case_type"],
        "reason": row["reason"],
        "created_at": row["created_at"],
        "actor": {
            "name": row["actor_name"],
            "photo_url": row["actor_photo"],
            "sector_label": None,
        },
        "can_view": can_view,
        "href": build_case_href(case_id) if can_view else None,
    }


def _build_movement_item(row) -> Dict[str, Any]:
    return {
        "case_id": str(row["case_id"]),
        "case_number": row["case_number"],
        "case_reference": row["case_reference"],
        "case_type": row["case_type"],
        "short_ai_summary": row["short_ai_summary"],
        "new_count": row["new_count"],
        "last_move_at": row["last_move_at"],
        "href": build_case_href(str(row["case_id"])),
    }


async def get_home_cases(
    user_id: str,
    scope: str,
    limit: int,
    cursor: Optional[str],
    *,
    schema_name: str,
    _force_parallel_fetch: bool = False,
) -> Dict[str, Any]:
    if scope not in ("mine", "all"):
        raise ValidationError("scope debe ser 'mine' o 'all'")

    cursor_tuple = _decode_cursor(cursor) if cursor else None
    cursor_last_move_at = cursor_tuple[0] if cursor_tuple else None
    cursor_case_id = cursor_tuple[1] if cursor_tuple else None

    fetch_side_lists = cursor is None

    if _force_parallel_fetch:
        async def _responsible():
            if not fetch_side_lists:
                return []
            rows = await fetch_all(get_responsible_notifications_query(), user_id, schema_name=schema_name)
            return [_build_responsible_item(row) for row in rows]

        async def _mention():
            if not fetch_side_lists:
                return []
            mention_rows, viewable_rows = await asyncio.gather(
                fetch_all(get_mention_notifications_query(), user_id, schema_name=schema_name),
                fetch_all(
                    get_viewable_cases_cte() + "SELECT id FROM viewable_cases",
                    user_id, user_id, schema_name=schema_name,
                ),
            )
            viewable_ids = {str(r["id"]) for r in viewable_rows}
            return [_build_mention_item(row, viewable_ids) for row in mention_rows]

        async def _movements():
            rows = await fetch_all(
                get_case_movements_grouped_query(),
                user_id, user_id, user_id, scope,
                cursor_last_move_at, cursor_case_id, limit + 1,
                schema_name=schema_name,
            )
            has_more = len(rows) > limit
            if has_more:
                rows = rows[:limit]
            items = [_build_movement_item(row) for row in rows]
            next_cursor = None
            if has_more and items:
                last = items[-1]
                next_cursor = _encode_cursor(last["last_move_at"], last["case_id"])
            return {"items": items, "next_cursor": next_cursor}

        async def _failed_signatures():
            if not fetch_side_lists:
                return []
            rows = await fetch_all(
                get_failed_signature_notifications_query(),
                user_id, schema_name, schema_name=schema_name,
            )
            return [_build_failed_signature_item(row) for row in rows]

        responsible, mention, movements, failed_signatures = await asyncio.gather(
            _responsible(), _mention(), _movements(), _failed_signatures()
        )
        return {
            "scope": scope,
            "responsible": responsible,
            "mention": mention,
            "failed_signatures": failed_signatures,
            "case_movements": movements,
        }

    async with get_conn(schema_name=schema_name) as conn:
        responsible: List[Dict[str, Any]] = []
        mention: List[Dict[str, Any]] = []
        failed_signatures: List[Dict[str, Any]] = []

        if fetch_side_lists:
            responsible_rows = await conn.fetch(get_responsible_notifications_query(), user_id)
            responsible = [_build_responsible_item(row) for row in responsible_rows]

            mention_rows = await conn.fetch(get_mention_notifications_query(), user_id)
            viewable_rows = await conn.fetch(
                get_viewable_cases_cte() + "SELECT id FROM viewable_cases", user_id, user_id
            )
            viewable_ids = {str(r["id"]) for r in viewable_rows}
            mention = [_build_mention_item(row, viewable_ids) for row in mention_rows]

            failed_rows = await conn.fetch(
                get_failed_signature_notifications_query(), user_id, schema_name
            )
            failed_signatures = [_build_failed_signature_item(row) for row in failed_rows]

        movement_rows = await conn.fetch(
            get_case_movements_grouped_query(),
            user_id, user_id, user_id, scope,
            cursor_last_move_at, cursor_case_id, limit + 1,
        )
        has_more = len(movement_rows) > limit
        if has_more:
            movement_rows = movement_rows[:limit]

        items = [_build_movement_item(row) for row in movement_rows]
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = _encode_cursor(last["last_move_at"], last["case_id"])
        movements = {"items": items, "next_cursor": next_cursor}

    return {
        "scope": scope,
        "responsible": responsible,
        "mention": mention,
        "failed_signatures": failed_signatures,
        "case_movements": movements,
    }


def _build_unowned_item(row) -> Dict[str, Any]:
    return {
        "case_id": str(row["case_id"]),
        "case_number": row["case_number"],
        "case_reference": row["case_reference"],
        "case_type": row["case_type"],
        "created_at": row["created_at"],
        "ai_summary": row["short_ai_summary"],
        "href": build_case_href(str(row["case_id"])),
    }


def _build_task_item(row) -> Dict[str, Any]:
    return {
        "task_id": str(row["task_id"]),
        "case_id": str(row["case_id"]),
        "case_number": row["case_number"],
        "case_reference": row["case_reference"],
        "case_type": row["case_type"],
        "reason": row["reason"],
        "created_at": row["created_at"],
        "ai_summary": row["short_ai_summary"],
        "href": build_case_href(str(row["case_id"])),
    }


async def get_home_unassigned(
    user_id: str, limit: int, *, schema_name: str, _force_parallel_fetch: bool = False,
) -> Dict[str, Any]:
    if _force_parallel_fetch:
        sector_ids = await get_user_sector_ids(user_id, schema_name=schema_name)

        async def _unowned():
            rows, count_row = await asyncio.gather(
                fetch_all(get_unassigned_unowned_query(), user_id, user_id, limit, schema_name=schema_name),
                fetch_one(get_unassigned_unowned_count_query(), user_id, user_id, schema_name=schema_name),
            )
            items = [_build_unowned_item(row) for row in rows]
            total = count_row["total"] if count_row else 0
            return {"items": items, "total": total}

        async def _tasks():
            if not sector_ids:
                return {"items": [], "total": 0}
            rows, count_row = await asyncio.gather(
                fetch_all(get_unassigned_tasks_query(), sector_ids, limit, schema_name=schema_name),
                fetch_one(get_unassigned_tasks_count_query(), sector_ids, schema_name=schema_name),
            )
            items = [_build_task_item(row) for row in rows]
            total = count_row["total"] if count_row else 0
            return {"items": items, "total": total}

        unowned, tasks = await asyncio.gather(_unowned(), _tasks())
        return {"unowned": unowned, "tasks": tasks}

    async with get_conn(schema_name=schema_name) as conn:
        sector_rows = await conn.fetch(get_user_sectors_query(), user_id, user_id)
        sector_ids = [r["sector_id"] for r in sector_rows if r["sector_id"]]

        unowned_rows = await conn.fetch(get_unassigned_unowned_query(), user_id, user_id, limit)
        unowned_count_row = await conn.fetchrow(get_unassigned_unowned_count_query(), user_id, user_id)
        unowned = {
            "items": [_build_unowned_item(row) for row in unowned_rows],
            "total": unowned_count_row["total"] if unowned_count_row else 0,
        }

        if sector_ids:
            task_rows = await conn.fetch(get_unassigned_tasks_query(), sector_ids, limit)
            task_count_row = await conn.fetchrow(get_unassigned_tasks_count_query(), sector_ids)
            tasks = {
                "items": [_build_task_item(row) for row in task_rows],
                "total": task_count_row["total"] if task_count_row else 0,
            }
        else:
            tasks = {"items": [], "total": 0}

    return {"unowned": unowned, "tasks": tasks}


async def mark_case_viewed(user_id: str, case_id: str, *, schema_name: str) -> None:
    exists = await fetch_one(get_case_exists_query(), case_id, schema_name=schema_name)
    if not exists:
        raise NotFoundError(f"Expediente {case_id} no encontrado")

    async with transaction(schema_name=schema_name, user_id=user_id, auth_source="jwt") as conn:
        await conn.execute(upsert_case_user_view_query(), user_id, case_id)
        await conn.execute(dismiss_case_notifications_on_view_query(), user_id, case_id)


async def dismiss_notification(user_id: str, key: str, *, schema_name: str) -> None:
    if not key.startswith(_DISMISS_KEY_PREFIXES):
        raise ValidationError(
            "key debe empezar con 'responsible:', 'mention:' o 'signature_failed:'"
        )
    await execute(
        insert_notification_dismissal_query(), user_id, key,
        schema_name=schema_name, user_id=user_id, auth_source="jwt",
    )
