"""
Servicios para recuperar MEMOS.
Consultas de bandeja de entrada (received), enviados (sent) y archivados.

Diferencias clave con NOTAS:
- NO hay variantes multi_sector (query directa por user_id)
- read_status se obtiene de mr.opened_at inline (no tabla separada)
- sender info muestra nombre de persona + sector snapshot
"""

from typing import Dict, Any
from shared.logging import get_logger
from database import fetch_one, fetch_all
from services.shared.query_utils import escape_like, build_date_filter
from .queries import (
    get_received_memos_query,
    get_received_memos_count_query,
    get_received_memos_search_query,
    get_received_memos_search_count_query,
    get_sent_memos_query,
    get_sent_memos_count_query,
    get_sent_memos_search_query,
    get_sent_memos_search_count_query,
    get_archived_memos_query,
    get_archived_memos_count_query,
    get_archived_memos_search_query,
    get_archived_memos_search_count_query,
)

logger = get_logger(__name__)


def _build_date_filter(
    date_filter: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    start: int = 1,
) -> tuple:
    """Wrapper de compatibilidad. Usa build_date_filter de query_utils."""
    return build_date_filter(date_filter, date_from, date_to, start=start)


async def get_received_memos(
    user_id: str,
    *,
    schema_name: str,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
    date_filter: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None
) -> Dict[str, Any]:
    """
    Obtiene los memos recibidos por un usuario.
    Solo incluye memos oficializados (existentes en official_documents).
    """
    offset = (page - 1) * page_size

    if search:
        search_pattern = f"%{escape_like(search)}%"
        search_term = search.strip().lower()
        # Params fijos: $1=user_id, $2-$5=search, luego date params, luego limit/offset
        # Count: $1...$5 + date
        date_where, date_params = _build_date_filter(date_filter, date_from, date_to, start=6)
        count_params = (user_id, search_pattern, search_pattern, search_pattern, search_term, *date_params)
        row = await fetch_one(
            get_received_memos_search_count_query(date_where=date_where),
            *count_params,
            schema_name=schema_name
        )
        total = row['total'] if row else 0

        # Data: $1...$5 + date + $6=limit, $7=offset (date_where already at $6 if empty)
        # Since queries have fixed $6/$7 for limit/offset (no date support in search),
        # pass date_params before limit/offset is not supported in fixed queries.
        # Use count query date_where in data query too, limit/offset append after date params.
        # Rebuild for data query: date starts at $6, limit/offset at $6+len(date_params)
        limit_idx = 6 + len(date_params)
        offset_idx = limit_idx + 1
        data_query = get_received_memos_search_query(date_where=date_where).replace(
            "LIMIT $6 OFFSET $7",
            f"LIMIT ${limit_idx} OFFSET ${offset_idx}"
        )
        data_params = (user_id, search_pattern, search_pattern, search_pattern, search_term, *date_params, page_size, offset)
        memos_raw = await fetch_all(data_query, *data_params, schema_name=schema_name)
    else:
        date_where, date_params = _build_date_filter(date_filter, date_from, date_to, start=2)
        count_params = (user_id, *date_params)
        row = await fetch_one(
            get_received_memos_count_query(date_where=date_where),
            *count_params,
            schema_name=schema_name
        )
        total = row['total'] if row else 0

        limit_idx = 2 + len(date_params)
        offset_idx = limit_idx + 1
        data_query = get_received_memos_query(date_where=date_where).replace(
            "LIMIT $2 OFFSET $3",
            f"LIMIT ${limit_idx} OFFSET ${offset_idx}"
        )
        data_params = (user_id, *date_params, page_size, offset)
        memos_raw = await fetch_all(data_query, *data_params, schema_name=schema_name)

    memos = []
    for row in memos_raw:
        memo = {
            'document_id': str(row['id']),
            'official_number': row['official_number'],
            'reference': row['reference'],
            'signed_at': row['signed_at'].isoformat() if row['signed_at'] else None,
            'ai_summary': row['ai_summary'],
            'document_type': row['document_type'],
            'recipient_type': row['recipient_type'],
            'sender': {
                'user_id': str(row['sender_user_id']),
                'full_name': row['sender_name'],
                'sector_acronym': row['sender_sector_acronym'] or ''
            },
            'read_status': {
                'opened': row['opened_at'] is not None,
                'opened_at': row['opened_at'].isoformat() if row['opened_at'] else None
            }
        }
        memos.append(memo)

    total_pages = (total + page_size - 1) // page_size if total > 0 else 1

    logger.debug(
        f"[{schema_name}] User {user_id}: {len(memos)} memos recibidos "
        f"(pagina {page}){' search=' + search if search else ''}"
    )

    return {
        'memos': memos,
        'pagination': {
            'page': page,
            'page_size': page_size,
            'total': total,
            'total_pages': total_pages
        }
    }


async def get_sent_memos(
    user_id: str,
    *,
    schema_name: str,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
    date_filter: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None
) -> Dict[str, Any]:
    """
    Obtiene los memos enviados por un usuario.
    Solo incluye memos oficializados.
    """
    offset = (page - 1) * page_size

    if search:
        search_pattern = f"%{escape_like(search)}%"
        search_term = search.strip().lower()
        date_where, date_params = _build_date_filter(date_filter, date_from, date_to, start=6)
        count_params = (user_id, search_pattern, search_pattern, search_pattern, search_term, *date_params)
        row = await fetch_one(
            get_sent_memos_search_count_query(date_where=date_where),
            *count_params,
            schema_name=schema_name
        )
        total = row['total'] if row else 0

        limit_idx = 6 + len(date_params)
        offset_idx = limit_idx + 1
        data_query = get_sent_memos_search_query(date_where=date_where).replace(
            "LIMIT $6 OFFSET $7",
            f"LIMIT ${limit_idx} OFFSET ${offset_idx}"
        )
        data_params = (user_id, search_pattern, search_pattern, search_pattern, search_term, *date_params, page_size, offset)
        memos_raw = await fetch_all(data_query, *data_params, schema_name=schema_name)
    else:
        date_where, date_params = _build_date_filter(date_filter, date_from, date_to, start=2)
        count_params = (user_id, *date_params)
        row = await fetch_one(
            get_sent_memos_count_query(date_where=date_where),
            *count_params,
            schema_name=schema_name
        )
        total = row['total'] if row else 0

        limit_idx = 2 + len(date_params)
        offset_idx = limit_idx + 1
        data_query = get_sent_memos_query(date_where=date_where).replace(
            "LIMIT $2 OFFSET $3",
            f"LIMIT ${limit_idx} OFFSET ${offset_idx}"
        )
        data_params = (user_id, *date_params, page_size, offset)
        memos_raw = await fetch_all(data_query, *data_params, schema_name=schema_name)

    memos = []
    for row in memos_raw:
        memo = {
            'document_id': str(row['id']),
            'official_number': row['official_number'],
            'reference': row['reference'],
            'signed_at': row['signed_at'].isoformat() if row['signed_at'] else None,
            'ai_summary': row['ai_summary'],
            'document_type': row['document_type'],
            'recipients': row['recipients'] or [],
            'openings_count': row['openings_count']
        }
        memos.append(memo)

    total_pages = (total + page_size - 1) // page_size if total > 0 else 1

    logger.debug(
        f"[{schema_name}] User {user_id}: {len(memos)} memos enviados "
        f"(pagina {page}){' search=' + search if search else ''}"
    )

    return {
        'memos': memos,
        'pagination': {
            'page': page,
            'page_size': page_size,
            'total': total,
            'total_pages': total_pages
        }
    }


async def get_archived_memos(
    user_id: str,
    *,
    schema_name: str,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None
) -> Dict[str, Any]:
    """
    Obtiene los memos ARCHIVADOS de un usuario.
    Solo incluye memos oficializados que fueron archivados por el receptor.
    """
    offset = (page - 1) * page_size

    if search:
        search_pattern = f"%{escape_like(search)}%"
        search_term = search.strip().lower()
        row = await fetch_one(
            get_archived_memos_search_count_query(),
            user_id, search_pattern, search_pattern, search_pattern, search_term,
            schema_name=schema_name
        )
        total = row['total'] if row else 0

        memos_raw = await fetch_all(
            get_archived_memos_search_query(),
            user_id, search_pattern, search_pattern, search_pattern, search_term,
            page_size, offset,
            schema_name=schema_name
        )
    else:
        row = await fetch_one(
            get_archived_memos_count_query(), user_id,
            schema_name=schema_name
        )
        total = row['total'] if row else 0

        memos_raw = await fetch_all(
            get_archived_memos_query(), user_id, page_size, offset,
            schema_name=schema_name
        )

    memos = []
    for row in memos_raw:
        memo = {
            'document_id': str(row['id']),
            'official_number': row['official_number'],
            'reference': row['reference'],
            'signed_at': row['signed_at'].isoformat() if row['signed_at'] else None,
            'ai_summary': row['ai_summary'],
            'document_type': row['document_type'],
            'recipient_type': row['recipient_type'],
            'is_archived': row['is_archived'],
            'archived_at': row['archived_at'].isoformat() if row['archived_at'] else None,
            'sender': {
                'user_id': str(row['sender_user_id']),
                'full_name': row['sender_name'],
                'sector_acronym': row['sender_sector_acronym'] or ''
            },
            'read_status': {
                'opened': row['opened_at'] is not None,
                'opened_at': row['opened_at'].isoformat() if row['opened_at'] else None
            }
        }
        memos.append(memo)

    total_pages = (total + page_size - 1) // page_size if total > 0 else 1

    logger.debug(
        f"[{schema_name}] User {user_id}: {len(memos)} memos archivados "
        f"(pagina {page}){' search=' + search if search else ''}"
    )

    return {
        'memos': memos,
        'pagination': {
            'page': page,
            'page_size': page_size,
            'total': total,
            'total_pages': total_pages
        }
    }
