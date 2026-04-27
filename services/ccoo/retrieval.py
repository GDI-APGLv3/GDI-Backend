"""
Servicios para recuperar CCOO (Comunicaciones Oficiales).
Unifica notas y memos en consultas UNION ALL con paginacion server-side.
"""

from typing import Dict, Any
from shared.logging import get_logger
from database import get_db_connection
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
    """Formatea un row de received/archived CCOO al formato de respuesta."""
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
    """Formatea un row de sent CCOO al formato de respuesta."""
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
    """Calcula total de paginas."""
    return (total + page_size - 1) // page_size if total > 0 else 1


def get_received_ccoo(
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
    """
    Obtiene las CCOO recibidas (notas + memos unificadas) con paginacion server-side.

    Args:
        sector_ids: Lista de UUIDs de sectores con can_view=true (para notas)
        user_id: UUID del usuario (para memos)
        schema_name: Schema del tenant
        page: Numero de pagina (1-indexed)
        page_size: Elementos por pagina
        search: Termino de busqueda
        date_filter: Filtro de fecha predefinido
        date_from: Fecha desde (YYYY-MM-DD)
        date_to: Fecha hasta (YYYY-MM-DD)

    Returns:
        Dict con {items: [...], pagination: {...}}
    """
    if not sector_ids:
        sector_ids = []

    offset = (page - 1) * page_size
    date_where, date_params = build_date_filter(date_filter, date_from, date_to)

    # Base params para named parameters
    base_params = {
        'sector_ids': sector_ids,
        'user_id': user_id,
        'limit': page_size,
        'offset': offset,
    }

    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            if search:
                search_pattern = f"%{escape_like(search)}%"
                search_term = search.strip().lower()
                search_params = {
                    **base_params,
                    'search_pattern': search_pattern,
                    'search_term': search_term,
                }

                # Count
                cursor.execute(
                    get_received_ccoo_search_count_query(date_where=date_where),
                    search_params
                )
                total = cursor.fetchone()['total']

                # Data
                cursor.execute(
                    get_received_ccoo_search_query(date_where=date_where),
                    search_params
                )
            else:
                # Count
                cursor.execute(
                    get_received_ccoo_count_query(date_where=date_where),
                    base_params
                )
                total = cursor.fetchone()['total']

                # Data
                cursor.execute(
                    get_received_ccoo_query(date_where=date_where),
                    base_params
                )

            rows = cursor.fetchall()
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


def get_sent_ccoo(
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
    """
    Obtiene las CCOO enviadas (notas + memos unificadas) con paginacion server-side.

    Args:
        sector_ids: Lista de UUIDs de sectores con can_view=true (para notas)
        user_id: UUID del usuario (para memos)
        schema_name: Schema del tenant
        page: Numero de pagina (1-indexed)
        page_size: Elementos por pagina
        search: Termino de busqueda
        date_filter: Filtro de fecha predefinido
        date_from: Fecha desde (YYYY-MM-DD)
        date_to: Fecha hasta (YYYY-MM-DD)

    Returns:
        Dict con {items: [...], pagination: {...}}
    """
    if not sector_ids:
        sector_ids = []

    offset = (page - 1) * page_size
    date_where, date_params = build_date_filter(date_filter, date_from, date_to)

    base_params = {
        'sector_ids': sector_ids,
        'user_id': user_id,
        'limit': page_size,
        'offset': offset,
    }

    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            if search:
                search_pattern = f"%{escape_like(search)}%"
                search_term = search.strip().lower()
                search_params = {
                    **base_params,
                    'search_pattern': search_pattern,
                    'search_term': search_term,
                }

                # Count
                cursor.execute(
                    get_sent_ccoo_search_count_query(date_where=date_where),
                    search_params
                )
                total = cursor.fetchone()['total']

                # Data
                cursor.execute(
                    get_sent_ccoo_search_query(date_where=date_where),
                    search_params
                )
            else:
                # Count
                cursor.execute(
                    get_sent_ccoo_count_query(date_where=date_where),
                    base_params
                )
                total = cursor.fetchone()['total']

                # Data
                cursor.execute(
                    get_sent_ccoo_query(date_where=date_where),
                    base_params
                )

            rows = cursor.fetchall()
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


def get_archived_ccoo(
    sector_ids: list[str],
    user_id: str,
    *,
    schema_name: str,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None
) -> Dict[str, Any]:
    """
    Obtiene las CCOO archivadas (notas + memos unificadas) con paginacion server-side.

    Args:
        sector_ids: Lista de UUIDs de sectores con can_view=true (para notas)
        user_id: UUID del usuario (para memos)
        schema_name: Schema del tenant
        page: Numero de pagina (1-indexed)
        page_size: Elementos por pagina
        search: Termino de busqueda

    Returns:
        Dict con {items: [...], pagination: {...}}
    """
    if not sector_ids:
        sector_ids = []

    offset = (page - 1) * page_size

    base_params = {
        'sector_ids': sector_ids,
        'user_id': user_id,
        'limit': page_size,
        'offset': offset,
    }

    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            if search:
                search_pattern = f"%{escape_like(search)}%"
                search_term = search.strip().lower()
                search_params = {
                    **base_params,
                    'search_pattern': search_pattern,
                    'search_term': search_term,
                }

                # Count
                cursor.execute(
                    get_archived_ccoo_search_count_query(),
                    search_params
                )
                total = cursor.fetchone()['total']

                # Data
                cursor.execute(
                    get_archived_ccoo_search_query(),
                    search_params
                )
            else:
                # Count
                cursor.execute(get_archived_ccoo_count_query(), base_params)
                total = cursor.fetchone()['total']

                # Data
                cursor.execute(get_archived_ccoo_query(), base_params)

            rows = cursor.fetchall()

            items = []
            for row in rows:
                item = _format_received_item(row)
                item['archived_at'] = row['archived_at'].isoformat() if row.get('archived_at') else None
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
