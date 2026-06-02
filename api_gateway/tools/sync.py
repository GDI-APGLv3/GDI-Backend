"""
Sync tools para backup incremental.

Provee catálogo de tablas y datos incrementales por updated_at.
"""
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def _parse_since(since: str) -> datetime:
    """
    Convierte el cursor ISO 8601 (string) a datetime aware.

    asyncpg es estricto con tipos: para comparar contra una columna timestamptz
    exige un objeto datetime, NO un string (un cast $1::timestamptz tampoco alcanza,
    asyncpg infiere el tipo del placeholder y rechaza el str antes de enviarlo).
    Acepta sufijo 'Z' y normaliza naive -> UTC.
    """
    s = (since or "").strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt

# 33 tablas sincronizables con su campo cursor
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
    "case_responsibles": "updated_at",
    "case_favorites": "updated_at",
    "official_documents": "updated_at",
    "document_signers": "updated_at",
    "document_rejections": "updated_at",
    "notes_recipients": "updated_at",
    "notes_openings": "updated_at",
    "memo_recipients": "updated_at",
    "registry_families": "updated_at",
    "registry_family_permissions": "updated_at",
    "records": "updated_at",
    "record_history": "updated_at",
    "record_relations": "updated_at",
    "record_case_links": "updated_at",
    "record_document_links": "updated_at",
}

# users: solo columnas seguras (sin auth_id, password, tokens, etc)
USERS_COLUMNS = "id, full_name, email, sector_id, updated_at"
OFFICIAL_DOCUMENTS_COLUMNS = "id, document_type_id, reference, official_number, year, department_id, numerator_id, signed_at, signers, global_sequence, signer_sector_ids, resume, created_at, updated_at"


async def get_sync_catalog(*, schema_name: str) -> dict:
    """
    Catálogo de tablas sincronizables con count de filas.

    Usa una sola query UNION ALL para obtener los 33 counts
    en un solo roundtrip (evita bloquear el event loop).

    Returns:
        dict con server_time, schema_name, tables[]
    """
    from database import fetch_all

    # Una sola query: 33 counts via UNION ALL — identifiers validados contra SYNC_TABLES whitelist
    # Comillas dobles obligatorias: los schemas tenant empiezan con dígito (ej: 100_test),
    # identificador sin comillas -> syntax error en Postgres.
    unions = " UNION ALL ".join(
        f'SELECT \'{table}\' as name, COUNT(*) as total FROM "{schema_name}"."{table}"'
        for table in SYNC_TABLES
    )

    try:
        rows = await fetch_all(unions, schema_name="public") or []
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


async def get_sync_data(table: str, since: str, page: int, page_size: int, *, schema_name: str) -> dict:
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
    from database import fetch_all

    if table not in SYNC_TABLES:
        raise ValueError(f"Tabla '{table}' no es sincronizable. Tablas válidas: {', '.join(sorted(SYNC_TABLES.keys()))}")

    cursor_field = SYNC_TABLES[table]
    page_size = min(max(page_size, 1), 100)
    offset = (max(page, 1) - 1) * page_size

    # Columnas: users y official_documents tienen whitelist, el resto SELECT *
    if table == "users":
        cols_sql = USERS_COLUMNS
    elif table == "official_documents":
        cols_sql = OFFICIAL_DOCUMENTS_COLUMNS
    else:
        cols_sql = "*"

    # official_documents: excluir RESERVED (nunca firmados) y CANCELLED (pueden borrarse → datos fantasma)
    # OR IS NULL mantiene backward compat con docs creados antes del refactor FixNumber v2
    extra_filter = ""
    if table == "official_documents":
        extra_filter = "AND (reservation_status = 'CONFIRMED' OR reservation_status IS NULL)"

    # LIMIT+1 para detectar has_more sin COUNT extra — identifiers validados contra SYNC_TABLES whitelist
    # $1::timestamptz: asyncpg es estricto con tipos. Sin el cast infiere $1 como timestamptz
    # y exige un datetime; el cast permite pasar el cursor como string ISO (text -> timestamptz).
    query = f"""
        SELECT {cols_sql}
        FROM "{schema_name}"."{table}"
        WHERE {cursor_field} >= $1
        {extra_filter}
        ORDER BY {cursor_field} ASC
        LIMIT $2 OFFSET $3
    """

    rows = await fetch_all(query, _parse_since(since), page_size + 1, offset, schema_name="public")

    if rows is None:
        rows = []

    has_more = len(rows) > page_size
    if has_more:
        rows = rows[:page_size]

    rows = [dict(row) for row in rows]

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


async def get_sync_documents(since: str, page: int, page_size: int, *, schema_name: str) -> dict:
    """
    PDFs de documentos oficiales firmados desde `since`, con presigned URLs de R2.

    Solo incluye documentos con signed_at IS NOT NULL y reservation_status CONFIRMED (o NULL
    para docs pre-FixNumber). Las URLs expiran en 600 segundos — el cliente debe descargar
    dentro de ese plazo; si expiran, repite el request para obtener URLs nuevas.

    Args:
        since: ISO 8601 timestamp cursor
        page: Página (1-based)
        page_size: Documentos por página (max 100)
        schema_name: Schema del tenant

    Returns:
        dict con server_time, documents[], has_more, count
    """
    from database import fetch_all
    from services.storage.cloudflare import get_tenant_r2_client

    page_size = min(max(page_size, 1), 100)
    offset = (max(page, 1) - 1) * page_size

    query = f"""
        SELECT id, official_number, signed_at
        FROM "{schema_name}".official_documents
        WHERE updated_at >= $1
          AND signed_at IS NOT NULL
          AND (reservation_status = 'CONFIRMED' OR reservation_status IS NULL)
        ORDER BY updated_at ASC
        LIMIT $2 OFFSET $3
    """

    rows = await fetch_all(query, _parse_since(since), page_size + 1, offset, schema_name="public")
    if rows is None:
        rows = []

    has_more = len(rows) > page_size
    if has_more:
        rows = rows[:page_size]

    r2 = await get_tenant_r2_client(schema_name=schema_name)
    pdf_url_expires_at = (datetime.now(timezone.utc) + timedelta(seconds=600)).isoformat()

    documents = []
    for row in rows:
        pdf_url = r2.get_oficial_url(row["official_number"])
        documents.append({
            "id": str(row["id"]),
            "official_number": row["official_number"],
            "signed_at": str(row["signed_at"]) if row["signed_at"] else None,
            "pdf_download_url": pdf_url,
            "pdf_url_expires_at": pdf_url_expires_at,
        })

    return {
        "server_time": datetime.now(timezone.utc).isoformat(),
        "since": since,
        "page": page,
        "page_size": page_size,
        "has_more": has_more,
        "count": len(documents),
        "documents": documents,
    }
