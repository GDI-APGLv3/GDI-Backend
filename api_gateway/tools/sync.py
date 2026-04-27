"""
Sync tools para backup incremental.

Provee catálogo de tablas y datos incrementales por updated_at.
"""
import logging
from datetime import datetime, timezone
from psycopg2 import sql

logger = logging.getLogger(__name__)

# 23 tablas sincronizables con su campo cursor
SYNC_TABLES = {
    "departments": "updated_at",
    "sectors": "updated_at",
    "ranks": "updated_at",
    "city_seals": "updated_at",
    "users": "updated_at",
    "user_roles": "updated_at",
    "user_seals": "updated_at",
    "user_sector_permissions": "updated_at",
    "estado_users": "updated_at",
    "document_types": "updated_at",
    "document_types_allowed_by_rank": "updated_at",
    "enabled_document_types_by_sector": "updated_at",
    "cases": "updated_at",
    "case_movements": "updated_at",
    "case_templates": "updated_at",
    "case_template_allowed_departments": "updated_at",
    "case_official_documents": "updated_at",
    "case_proposed_documents": "updated_at",
    "official_documents": "updated_at",
    "document_signers": "updated_at",
    "document_rejections": "updated_at",
    "notes_recipients": "updated_at",
    "notes_openings": "updated_at",
}

# users: solo columnas seguras (sin auth_id, password, tokens, etc)
USERS_COLUMNS = "id, full_name, email, sector_id, updated_at"
OFFICIAL_DOCUMENTS_COLUMNS = "id, document_type_id, reference, official_number, year, department_id, numerator_id, signed_at, signers, global_sequence, signer_sector_ids, resume, created_at, updated_at"


def get_sync_catalog(*, schema_name: str) -> dict:
    """
    Catálogo de tablas sincronizables con count de filas.

    Usa una sola query UNION ALL para obtener los 23 counts
    en un solo roundtrip (evita bloquear el event loop).

    Returns:
        dict con server_time, schema_name, tables[]
    """
    from database import execute_query

    # Una sola query: 23 counts via UNION ALL (SEC-09: parameterized identifiers)
    unions = sql.SQL(" UNION ALL ").join(
        sql.SQL("SELECT {} as name, COUNT(*) as total FROM {}.{}").format(
            sql.Literal(table),
            sql.Identifier(schema_name),
            sql.Identifier(table)
        )
        for table in SYNC_TABLES
    )

    try:
        rows = execute_query(unions, (), schema_name="public") or []
        counts = {r["name"]: r["total"] for r in rows}
    except Exception as e:
        logger.warning(f"[Sync] Error en catalog query: {e}")
        counts = {}

    tables = [
        {
            "name": table,
            "cursor_field": cursor_field,
            "total_rows": counts.get(table, 0)
        }
        for table, cursor_field in SYNC_TABLES.items()
    ]

    return {
        "server_time": datetime.now(timezone.utc).isoformat(),
        "schema_name": schema_name,
        "tables": tables
    }


def get_sync_data(table: str, since: str, page: int, page_size: int, *, schema_name: str) -> dict:
    """
    Datos incrementales de una tabla desde `since`.

    Usa LIMIT+1 pattern para has_more (sin COUNT extra).

    Args:
        table: Nombre de tabla (debe estar en SYNC_TABLES)
        since: ISO 8601 timestamp
        page: Página (1-based)
        page_size: Filas por página (max 100)
        schema_name: Schema del tenant

    Returns:
        dict con server_time, table, rows[], has_more, count

    Raises:
        ValueError: Si tabla no está en SYNC_TABLES
    """
    from database import execute_query

    if table not in SYNC_TABLES:
        raise ValueError(f"Tabla '{table}' no es sincronizable. Tablas válidas: {', '.join(sorted(SYNC_TABLES.keys()))}")

    cursor_field = SYNC_TABLES[table]
    page_size = min(max(page_size, 1), 100)
    offset = (max(page, 1) - 1) * page_size

    # Columnas: users tiene whitelist, el resto SELECT *
    if table == "users":
        columns = USERS_COLUMNS
    elif table == "official_documents":
        columns = OFFICIAL_DOCUMENTS_COLUMNS
    else:
        columns = "*"

    # LIMIT+1 para detectar has_more sin COUNT extra (SEC-09: parameterized identifiers)
    query = sql.SQL("""
        SELECT {columns}
        FROM {schema}.{table}
        WHERE {cursor} >= %s
        ORDER BY {cursor} ASC
        LIMIT %s OFFSET %s
    """).format(
        columns=sql.SQL("*") if columns == "*" else sql.SQL(", ").join(
            sql.Identifier(c.strip()) for c in columns.split(",")
        ),
        schema=sql.Identifier(schema_name),
        table=sql.Identifier(table),
        cursor=sql.Identifier(cursor_field)
    )

    rows = execute_query(
        query,
        (since, page_size + 1, offset),
        schema_name="public"
    )

    if rows is None:
        rows = []

    has_more = len(rows) > page_size
    if has_more:
        rows = rows[:page_size]

    return {
        "server_time": datetime.now(timezone.utc).isoformat(),
        "table": table,
        "cursor_field": cursor_field,
        "since": since,
        "page": page,
        "page_size": page_size,
        "has_more": has_more,
        "count": len(rows),
        "rows": rows
    }
