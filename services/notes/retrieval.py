"""
Servicios para recuperar NOTAS.
Consultas de bandeja de entrada (received) y enviados (sent).
"""

from typing import Dict, List, Any
from shared.logging import get_logger
from database import get_db_connection
from .queries import (
    get_sent_notes_query,
    get_sent_notes_count_query,
    get_received_notes_query,
    get_received_notes_count_query,
    get_received_notes_multi_sector_paginated_query,
    get_received_notes_multi_sector_count_query,
    get_sent_notes_multi_sector_query,
    get_sent_notes_multi_sector_count_query,
    get_received_notes_multi_sector_search_query,
    get_received_notes_multi_sector_search_count_query,
    get_sent_notes_multi_sector_search_query,
    get_sent_notes_multi_sector_search_count_query,
    get_archived_notes_multi_sector_paginated_query,
    get_archived_notes_multi_sector_count_query,
    get_archived_notes_multi_sector_search_query,
    get_archived_notes_multi_sector_search_count_query,
)

logger = get_logger(__name__)


def escape_like(value: str) -> str:
    """
    Escapa caracteres especiales de ILIKE (%, _) para evitar wildcards inesperados.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _build_date_filter(
    date_filter: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None
) -> tuple:
    """
    Construye clausula SQL de filtro de fecha para notas.
    Returns: (clause: str con AND prefix o vacio, params: list)
    """
    if date_filter:
        mapping = {
            "hoy": "AND DATE(od.signed_at) = CURRENT_DATE",
            "ayer": "AND DATE(od.signed_at) = CURRENT_DATE - INTERVAL '1 day'",
            "ultimos_7_dias": "AND od.signed_at >= CURRENT_DATE - INTERVAL '7 days'",
            "ultimos_30_dias": "AND od.signed_at >= CURRENT_DATE - INTERVAL '30 days'",
        }
        clause = mapping.get(date_filter, "")
        return clause, []

    clauses = []
    params = []
    if date_from:
        clauses.append("AND od.signed_at >= %s")
        params.append(date_from)
    if date_to:
        clauses.append("AND od.signed_at < %s::date + INTERVAL '1 day'")
        params.append(date_to)

    return " ".join(clauses), params


def get_sent_notes(
    sector_id: str,
    *,
    schema_name: str,
    page: int = 1,
    page_size: int = 20
) -> Dict[str, Any]:
    """
    Obtiene las notas enviadas por un sector.
    Solo incluye notas oficializadas (existentes en official_documents).

    Args:
        sector_id: UUID del sector emisor
        schema_name: Schema del tenant
        page: Número de página (1-indexed)
        page_size: Elementos por página

    Returns:
        Dict con {
            notes: [...],
            pagination: {page, page_size, total, total_pages}
        }
    """
    offset = (page - 1) * page_size

    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            # Obtener total
            cursor.execute(get_sent_notes_count_query(), (sector_id,))
            total = cursor.fetchone()['total']

            # Obtener notas
            cursor.execute(get_sent_notes_query(), (sector_id, page_size, offset))
            notes_raw = cursor.fetchall()

            # Formatear respuesta
            notes = []
            for row in notes_raw:
                note = {
                    'document_id': str(row['id']),
                    'official_number': row['official_number'],
                    'reference': row['reference'],
                    'signed_at': row['signed_at'].isoformat() if row['signed_at'] else None,
                    'ai_summary': row['ai_summary'],
                    'document_type': row['document_type'],
                    'recipients': row['recipients'] or [],
                    'openings_count': row['openings_count']
                }
                notes.append(note)

            total_pages = (total + page_size - 1) // page_size if total > 0 else 1

            logger.debug(f"[{schema_name}] Sector {sector_id}: {len(notes)} notas enviadas (página {page})")

            return {
                'notes': notes,
                'pagination': {
                    'page': page,
                    'page_size': page_size,
                    'total': total,
                    'total_pages': total_pages
                }
            }


def get_received_notes(
    sector_id: str,
    *,
    schema_name: str,
    page: int = 1,
    page_size: int = 20,
    unread_only: bool = False
) -> Dict[str, Any]:
    """
    Obtiene las notas recibidas por un sector.
    Solo incluye notas oficializadas.

    Args:
        sector_id: UUID del sector receptor
        schema_name: Schema del tenant
        page: Número de página (1-indexed)
        page_size: Elementos por página
        unread_only: Si True, solo retorna notas no leídas

    Returns:
        Dict con {
            notes: [...],
            pagination: {page, page_size, total, total_pages}
        }
    """
    offset = (page - 1) * page_size

    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            # Obtener total
            cursor.execute(get_received_notes_count_query(), (sector_id,))
            total = cursor.fetchone()['total']

            # Obtener notas (el sector_id se pasa 3 veces para las subconsultas)
            cursor.execute(
                get_received_notes_query(),
                (sector_id, sector_id, sector_id, page_size, offset)
            )
            notes_raw = cursor.fetchall()

            # Formatear respuesta
            notes = []
            for row in notes_raw:
                read_status = row['read_status'] or {}

                # Si solo queremos no leídas y ya está leída, skip
                if unread_only and read_status.get('opened'):
                    continue

                note = {
                    'document_id': str(row['id']),
                    'official_number': row['official_number'],
                    'reference': row['reference'],
                    'signed_at': row['signed_at'].isoformat() if row['signed_at'] else None,
                    'ai_summary': row['ai_summary'],
                    'document_type': row['document_type'],
                    'recipient_type': row['recipient_type'],
                    'sender': {
                        'sector_id': str(row['sender_sector_id']),
                        'acronym': row['sender_sector_acronym'],
                        'department_name': row['sender_department_name']
                    },
                    'read_status': {
                        'opened': read_status.get('opened', False),
                        'opened_at': read_status.get('opened_at')
                    }
                }
                notes.append(note)

            total_pages = (total + page_size - 1) // page_size if total > 0 else 1

            logger.debug(f"[{schema_name}] Sector {sector_id}: {len(notes)} notas recibidas (página {page})")

            return {
                'notes': notes,
                'pagination': {
                    'page': page,
                    'page_size': page_size,
                    'total': total,
                    'total_pages': total_pages
                }
            }


def get_received_notes_multi_sector(
    sector_ids: list[str],
    *,
    schema_name: str,
    page: int = 1,
    page_size: int = 20,
    unread_only: bool = False,
    search: str | None = None,
    date_filter: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None
) -> Dict[str, Any]:
    """
    Obtiene las notas recibidas en CUALQUIERA de los sectores dados.
    Solo incluye notas oficializadas.

    Args:
        sector_ids: Lista de UUIDs de sectores receptores
        schema_name: Schema del tenant
        page: Número de página (1-indexed)
        page_size: Elementos por página
        unread_only: Si True, solo retorna notas no leídas
        search: Término de búsqueda (busca en official_number, reference, content)

    Returns:
        Dict con {
            notes: [...],
            pagination: {page, page_size, total, total_pages}
        }
    """
    if not sector_ids:
        return {
            'notes': [],
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total': 0,
                'total_pages': 1
            }
        }

    offset = (page - 1) * page_size
    date_where, date_params = _build_date_filter(date_filter, date_from, date_to)

    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            # Si hay búsqueda, usar queries con ILIKE
            if search:
                search_pattern = f"%{escape_like(search)}%"
                search_term = search.strip().lower()
                # Obtener total con búsqueda
                cursor.execute(
                    get_received_notes_multi_sector_search_count_query(date_where=date_where),
                    (sector_ids, search_pattern, search_pattern, search_pattern, search_term, *date_params)
                )
                total = cursor.fetchone()['total']

                # Obtener notas con búsqueda
                cursor.execute(
                    get_received_notes_multi_sector_search_query(date_where=date_where),
                    (sector_ids, search_pattern, search_pattern, search_pattern, search_term, *date_params, page_size, offset)
                )
            else:
                # Obtener total sin búsqueda
                cursor.execute(
                    get_received_notes_multi_sector_count_query(date_where=date_where),
                    (sector_ids, *date_params)
                )
                total = cursor.fetchone()['total']

                # Obtener notas sin búsqueda
                cursor.execute(
                    get_received_notes_multi_sector_paginated_query(date_where=date_where),
                    (sector_ids, *date_params, page_size, offset)
                )

            notes_raw = cursor.fetchall()

            # Formatear respuesta
            notes = []
            for row in notes_raw:
                read_status = row['read_status'] or {}

                # Si solo queremos no leídas y ya está leída, skip
                if unread_only and read_status.get('opened'):
                    continue

                note = {
                    'document_id': str(row['id']),
                    'official_number': row['official_number'],
                    'reference': row['reference'],
                    'signed_at': row['signed_at'].isoformat() if row['signed_at'] else None,
                    'ai_summary': row['ai_summary'],
                    'document_type': row['document_type'],
                    'recipient_type': row['recipient_type'],
                    'sender': {
                        'sector_id': str(row['sender_sector_id']),
                        'acronym': row['sender_sector_acronym'],
                        'department_name': row['sender_department_name']
                    },
                    'read_status': {
                        'opened': read_status.get('opened', False),
                        'opened_at': read_status.get('opened_at')
                    }
                }
                notes.append(note)

            total_pages = (total + page_size - 1) // page_size if total > 0 else 1

            logger.debug(f"[{schema_name}] Sectores {sector_ids}: {len(notes)} notas recibidas (página {page}){' search=' + search if search else ''}")

            return {
                'notes': notes,
                'pagination': {
                    'page': page,
                    'page_size': page_size,
                    'total': total,
                    'total_pages': total_pages
                }
            }


def get_sent_notes_multi_sector(
    sector_ids: list[str],
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
    Obtiene las notas enviadas desde CUALQUIERA de los sectores dados.
    Solo incluye notas oficializadas.

    Args:
        sector_ids: Lista de UUIDs de sectores emisores
        schema_name: Schema del tenant
        page: Número de página (1-indexed)
        page_size: Elementos por página
        search: Término de búsqueda (busca en official_number, reference, content)

    Returns:
        Dict con {
            notes: [...],
            pagination: {page, page_size, total, total_pages}
        }
    """
    if not sector_ids:
        return {
            'notes': [],
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total': 0,
                'total_pages': 1
            }
        }

    offset = (page - 1) * page_size
    date_where, date_params = _build_date_filter(date_filter, date_from, date_to)

    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            # Si hay búsqueda, usar queries con ILIKE
            if search:
                search_pattern = f"%{escape_like(search)}%"
                search_term = search.strip().lower()
                # Obtener total con búsqueda
                cursor.execute(
                    get_sent_notes_multi_sector_search_count_query(date_where=date_where),
                    (sector_ids, search_pattern, search_pattern, search_pattern, search_term, *date_params)
                )
                total = cursor.fetchone()['total']

                # Obtener notas con búsqueda
                cursor.execute(
                    get_sent_notes_multi_sector_search_query(date_where=date_where),
                    (sector_ids, search_pattern, search_pattern, search_pattern, search_term, *date_params, page_size, offset)
                )
            else:
                # Obtener total sin búsqueda
                cursor.execute(
                    get_sent_notes_multi_sector_count_query(date_where=date_where),
                    (sector_ids, *date_params)
                )
                total = cursor.fetchone()['total']

                # Obtener notas sin búsqueda
                cursor.execute(
                    get_sent_notes_multi_sector_query(date_where=date_where),
                    (sector_ids, *date_params, page_size, offset)
                )

            notes_raw = cursor.fetchall()

            # Formatear respuesta
            notes = []
            for row in notes_raw:
                note = {
                    'document_id': str(row['id']),
                    'official_number': row['official_number'],
                    'reference': row['reference'],
                    'signed_at': row['signed_at'].isoformat() if row['signed_at'] else None,
                    'ai_summary': row['ai_summary'],
                    'document_type': row['document_type'],
                    'recipients': row['recipients'] or [],
                    'openings_count': row['openings_count']
                }
                notes.append(note)

            total_pages = (total + page_size - 1) // page_size if total > 0 else 1

            logger.debug(f"[{schema_name}] Sectores {sector_ids}: {len(notes)} notas enviadas (página {page}){' search=' + search if search else ''}")

            return {
                'notes': notes,
                'pagination': {
                    'page': page,
                    'page_size': page_size,
                    'total': total,
                    'total_pages': total_pages
                }
            }


def get_archived_notes_multi_sector(
    sector_ids: list[str],
    *,
    schema_name: str,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None
) -> Dict[str, Any]:
    """
    Obtiene las notas ARCHIVADAS en CUALQUIERA de los sectores dados.
    Solo incluye notas oficializadas que fueron archivadas por el receptor.

    Args:
        sector_ids: Lista de UUIDs de sectores receptores
        schema_name: Schema del tenant
        page: Número de página (1-indexed)
        page_size: Elementos por página
        search: Término de búsqueda (busca en official_number, reference, content)

    Returns:
        Dict con {
            notes: [...],
            pagination: {page, page_size, total, total_pages}
        }
    """
    if not sector_ids:
        return {
            'notes': [],
            'pagination': {
                'page': page,
                'page_size': page_size,
                'total': 0,
                'total_pages': 1
            }
        }

    offset = (page - 1) * page_size

    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            # Si hay búsqueda, usar queries con ILIKE
            if search:
                search_pattern = f"%{escape_like(search)}%"
                search_term = search.strip().lower()
                # Obtener total con búsqueda
                cursor.execute(
                    get_archived_notes_multi_sector_search_count_query(),
                    (sector_ids, search_pattern, search_pattern, search_pattern, search_term)
                )
                total = cursor.fetchone()['total']

                # Obtener notas con búsqueda
                cursor.execute(
                    get_archived_notes_multi_sector_search_query(),
                    (sector_ids, search_pattern, search_pattern, search_pattern, search_term, page_size, offset)
                )
            else:
                # Obtener total sin búsqueda
                cursor.execute(get_archived_notes_multi_sector_count_query(), (sector_ids,))
                total = cursor.fetchone()['total']

                # Obtener notas sin búsqueda
                cursor.execute(
                    get_archived_notes_multi_sector_paginated_query(),
                    (sector_ids, page_size, offset)
                )

            notes_raw = cursor.fetchall()

            # Formatear respuesta
            notes = []
            for row in notes_raw:
                read_status = row['read_status'] or {}

                note = {
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
                        'sector_id': str(row['sender_sector_id']),
                        'acronym': row['sender_sector_acronym'],
                        'department_name': row['sender_department_name']
                    },
                    'read_status': {
                        'opened': read_status.get('opened', False),
                        'opened_at': read_status.get('opened_at')
                    }
                }
                notes.append(note)

            total_pages = (total + page_size - 1) // page_size if total > 0 else 1

            logger.debug(f"[{schema_name}] Sectores {sector_ids}: {len(notes)} notas archivadas (página {page}){' search=' + search if search else ''}")

            return {
                'notes': notes,
                'pagination': {
                    'page': page,
                    'page_size': page_size,
                    'total': total,
                    'total_pages': total_pages
                }
            }
