from typing import Optional

from database import fetch_all, fetch_one
from shared.logging import get_logger

from api_gateway.public_info.sanitize import whitelist_fields, build_public_pdf_url, sanitize_field_names
from api_gateway.public_info.muni import get_bucket_publico

logger = get_logger(__name__)

MAX_PAGE_SIZE_PUBLIC = 25
DEFAULT_VISIBLE_STATES = ["Activo"]


async def get_public_families(*, schema_name: str, code: Optional[str] = None) -> list[dict]:
    sql = """
        SELECT id, code, name, description, public_config
        FROM registry_families
        WHERE is_active = true AND is_public = true
    """
    params: list = []
    if code:
        sql += " AND code = $1"
        params.append(code.upper())
    rows = await fetch_all(sql, *params, schema_name=schema_name)
    return [dict(r) for r in (rows or [])]


async def list_registries_public(*, schema_name: str) -> dict:
    families = await get_public_families(schema_name=schema_name)
    registries = []
    for f in families:
        cfg = f.get("public_config") or {}
        registries.append({
            "code": f["code"],
            "name": f["name"],
            "description": f.get("description"),
            "fields": cfg.get("fields") or [],
        })
    return {"registries": registries, "total": len(registries)}


def _resolve_visible_states(cfg: dict) -> list:
    states = cfg.get("visible_states")
    return DEFAULT_VISIBLE_STATES if states is None else states


def _visible_states(family: dict) -> list:
    cfg = family.get("public_config") or {}
    return _resolve_visible_states(cfg)


async def list_records_public(
    *,
    schema_name: str,
    registry_code: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), MAX_PAGE_SIZE_PUBLIC)

    families = await get_public_families(schema_name=schema_name, code=registry_code)
    if not families:
        return {"records": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 1}

    families_by_id = {f["id"]: f for f in families}
    params: list = []

    state_clauses = []
    for f in families:
        idx_fam = len(params) + 1
        params.append(f["id"])
        idx_states = len(params) + 1
        params.append(_visible_states(f))
        state_clauses.append(f"(r.registry_family_id = ${idx_fam} AND r.state = ANY(${idx_states}::text[]))")
    where_clauses = ["(" + " OR ".join(state_clauses) + ")"]

    if search and len(search) >= 2:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"

        idx = len(params) + 1
        params.append(pattern)
        search_ors = [f"r.record_number ILIKE ${idx}"]
        idx = len(params) + 1
        params.append(pattern)
        search_ors.append(f"r.display_name ILIKE ${idx}")

        for f in families:
            cfg = f.get("public_config") or {}
            fields = sanitize_field_names(cfg.get("fields") or [])
            if not fields:
                continue
            field_ors = []
            for field in fields:
                idx = len(params) + 1
                params.append(pattern)
                field_ors.append(f"r.data->>'{field}' ILIKE ${idx}")
            idx_fam = len(params) + 1
            params.append(f["id"])
            search_ors.append(f"(r.registry_family_id = ${idx_fam} AND ({' OR '.join(field_ors)}))")

        where_clauses.append("(" + " OR ".join(search_ors) + ")")

    where_sql = " AND ".join(where_clauses)

    count_sql = f"SELECT COUNT(*) as total FROM records r WHERE {where_sql}"
    count_row = await fetch_one(count_sql, *params, schema_name=schema_name)
    total = count_row["total"] if count_row else 0

    offset = (page - 1) * page_size
    limit_idx = len(params) + 1
    offset_idx = len(params) + 2
    list_sql = f"""
        SELECT r.id, r.record_number, r.display_name, r.state, r.data, r.registry_family_id
        FROM records r
        WHERE {where_sql}
        ORDER BY r.created_at DESC
        LIMIT ${limit_idx} OFFSET ${offset_idx}
    """
    rows = await fetch_all(list_sql, *params, page_size, offset, schema_name=schema_name)

    records = []
    for row in (rows or []):
        fam = families_by_id.get(row["registry_family_id"])
        cfg = (fam.get("public_config") or {}) if fam else {}
        fields = cfg.get("fields") or []
        records.append({
            "record_number": row["record_number"],
            "display_name": row["display_name"],
            "state": row["state"],
            "registry_code": fam["code"] if fam else None,
            "fields": whitelist_fields(row["data"] or {}, fields),
        })

    total_pages = max(1, -(-total // page_size))
    return {
        "records": records,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
    }


async def get_record_public(*, schema_name: str, record_number: str, muni: str) -> Optional[dict]:
    row = await fetch_one(
        """
        SELECT r.id, r.record_number, r.display_name, r.state, r.data,
               rf.id AS family_id, rf.code AS registry_code, rf.name AS registry_name,
               rf.is_public AS family_is_public, rf.public_config
        FROM records r
        JOIN registry_families rf ON rf.id = r.registry_family_id
        WHERE r.record_number = $1
        """,
        record_number,
        schema_name=schema_name,
    )
    if not row or not row["family_is_public"]:
        return None

    cfg = row["public_config"] or {}
    visible_states = _resolve_visible_states(cfg)
    if row["state"] not in visible_states:
        return None

    fields = cfg.get("fields") or []
    result = {
        "record_number": row["record_number"],
        "display_name": row["display_name"],
        "state": row["state"],
        "registry": {"code": row["registry_code"], "name": row["registry_name"]},
        "fields": whitelist_fields(row["data"] or {}, fields),
    }

    if cfg.get("show_documents"):
        result["documents"] = await _get_linked_documents_public(record_id=row["id"], schema_name=schema_name, muni=muni)
    if cfg.get("show_cases"):
        result["cases"] = await _get_linked_cases_public(record_id=row["id"], schema_name=schema_name)
    if cfg.get("show_related_records"):
        result["related_records"] = await _get_related_records_public(record_id=row["id"], schema_name=schema_name)

    return result


async def _get_linked_documents_public(*, record_id: str, schema_name: str, muni: str) -> list[dict]:
    rows = await fetch_all(
        """
        SELECT od.official_number, od.reference, od.resume, dt.visibility,
               -- GDI-126: el UUID se expone SOLO para docs publicos, y se
               -- decide en el SQL (no en Python) para que la fuente de datos
               -- nunca contenga UUIDs de docs no-publicos -- ningun refactor
               -- futuro puede filtrarlos por accidente (D16: filtrar en el
               -- WHERE/proyeccion, no post-fetch). Con ese UUID la app del
               -- muni pide el contenido en GET /public/{muni}/documents/{id}/content.
               CASE WHEN dt.visibility = 'publico' THEN od.id::text ELSE NULL END AS document_id
        FROM record_document_links rdl
        JOIN official_documents od ON od.id = rdl.document_id
        JOIN document_types dt ON dt.id = od.document_type_id
        WHERE rdl.record_id = $1
          AND dt.visibility != 'reservado'
        ORDER BY od.official_number
        """,
        record_id,
        schema_name=schema_name,
    )
    bucket_publico = await get_bucket_publico(schema_name=schema_name)
    docs = []
    for row in (rows or []):
        es_publico = row["visibility"] == "publico"
        pdf_url = None
        if es_publico and bucket_publico:
            pdf_url = build_public_pdf_url(muni, row["document_id"])
        docs.append({
            "official_number": row["official_number"],
            "reference": row["reference"],
            "document_id": row["document_id"],
            "pdf_url": pdf_url,
            "resume": row["resume"] if es_publico else None,
        })
    return docs


async def _get_linked_cases_public(*, record_id: str, schema_name: str) -> list[dict]:
    rows = await fetch_all(
        """
        SELECT c.case_number, c.reference
        FROM record_case_links rcl
        JOIN cases c ON c.id = rcl.case_id
        JOIN case_templates ct ON ct.id = c.case_template_id
        WHERE rcl.record_id = $1
          AND NOT ct.is_reserved
        ORDER BY c.case_number
        """,
        record_id,
        schema_name=schema_name,
    )
    return [{"case_number": r["case_number"], "reference": r["reference"]} for r in (rows or [])]


async def _get_related_records_public(*, record_id: str, schema_name: str) -> list[dict]:
    rows = await fetch_all(
        """
        SELECT
            r2.record_number,
            r2.display_name,
            r2.state,
            r2.resume,
            rf2.is_public AS target_is_public,
            -- Semantica igual a queries.py: COALESCE solo actua sobre SQL
            -- NULL (clave ausente), no sobre un `[]` explicito -- ese caso
            -- sigue viajando como `[]` hasta Python, donde NO hay que
            -- volver a "corregirlo" con `or` (ver _resolve_visible_states).
            COALESCE(rf2.public_config -> 'visible_states', '["Activo"]'::jsonb) AS target_visible_states
        FROM record_relations rr
        JOIN records r2 ON r2.id = CASE
            WHEN rr.source_record_id = $1 THEN rr.target_record_id
            ELSE rr.source_record_id
        END
        JOIN registry_families rf2 ON rf2.id = r2.registry_family_id
        WHERE rr.source_record_id = $1 OR rr.target_record_id = $1
        """,
        record_id,
        schema_name=schema_name,
    )
    result = []
    for r in (rows or []):
        visible_states = r["target_visible_states"]
        if visible_states is None:
            visible_states = ["Activo"]
        is_pub = bool(r["target_is_public"]) and r["state"] in visible_states
        result.append({
            "record_number": r["record_number"] if is_pub else None,
            "display_name": r["display_name"],
            "linked": is_pub,
            "resume": r["resume"] if is_pub else None,
        })
    return result
