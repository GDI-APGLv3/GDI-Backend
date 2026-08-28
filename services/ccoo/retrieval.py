
from typing import Dict, Any
from shared.logging import get_logger
from database import get_conn
from services.shared.query_utils import escape_like, build_date_filter
from .queries import (
    get_received_ccoo_query,
    get_received_ccoo_count_query,
    get_received_ccoo_search_query,
    get_received_ccoo_search_count_query,
    get_sent_ccoo_query,
    get_sent_ccoo_count_query,
    get_sent_ccoo_search_query,
    get_sent_ccoo_search_count_query,
    get_archived_ccoo_query,
    get_archived_ccoo_count_query,
    get_archived_ccoo_search_query,
    get_archived_ccoo_search_count_query,
)

logger = get_logger(__name__)


def _format_received_item(row: dict) -> dict:
    read_status = row['read_status'] or {}
    sender = row['sender'] or {}

    return {
        'document_id': str(row['id']),
        'official_number': row['official_number'],
        'reference': row['reference'],
        'signed_at': row['signed_at'].isoformat() if row['signed_at'] else None,
        'ai_summary': row['ai_summary'],
        'document_type': row['document_type'],
        'recipient_type': row['recipient_type'],
        'ccoo_type': row['ccoo_type'],
        'sender': {
            'label': sender.get('label', ''),
            'detail': sender.get('detail', ''),
            'type': sender.get('type', 'sector'),
        },
        'read_status': {
            'opened': read_status.get('opened', False),
            'opened_at': read_status.get('opened_at'),
        }
    }


def _format_sent_item(row: dict) -> dict:
    return {
        'document_id': str(row['id']),
        'official_number': row['official_number'],
        'reference': row['reference'],
        'signed_at': row['signed_at'].isoformat() if row['signed_at'] else None,
        'ai_summary': row['ai_summary'],
        'document_type': row['document_type'],
        'ccoo_type': row['ccoo_type'],
        'recipients_label': row['recipients_label'] or '',
        'recipients_count': row['recipients_count'] or 0,
        'openings_count': row['openings_count'] or 0,
    }


def _calc_total_pages(total: int, page_size: int) -> int:
    return (total + page_size - 1) // page_size if total > 0 else 1


async def get_received_ccoo(
    sector_ids: list[str],
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
    if not sector_ids:
        sector_ids = []

    offset = (page - 1) * page_size

    async with get_conn(schema_name=schema_name) as conn:
        if search:
            search_pattern = f"%{escape_like(search)}%"
            search_term = search.strip().lower()

            count_where, count_date_params = build_date_filter(date_filter, date_from, date_to, start=5)
            count_row = await conn.fetchrow(
                get_received_ccoo_search_count_query(date_where=count_where),
                sector_ids, user_id, search_pattern, search_term, *count_date_params
            )
            total = count_row['total']

            data_where, data_date_params = build_date_filter(date_filter, date_from, date_to, start=7)
            rows = await conn.fetch(
                get_received_ccoo_search_query(date_where=data_where),
                sector_ids, user_id, search_pattern, search_term, page_size, offset, *data_date_params
            )
        else:
            count_where, count_date_params = build_date_filter(date_filter, date_from, date_to, start=3)
            count_row = await conn.fetchrow(
                get_received_ccoo_count_query(date_where=count_where),
                sector_ids, user_id, *count_date_params
            )
            total = count_row['total']

            data_where, data_date_params = build_date_filter(date_filter, date_from, date_to, start=5)
            rows = await conn.fetch(
                get_received_ccoo_query(date_where=data_where),
                sector_ids, user_id, page_size, offset, *data_date_params
            )

        items = [_format_received_item(row) for row in rows]

        logger.debug(
            f"[{schema_name}] User {user_id}: {len(items)} CCOO recibidas "
            f"(pagina {page}){' search=' + search if search else ''}"
        )

        return {
            'items': items,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total': total,
                'total_pages': _calc_total_pages(total, page_size),
            }
        }


async def get_sent_ccoo(
    sector_ids: list[str],
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
    if not sector_ids:
        sector_ids = []

    offset = (page - 1) * page_size

    async with get_conn(schema_name=schema_name) as conn:
        if search:
            search_pattern = f"%{escape_like(search)}%"
            search_term = search.strip().lower()

            count_where, count_date_params = build_date_filter(date_filter, date_from, date_to, start=5)
            count_row = await conn.fetchrow(
                get_sent_ccoo_search_count_query(date_where=count_where),
                sector_ids, user_id, search_pattern, search_term, *count_date_params
            )
            total = count_row['total']

            data_where, data_date_params = build_date_filter(date_filter, date_from, date_to, start=7)
            rows = await conn.fetch(
                get_sent_ccoo_search_query(date_where=data_where),
                sector_ids, user_id, search_pattern, search_term, page_size, offset, *data_date_params
            )
        else:
            count_where, count_date_params = build_date_filter(date_filter, date_from, date_to, start=3)
            count_row = await conn.fetchrow(
                get_sent_ccoo_count_query(date_where=count_where),
                sector_ids, user_id, *count_date_params
            )
            total = count_row['total']

            data_where, data_date_params = build_date_filter(date_filter, date_from, date_to, start=5)
            rows = await conn.fetch(
                get_sent_ccoo_query(date_where=data_where),
                sector_ids, user_id, page_size, offset, *data_date_params
            )

        items = [_format_sent_item(row) for row in rows]

        logger.debug(
            f"[{schema_name}] User {user_id}: {len(items)} CCOO enviadas "
            f"(pagina {page}){' search=' + search if search else ''}"
        )

        return {
            'items': items,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total': total,
                'total_pages': _calc_total_pages(total, page_size),
            }
        }


async def get_archived_ccoo(
    sector_ids: list[str],
    user_id: str,
    *,
    schema_name: str,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None
) -> Dict[str, Any]:
    if not sector_ids:
        sector_ids = []

    offset = (page - 1) * page_size

    async with get_conn(schema_name=schema_name) as conn:
        if search:
            search_pattern = f"%{escape_like(search)}%"
            search_term = search.strip().lower()

            count_row = await conn.fetchrow(
                get_archived_ccoo_search_count_query(),
                sector_ids, user_id, search_pattern, search_term
            )
            total = count_row['total']

            rows = await conn.fetch(
                get_archived_ccoo_search_query(),
                sector_ids, user_id, search_pattern, search_term, page_size, offset
            )
        else:
            count_row = await conn.fetchrow(
                get_archived_ccoo_count_query(),
                sector_ids, user_id
            )
            total = count_row['total']

            rows = await conn.fetch(
                get_archived_ccoo_query(),
                sector_ids, user_id, page_size, offset
            )

        items = []
        for row in rows:
            item = _format_received_item(row)
            item['archived_at'] = row['archived_at'].isoformat() if row['archived_at'] else None
            items.append(item)

        logger.debug(
            f"[{schema_name}] User {user_id}: {len(items)} CCOO archivadas "
            f"(pagina {page}){' search=' + search if search else ''}"
        )

        return {
            'items': items,
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total': total,
                'total_pages': _calc_total_pages(total, page_size),
            }
        }
