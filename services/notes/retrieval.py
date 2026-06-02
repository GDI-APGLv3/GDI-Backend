"""
Servicios para recuperar NOTAS.
Consultas de bandeja de entrada (received) y enviados (sent).
"""

from typing import Dict, Any
from shared.logging import get_logger
from database import fetch_all, fetch_one
from services.shared.query_utils import escape_like, build_date_filter
from .queries import (
    get_sent_notes_count_query,
    get_sent_notes_query,
    get_received_notes_count_query,
    get_received_notes_query,
    get_archived_notes_multi_sector_count_query,
    get_archived_notes_multi_sector_paginated_query,
    get_archived_notes_multi_sector_search_count_query,
    get_archived_notes_multi_sector_search_query,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers internos: construyen SQL completo con $N correctos segun offset
# ---------------------------------------------------------------------------

def _received_paginated_sql(date_where: str, limit_n: int, offset_n: int) -> str:
    return f"""
        WITH distinct_notes AS (
            SELECT DISTINCT ON (od.id)
                od.id, od.official_number, od.reference, od.signed_at,
                od.resume as ai_summary, dt.acronym as document_type,
                nr.recipient_type, nr.sector_id as matched_sector_id,
                nr.sender_sector_id, ss.acronym as sender_sector_acronym,
                sd.name as sender_department_name,
                (SELECT json_build_object(
                    'opened', EXISTS(
                        SELECT 1 FROM notes_openings no
                        WHERE no.document_id = od.id AND no.sector_id = nr.sector_id
                    ),
                    'opened_at', (
                        SELECT no.opened_at FROM notes_openings no
                        WHERE no.document_id = od.id AND no.sector_id = nr.sector_id LIMIT 1
                    )
                )) as read_status
            FROM official_documents od
            JOIN notes_recipients nr
                ON nr.document_id = od.id AND nr.sector_id = ANY($1::uuid[]) AND nr.is_archived = false
            JOIN document_types dt ON dt.id = od.document_type_id
            JOIN sectors ss ON ss.id = nr.sender_sector_id
            JOIN departments sd ON sd.id = ss.department_id
            WHERE od.signed_at IS NOT NULL {date_where}
            ORDER BY od.id, nr.recipient_type
        )
        SELECT * FROM distinct_notes
        ORDER BY signed_at DESC
        LIMIT ${limit_n} OFFSET ${offset_n}
    """


def _received_count_sql(date_where: str) -> str:
    return f"""
        SELECT COUNT(DISTINCT od.id)::int as total
        FROM official_documents od
        JOIN notes_recipients nr
            ON nr.document_id = od.id AND nr.sector_id = ANY($1::uuid[]) AND nr.is_archived = false
        WHERE od.signed_at IS NOT NULL {date_where}
    """


def _received_search_paginated_sql(date_where: str, limit_n: int, offset_n: int) -> str:
    return f"""
        WITH distinct_notes AS (
            SELECT DISTINCT ON (od.id)
                od.id, od.official_number, od.reference, od.signed_at,
                od.resume as ai_summary, dt.acronym as document_type,
                nr.recipient_type, nr.sector_id as matched_sector_id,
                nr.sender_sector_id, ss.acronym as sender_sector_acronym,
                sd.name as sender_department_name,
                (SELECT json_build_object(
                    'opened', EXISTS(
                        SELECT 1 FROM notes_openings no
                        WHERE no.document_id = od.id AND no.sector_id = nr.sector_id
                    ),
                    'opened_at', (
                        SELECT no.opened_at FROM notes_openings no
                        WHERE no.document_id = od.id AND no.sector_id = nr.sector_id LIMIT 1
                    )
                )) as read_status
            FROM official_documents od
            JOIN notes_recipients nr
                ON nr.document_id = od.id AND nr.sector_id = ANY($1::uuid[]) AND nr.is_archived = false
            JOIN document_types dt ON dt.id = od.document_type_id
            JOIN sectors ss ON ss.id = nr.sender_sector_id
            JOIN departments sd ON sd.id = ss.department_id
            WHERE od.signed_at IS NOT NULL
              AND (
                od.official_number ILIKE $2
                OR od.reference ILIKE $3
                OR od.content->>'html' ILIKE $4
                OR similarity(LOWER(COALESCE(od.reference, '')), LOWER($5)) > 0.3
              )
            {date_where}
            ORDER BY od.id, nr.recipient_type
        )
        SELECT * FROM distinct_notes
        ORDER BY signed_at DESC
        LIMIT ${limit_n} OFFSET ${offset_n}
    """


def _received_search_count_sql(date_where: str) -> str:
    return f"""
        SELECT COUNT(DISTINCT od.id)::int as total
        FROM official_documents od
        JOIN notes_recipients nr
            ON nr.document_id = od.id AND nr.sector_id = ANY($1::uuid[]) AND nr.is_archived = false
        WHERE od.signed_at IS NOT NULL
          AND (
            od.official_number ILIKE $2
            OR od.reference ILIKE $3
            OR od.content->>'html' ILIKE $4
            OR similarity(LOWER(COALESCE(od.reference, '')), LOWER($5)) > 0.3
          )
        {date_where}
    """


def _sent_paginated_sql(date_where: str, limit_n: int, offset_n: int) -> str:
    return f"""
        SELECT
            od.id, od.official_number, od.reference, od.signed_at,
            od.resume as ai_summary, dt.acronym as document_type,
            (SELECT json_agg(json_build_object(
                'sector_id', nr2.sector_id, 'type', nr2.recipient_type,
                'acronym', s2.acronym, 'department', d2.name
            ))
             FROM notes_recipients nr2
             JOIN sectors s2 ON s2.id = nr2.sector_id
             JOIN departments d2 ON d2.id = s2.department_id
             WHERE nr2.document_id = od.id) as recipients,
            (SELECT COUNT(*)::int FROM notes_openings no
             WHERE no.document_id = od.id) as openings_count
        FROM official_documents od
        JOIN document_types dt ON dt.id = od.document_type_id
        WHERE od.signed_at IS NOT NULL
          AND od.id IN (
              SELECT DISTINCT nr.document_id FROM notes_recipients nr
              WHERE nr.sender_sector_id = ANY($1::uuid[])
          )
        {date_where}
        ORDER BY od.signed_at DESC
        LIMIT ${limit_n} OFFSET ${offset_n}
    """


def _sent_count_sql(date_where: str) -> str:
    return f"""
        SELECT COUNT(DISTINCT od.id)::int as total
        FROM official_documents od
        JOIN notes_recipients nr ON nr.document_id = od.id
        WHERE od.signed_at IS NOT NULL
          AND nr.sender_sector_id = ANY($1::uuid[])
        {date_where}
    """


def _sent_search_paginated_sql(date_where: str, limit_n: int, offset_n: int) -> str:
    return f"""
        SELECT
            od.id, od.official_number, od.reference, od.signed_at,
            od.resume as ai_summary, dt.acronym as document_type,
            (SELECT json_agg(json_build_object(
                'sector_id', nr2.sector_id, 'type', nr2.recipient_type,
                'acronym', s2.acronym, 'department', d2.name
            ))
             FROM notes_recipients nr2
             JOIN sectors s2 ON s2.id = nr2.sector_id
             JOIN departments d2 ON d2.id = s2.department_id
             WHERE nr2.document_id = od.id) as recipients,
            (SELECT COUNT(*)::int FROM notes_openings no
             WHERE no.document_id = od.id) as openings_count
        FROM official_documents od
        JOIN document_types dt ON dt.id = od.document_type_id
        WHERE od.signed_at IS NOT NULL
          AND od.id IN (
              SELECT DISTINCT nr.document_id FROM notes_recipients nr
              WHERE nr.sender_sector_id = ANY($1::uuid[])
          )
          AND (
            od.official_number ILIKE $2
            OR od.reference ILIKE $3
            OR od.content->>'html' ILIKE $4
            OR similarity(LOWER(COALESCE(od.reference, '')), LOWER($5)) > 0.3
          )
        {date_where}
        ORDER BY od.signed_at DESC
        LIMIT ${limit_n} OFFSET ${offset_n}
    """


def _sent_search_count_sql(date_where: str) -> str:
    return f"""
        SELECT COUNT(DISTINCT od.id)::int as total
        FROM official_documents od
        JOIN notes_recipients nr ON nr.document_id = od.id
        WHERE od.signed_at IS NOT NULL
          AND nr.sender_sector_id = ANY($1::uuid[])
          AND (
            od.official_number ILIKE $2
            OR od.reference ILIKE $3
            OR od.content->>'html' ILIKE $4
            OR similarity(LOWER(COALESCE(od.reference, '')), LOWER($5)) > 0.3
          )
        {date_where}
    """


# ---------------------------------------------------------------------------
# Funciones públicas
# ---------------------------------------------------------------------------

async def get_sent_notes(
    sector_id: str,
    *,
    schema_name: str,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    """Obtiene las notas enviadas por un sector (solo oficializadas)."""
    offset = (page - 1) * page_size

    total_row = await fetch_one(get_sent_notes_count_query(), sector_id, schema_name=schema_name)
    total = total_row["total"] if total_row else 0

    notes_raw = await fetch_all(
        get_sent_notes_query(), sector_id, page_size, offset, schema_name=schema_name
    )

    notes = [
        {
            "document_id": str(row["id"]),
            "official_number": row["official_number"],
            "reference": row["reference"],
            "signed_at": row["signed_at"].isoformat() if row["signed_at"] else None,
            "ai_summary": row["ai_summary"],
            "document_type": row["document_type"],
            "recipients": row["recipients"] or [],
            "openings_count": row["openings_count"],
        }
        for row in notes_raw
    ]

    total_pages = (total + page_size - 1) // page_size if total > 0 else 1
    logger.debug(f"[{schema_name}] Sector {sector_id}: {len(notes)} notas enviadas (página {page})")
    return {"notes": notes, "pagination": {"page": page, "page_size": page_size, "total": total, "total_pages": total_pages}}


async def get_received_notes(
    sector_id: str,
    *,
    schema_name: str,
    page: int = 1,
    page_size: int = 20,
    unread_only: bool = False,
) -> Dict[str, Any]:
    """Obtiene las notas recibidas por un sector (solo oficializadas)."""
    offset = (page - 1) * page_size

    total_row = await fetch_one(get_received_notes_count_query(), sector_id, schema_name=schema_name)
    total = total_row["total"] if total_row else 0

    # sector_id pasa 3 veces: para $1, $2 (subconsultas read_status) y $3 (JOIN)
    notes_raw = await fetch_all(
        get_received_notes_query(),
        sector_id, sector_id, sector_id, page_size, offset,
        schema_name=schema_name,
    )

    notes = []
    for row in notes_raw:
        read_status = row["read_status"] or {}
        if unread_only and read_status.get("opened"):
            continue
        notes.append({
            "document_id": str(row["id"]),
            "official_number": row["official_number"],
            "reference": row["reference"],
            "signed_at": row["signed_at"].isoformat() if row["signed_at"] else None,
            "ai_summary": row["ai_summary"],
            "document_type": row["document_type"],
            "recipient_type": row["recipient_type"],
            "sender": {
                "sector_id": str(row["sender_sector_id"]),
                "acronym": row["sender_sector_acronym"],
                "department_name": row["sender_department_name"],
            },
            "read_status": {"opened": read_status.get("opened", False), "opened_at": read_status.get("opened_at")},
        })

    total_pages = (total + page_size - 1) // page_size if total > 0 else 1
    logger.debug(f"[{schema_name}] Sector {sector_id}: {len(notes)} notas recibidas (página {page})")
    return {"notes": notes, "pagination": {"page": page, "page_size": page_size, "total": total, "total_pages": total_pages}}


async def get_received_notes_multi_sector(
    sector_ids: list[str],
    *,
    schema_name: str,
    page: int = 1,
    page_size: int = 20,
    unread_only: bool = False,
    search: str | None = None,
    date_filter: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> Dict[str, Any]:
    """Obtiene las notas recibidas en CUALQUIERA de los sectores dados."""
    if not sector_ids:
        return {"notes": [], "pagination": {"page": page, "page_size": page_size, "total": 0, "total_pages": 1}}

    offset = (page - 1) * page_size

    if search:
        search_pattern = f"%{escape_like(search)}%"
        search_term = search.strip().lower()
        # $1=sector_ids, $2-$5=search params → date start at $6
        date_where, date_params = build_date_filter(date_filter, date_from, date_to, start=6)
        limit_n = 6 + len(date_params)
        offset_n = limit_n + 1
        base = [sector_ids, search_pattern, search_pattern, search_pattern, search_term, *date_params]
        total_row = await fetch_one(_received_search_count_sql(date_where), *base, schema_name=schema_name)
        notes_raw = await fetch_all(_received_search_paginated_sql(date_where, limit_n, offset_n), *base, page_size, offset, schema_name=schema_name)
    else:
        # $1=sector_ids → date start at $2
        date_where, date_params = build_date_filter(date_filter, date_from, date_to, start=2)
        limit_n = 2 + len(date_params)
        offset_n = limit_n + 1
        base = [sector_ids, *date_params]
        total_row = await fetch_one(_received_count_sql(date_where), *base, schema_name=schema_name)
        notes_raw = await fetch_all(_received_paginated_sql(date_where, limit_n, offset_n), *base, page_size, offset, schema_name=schema_name)

    total = total_row["total"] if total_row else 0

    notes = []
    for row in notes_raw:
        read_status = row["read_status"] or {}
        if unread_only and read_status.get("opened"):
            continue
        notes.append({
            "document_id": str(row["id"]),
            "official_number": row["official_number"],
            "reference": row["reference"],
            "signed_at": row["signed_at"].isoformat() if row["signed_at"] else None,
            "ai_summary": row["ai_summary"],
            "document_type": row["document_type"],
            "recipient_type": row["recipient_type"],
            "sender": {
                "sector_id": str(row["sender_sector_id"]),
                "acronym": row["sender_sector_acronym"],
                "department_name": row["sender_department_name"],
            },
            "read_status": {"opened": read_status.get("opened", False), "opened_at": read_status.get("opened_at")},
        })

    total_pages = (total + page_size - 1) // page_size if total > 0 else 1
    logger.debug(
        f"[{schema_name}] Sectores {sector_ids}: {len(notes)} notas recibidas (página {page})"
        f"{' search=' + search if search else ''}"
    )
    return {"notes": notes, "pagination": {"page": page, "page_size": page_size, "total": total, "total_pages": total_pages}}


async def get_sent_notes_multi_sector(
    sector_ids: list[str],
    *,
    schema_name: str,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
    date_filter: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> Dict[str, Any]:
    """Obtiene las notas enviadas desde CUALQUIERA de los sectores dados."""
    if not sector_ids:
        return {"notes": [], "pagination": {"page": page, "page_size": page_size, "total": 0, "total_pages": 1}}

    offset = (page - 1) * page_size

    if search:
        search_pattern = f"%{escape_like(search)}%"
        search_term = search.strip().lower()
        date_where, date_params = build_date_filter(date_filter, date_from, date_to, start=6)
        limit_n = 6 + len(date_params)
        offset_n = limit_n + 1
        base = [sector_ids, search_pattern, search_pattern, search_pattern, search_term, *date_params]
        total_row = await fetch_one(_sent_search_count_sql(date_where), *base, schema_name=schema_name)
        notes_raw = await fetch_all(_sent_search_paginated_sql(date_where, limit_n, offset_n), *base, page_size, offset, schema_name=schema_name)
    else:
        date_where, date_params = build_date_filter(date_filter, date_from, date_to, start=2)
        limit_n = 2 + len(date_params)
        offset_n = limit_n + 1
        base = [sector_ids, *date_params]
        total_row = await fetch_one(_sent_count_sql(date_where), *base, schema_name=schema_name)
        notes_raw = await fetch_all(_sent_paginated_sql(date_where, limit_n, offset_n), *base, page_size, offset, schema_name=schema_name)

    total = total_row["total"] if total_row else 0

    notes = [
        {
            "document_id": str(row["id"]),
            "official_number": row["official_number"],
            "reference": row["reference"],
            "signed_at": row["signed_at"].isoformat() if row["signed_at"] else None,
            "ai_summary": row["ai_summary"],
            "document_type": row["document_type"],
            "recipients": row["recipients"] or [],
            "openings_count": row["openings_count"],
        }
        for row in notes_raw
    ]

    total_pages = (total + page_size - 1) // page_size if total > 0 else 1
    logger.debug(
        f"[{schema_name}] Sectores {sector_ids}: {len(notes)} notas enviadas (página {page})"
        f"{' search=' + search if search else ''}"
    )
    return {"notes": notes, "pagination": {"page": page, "page_size": page_size, "total": total, "total_pages": total_pages}}


async def get_archived_notes_multi_sector(
    sector_ids: list[str],
    *,
    schema_name: str,
    page: int = 1,
    page_size: int = 20,
    search: str | None = None,
) -> Dict[str, Any]:
    """Obtiene las notas ARCHIVADAS en CUALQUIERA de los sectores dados."""
    if not sector_ids:
        return {"notes": [], "pagination": {"page": page, "page_size": page_size, "total": 0, "total_pages": 1}}

    offset = (page - 1) * page_size

    if search:
        search_pattern = f"%{escape_like(search)}%"
        search_term = search.strip().lower()
        total_row = await fetch_one(
            get_archived_notes_multi_sector_search_count_query(),
            sector_ids, search_pattern, search_pattern, search_pattern, search_term,
            schema_name=schema_name,
        )
        notes_raw = await fetch_all(
            get_archived_notes_multi_sector_search_query(),
            sector_ids, search_pattern, search_pattern, search_pattern, search_term, page_size, offset,
            schema_name=schema_name,
        )
    else:
        total_row = await fetch_one(
            get_archived_notes_multi_sector_count_query(), sector_ids, schema_name=schema_name
        )
        notes_raw = await fetch_all(
            get_archived_notes_multi_sector_paginated_query(), sector_ids, page_size, offset, schema_name=schema_name
        )

    total = total_row["total"] if total_row else 0

    notes = []
    for row in notes_raw:
        read_status = row["read_status"] or {}
        notes.append({
            "document_id": str(row["id"]),
            "official_number": row["official_number"],
            "reference": row["reference"],
            "signed_at": row["signed_at"].isoformat() if row["signed_at"] else None,
            "ai_summary": row["ai_summary"],
            "document_type": row["document_type"],
            "recipient_type": row["recipient_type"],
            "is_archived": row["is_archived"],
            "archived_at": row["archived_at"].isoformat() if row["archived_at"] else None,
            "sender": {
                "sector_id": str(row["sender_sector_id"]),
                "acronym": row["sender_sector_acronym"],
                "department_name": row["sender_department_name"],
            },
            "read_status": {"opened": read_status.get("opened", False), "opened_at": read_status.get("opened_at")},
        })

    total_pages = (total + page_size - 1) // page_size if total > 0 else 1
    logger.debug(
        f"[{schema_name}] Sectores {sector_ids}: {len(notes)} notas archivadas (página {page})"
        f"{' search=' + search if search else ''}"
    )
    return {"notes": notes, "pagination": {"page": page, "page_size": page_size, "total": total, "total_pages": total_pages}}
