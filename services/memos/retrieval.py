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
from database import get_db_connection
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
    date_to: str | None = None
) -> tuple:
    """Wrapper de compatibilidad. Usa build_date_filter de query_utils."""
    return build_date_filter(date_filter, date_from, date_to)


def get_received_memos(
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

    Args:
        user_id: UUID del usuario receptor
        schema_name: Schema del tenant
        page: Numero de pagina (1-indexed)
        page_size: Elementos por pagina
        search: Termino de busqueda (busca en official_number, reference, content)
        date_filter: Filtro de fecha predefinido (hoy, ayer, ultimos_7_dias, ultimos_30_dias)
        date_from: Fecha desde (formato YYYY-MM-DD)
        date_to: Fecha hasta (formato YYYY-MM-DD)

    Returns:
        Dict con {
            memos: [...],
            pagination: {page, page_size, total, total_pages}
        }
    """
    offset = (page - 1) * page_size
    date_where, date_params = _build_date_filter(date_filter, date_from, date_to)

    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            if search:
                search_pattern = f"%{escape_like(search)}%"
                search_term = search.strip().lower()
                # Obtener total con busqueda
                cursor.execute(
                    get_received_memos_search_count_query(date_where=date_where),
                    (user_id, search_pattern, search_pattern, search_pattern, search_term, *date_params)
                )
                total = cursor.fetchone()['total']

                # Obtener memos con busqueda
                cursor.execute(
                    get_received_memos_search_query(date_where=date_where),
                    (user_id, search_pattern, search_pattern, search_pattern, search_term, *date_params, page_size, offset)
                )
            else:
                # Obtener total sin busqueda
                cursor.execute(
                    get_received_memos_count_query(date_where=date_where),
                    (user_id, *date_params)
                )
                total = cursor.fetchone()['total']

                # Obtener memos sin busqueda
                cursor.execute(
                    get_received_memos_query(date_where=date_where),
                    (user_id, *date_params, page_size, offset)
                )

            memos_raw = cursor.fetchall()

            # Formatear respuesta
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


def get_sent_memos(
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

    Args:
        user_id: UUID del usuario emisor
        schema_name: Schema del tenant
        page: Numero de pagina (1-indexed)
        page_size: Elementos por pagina
        search: Termino de busqueda (busca en official_number, reference, content)
        date_filter: Filtro de fecha predefinido
        date_from: Fecha desde (formato YYYY-MM-DD)
        date_to: Fecha hasta (formato YYYY-MM-DD)

    Returns:
        Dict con {
            memos: [...],
            pagination: {page, page_size, total, total_pages}
        }
    """
    offset = (page - 1) * page_size
    date_where, date_params = _build_date_filter(date_filter, date_from, date_to)

    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            if search:
                search_pattern = f"%{escape_like(search)}%"
                search_term = search.strip().lower()
                # Obtener total con busqueda
                cursor.execute(
                    get_sent_memos_search_count_query(date_where=date_where),
                    (user_id, search_pattern, search_pattern, search_pattern, search_term, *date_params)
                )
                total = cursor.fetchone()['total']

                # Obtener memos con busqueda
                cursor.execute(
                    get_sent_memos_search_query(date_where=date_where),
                    (user_id, search_pattern, search_pattern, search_pattern, search_term, *date_params, page_size, offset)
                )
            else:
                # Obtener total sin busqueda
                cursor.execute(
                    get_sent_memos_count_query(date_where=date_where),
                    (user_id, *date_params)
                )
                total = cursor.fetchone()['total']

                # Obtener memos sin busqueda
                cursor.execute(
                    get_sent_memos_query(date_where=date_where),
                    (user_id, *date_params, page_size, offset)
                )

            memos_raw = cursor.fetchall()

            # Formatear respuesta
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


def get_archived_memos(
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

    Args:
        user_id: UUID del usuario receptor
        schema_name: Schema del tenant
        page: Numero de pagina (1-indexed)
        page_size: Elementos por pagina
        search: Termino de busqueda (busca en official_number, reference, content)

    Returns:
        Dict con {
            memos: [...],
            pagination: {page, page_size, total, total_pages}
        }
    """
    offset = (page - 1) * page_size

    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            if search:
                search_pattern = f"%{escape_like(search)}%"
                search_term = search.strip().lower()
                # Obtener total con busqueda
                cursor.execute(
                    get_archived_memos_search_count_query(),
                    (user_id, search_pattern, search_pattern, search_pattern, search_term)
                )
                total = cursor.fetchone()['total']

                # Obtener memos con busqueda
                cursor.execute(
                    get_archived_memos_search_query(),
                    (user_id, search_pattern, search_pattern, search_pattern, search_term, page_size, offset)
                )
            else:
                # Obtener total sin busqueda
                cursor.execute(get_archived_memos_count_query(), (user_id,))
                total = cursor.fetchone()['total']

                # Obtener memos sin busqueda
                cursor.execute(
                    get_archived_memos_query(),
                    (user_id, page_size, offset)
                )

            memos_raw = cursor.fetchall()

            # Formatear respuesta
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
