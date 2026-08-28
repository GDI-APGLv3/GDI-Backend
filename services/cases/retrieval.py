
from typing import List, Dict, Optional, Any
import hashlib
import json
import os
import asyncio
from shared.logging import get_logger
import re

from database import fetch_all
from services.case_queries import get_cases_list_query, get_cases_count_query, get_cases_summary_query
from services.cache import get_cached, invalidate_cache
from config.constants import (
    ACCESS_REASON_ADMIN,
    ACCESS_REASON_ASSIGNED,
    DATE_FILTER_OPTIONS,
    DEFAULT_PAGE_SIZE,
    CACHE_TTL_COUNTS,
    CASE_INVALID_DATE_FILTER_ERROR,
    CASE_SEARCH_MIN_CHARS
)
from shared.exceptions import ValidationError, BusinessLogicError

logger = get_logger(__name__)


def _cases_count_cache_ttl() -> int:
    try:
        return int(os.getenv("CASE_SEARCH_COUNT_CACHE_TTL", str(CACHE_TTL_COUNTS)))
    except (TypeError, ValueError):
        return CACHE_TTL_COUNTS


def _build_cases_count_cache_key(
    schema_name: str,
    view: Optional[str],
    count_params: tuple,
) -> str:
    raw = f"{schema_name}|{view or 'none'}|{repr(count_params)}"
    return f"cases_count:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:20]}"

def _is_uuid(value: str) -> bool:
    uuid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    return bool(re.match(uuid_pattern, value.lower().strip()))


def _looks_like_case_number(value: str) -> bool:
    case_number_pattern = r'^[A-Za-z]+-\d{4}-\d+(-[A-Za-z]+)?\s*$'
    return bool(re.match(case_number_pattern, value.strip()))


async def _resolve_trata_filter(trata_filter: str, *, schema_name: str) -> List[str]:
    if not trata_filter:
        return []

    if _is_uuid(trata_filter):
        return [trata_filter]

    acronyms = [a.strip().upper() for a in trata_filter.split(',') if a.strip()]
    if not acronyms:
        return []

    query = """
        SELECT id FROM case_templates
        WHERE UPPER(acronym) = ANY($1) AND is_active = true
    """
    results = await fetch_all(query, acronyms, schema_name=schema_name)
    return [str(r['id']) for r in results] if results else []


async def _resolve_sector_filter(sector_filter: str, *, schema_name: str) -> Optional[str]:
    if not sector_filter:
        return None

    if _is_uuid(sector_filter):
        return sector_filter

    if '#' in sector_filter:
        parts = sector_filter.split('#')
        if len(parts) == 2:
            dept_acronym, sector_acronym = parts[0].strip().upper(), parts[1].strip().upper()
            query = """
                SELECT s.id
                FROM sectors s
                JOIN departments d ON s.department_id = d.id
                WHERE UPPER(d.acronym) = $1
                AND UPPER(s.acronym) = $2
                AND s.is_active = true
            """
            results = await fetch_all(query, dept_acronym, sector_acronym, schema_name=schema_name)
            if results:
                return str(results[0]['id'])

    query = """
        SELECT s.id FROM sectors s
        WHERE UPPER(s.acronym) = $1 AND s.is_active = true
        LIMIT 1
    """
    results = await fetch_all(query, sector_filter.strip().upper(), schema_name=schema_name)
    return str(results[0]['id']) if results else None


def _build_where_conditions(
    search_filter: Optional[str],
    resolved_sector: Optional[str],
    resolved_tratas: List[str],
    date_filter: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str],
    is_global_search: bool = False,
    *,
    sector_param: int = 1,
    user_id_param: int = 2,
    param_start: int = 3,
) -> tuple:
    where_conditions = []
    idx = param_start

    sector_conditions = f"""
        (
            EXISTS (
                SELECT 1 FROM case_movements cm
                WHERE cm.case_id = c.id
                AND cm.assigned_sector_id = ANY(${sector_param}::uuid[])
                AND cm.is_active = true
            )
            OR
            EXISTS (
                SELECT 1 FROM case_movements cm
                WHERE cm.case_id = c.id
                AND cm.type = 'transfer'
                AND cm.is_active = false
                AND cm.admin_sector_id = ANY(${sector_param}::uuid[])
                AND cm.closed_at = (
                    SELECT MAX(cm2.closed_at)
                    FROM case_movements cm2
                    WHERE cm2.case_id = c.id
                    AND cm2.type = 'transfer'
                    AND cm2.is_active = false
                )
            )
            OR
            (
                EXISTS (
                    SELECT 1 FROM case_movements cm
                    WHERE cm.case_id = c.id
                    AND cm.type = 'creation'
                    AND cm.admin_sector_id = ANY(${sector_param}::uuid[])
                )
                AND NOT EXISTS (
                    SELECT 1 FROM case_movements cm
                    WHERE cm.case_id = c.id
                    AND cm.type = 'transfer'
                )
            )
        )
    """

    non_reserved_branch = sector_conditions if not is_global_search else "TRUE"

    from services.cases.reserved_predicate import build_reserved_or_exists
    reserved_branch = "(\n" + build_reserved_or_exists(
        case_ref="c.id",
        user_ph=f"${user_id_param}",
    ) + "\n)"

    where_conditions.append(f"""
        (
            (NOT ct.is_reserved AND ({non_reserved_branch}))
            OR
            (ct.is_reserved AND ({reserved_branch}))
        )
    """)

    where_conditions.append("c.status = 'active'")

    if search_filter:
        where_conditions.append(f"""(
            public.immutable_unaccent(LOWER(c.reference)) LIKE public.immutable_unaccent(LOWER(${idx}))
            OR public.immutable_unaccent(LOWER(c.case_number)) LIKE public.immutable_unaccent(LOWER(${idx + 1}))
            OR EXISTS (
                SELECT 1 FROM case_official_documents cod
                JOIN official_documents od ON cod.official_document_id = od.id
                WHERE cod.case_id = c.id
                AND cod.is_active = true
                AND od.signed_at IS NOT NULL
                AND (
                    public.immutable_unaccent(LOWER(COALESCE(od.official_number, ''))) LIKE public.immutable_unaccent(LOWER(${idx + 2}))
                    OR public.immutable_unaccent(LOWER(od.reference)) LIKE public.immutable_unaccent(LOWER(${idx + 3}))
                )
            )
        )""")
        idx += 4

    if resolved_sector:
        where_conditions.append(f"""
            EXISTS (
                SELECT 1 FROM case_movements cm_sector
                WHERE cm_sector.case_id = c.id
                AND cm_sector.assigned_sector_id = ${idx}
                AND cm_sector.is_active = true
            )
        """)
        idx += 1

    if resolved_tratas:
        where_conditions.append(f"""
            c.case_template_id = ANY(${idx}::uuid[])
        """)
        idx += 1

    if date_filter:
        date_condition = _get_date_filter_condition(date_filter)
        if date_condition:
            where_conditions.append(date_condition)

    if date_from:
        where_conditions.append(f"""DATE(GREATEST(
            COALESCE(
                (SELECT MAX(cm.created_at) FROM case_movements cm WHERE cm.case_id = c.id),
                c.created_at
            ),
            COALESCE(
                (SELECT MAX(cod.linking_date) FROM case_official_documents cod
                 WHERE cod.case_id = c.id AND cod.is_active = true),
                c.created_at
            )
        )) >= ${idx}""")
        idx += 1

    if date_to:
        where_conditions.append(f"""DATE(GREATEST(
            COALESCE(
                (SELECT MAX(cm.created_at) FROM case_movements cm WHERE cm.case_id = c.id),
                c.created_at
            ),
            COALESCE(
                (SELECT MAX(cod.linking_date) FROM case_official_documents cod
                 WHERE cod.case_id = c.id AND cod.is_active = true),
                c.created_at
            )
        )) <= ${idx}""")
        idx += 1

    return "WHERE " + " AND ".join(where_conditions), idx


def _get_date_filter_condition(date_filter: str) -> Optional[str]:
    date_conditions = {
        "hoy": """DATE(GREATEST(
            COALESCE((SELECT MAX(cm.created_at) FROM case_movements cm WHERE cm.case_id = c.id), c.created_at),
            COALESCE((SELECT MAX(cod.linking_date) FROM case_official_documents cod WHERE cod.case_id = c.id AND cod.is_active = true), c.created_at)
        )) = CURRENT_DATE""",
        "ayer": """DATE(GREATEST(
            COALESCE((SELECT MAX(cm.created_at) FROM case_movements cm WHERE cm.case_id = c.id), c.created_at),
            COALESCE((SELECT MAX(cod.linking_date) FROM case_official_documents cod WHERE cod.case_id = c.id AND cod.is_active = true), c.created_at)
        )) = CURRENT_DATE - INTERVAL '1 day'""",
        "ultimos_7_dias": """GREATEST(
            COALESCE((SELECT MAX(cm.created_at) FROM case_movements cm WHERE cm.case_id = c.id), c.created_at),
            COALESCE((SELECT MAX(cod.linking_date) FROM case_official_documents cod WHERE cod.case_id = c.id AND cod.is_active = true), c.created_at)
        ) >= CURRENT_DATE - INTERVAL '7 days'""",
        "ultimos_30_dias": """GREATEST(
            COALESCE((SELECT MAX(cm.created_at) FROM case_movements cm WHERE cm.case_id = c.id), c.created_at),
            COALESCE((SELECT MAX(cod.linking_date) FROM case_official_documents cod WHERE cod.case_id = c.id AND cod.is_active = true), c.created_at)
        ) >= CURRENT_DATE - INTERVAL '30 days'"""
    }
    return date_conditions.get(date_filter)


def _build_where_params(
    search_filter: Optional[str],
    resolved_sector: Optional[str],
    resolved_tratas: List[str],
    date_from: Optional[str],
    date_to: Optional[str],
) -> list:
    params = []

    if search_filter:
        search_pattern = f"%{search_filter.lower()}%"
        params.extend([search_pattern, search_pattern,
                       search_pattern, search_pattern])

    if resolved_sector:
        params.append(resolved_sector)

    if resolved_tratas:
        params.append(resolved_tratas)

    if date_from:
        params.append(date_from)

    if date_to:
        params.append(date_to)

    return params


def _determine_access_reason(row: Dict[str, Any]) -> str:
    if row['is_admin_by_transfer'] or row['is_admin_by_creation']:
        return ACCESS_REASON_ADMIN
    return ACCESS_REASON_ASSIGNED


def _build_case_response(row: Dict[str, Any], *, schema_name: str) -> Dict[str, Any]:
    access_reason = _determine_access_reason(row)

    admin_sector = None
    if row['admin_sector_acronym'] and row['admin_sector_department']:
        admin_sector = {
            "acronym": row['admin_sector_acronym'],
            "department": row['admin_sector_department'],
            "sector_color": row['admin_sector_color'],
        }

    assigned_sectors_raw = row['assigned_sectors_json'] or []

    if isinstance(assigned_sectors_raw, str):
        assigned_sectors_raw = json.loads(assigned_sectors_raw) if assigned_sectors_raw else []

    assigned_sectors = [
        {
            "acronym": asg['sector_acronym'],
            "department": asg['department_name'],
            "sector_color": asg.get('sector_color'),
        }
        for asg in assigned_sectors_raw
    ]

    responsibles_raw = row['responsibles_json'] or []
    if isinstance(responsibles_raw, str):
        responsibles_raw = json.loads(responsibles_raw) if responsibles_raw else []

    responsibles = [
        {
            "sector_acronym": r['sector_acronym'],
            "user_id": r['user_id'],
            "full_name": r['full_name'],
            "profile_picture_url": r.get('profile_picture_url'),
            "type": r['type'],
        }
        for r in responsibles_raw
    ]

    return {
        "id": row['id'],
        "case_number": row['case_number'],
        "reference": row['reference'],
        "last_modified_at": row['last_modified_at'],
        "case_type": {
            "name": row['type_name'],
            "acronym": row['case_type'],
            "is_reserved": bool(row['case_type_is_reserved']),
        },
        "access_reason": access_reason,
        "admin_sector": admin_sector,
        "assigned_sectors": assigned_sectors,
        "responsibles": responsibles,
        "short_ai_summary": row['short_ai_summary'],
        "ai_summary": row['ai_summary'],
        "is_favorite": bool(row['is_favorite']),
    }


async def _execute_count(count_query: str, where_params: list, schema_name: str) -> int:
    result = await fetch_all(count_query, *where_params, schema_name=schema_name)
    return result[0]['total_count'] if result else 0


from services.shared.sector_utils import get_user_sector_ids as _get_user_sector_ids


def _build_view_clause(
    view: Optional[str],
    user_id: str,
    schema_name: str,
    user_id_param_idx: int,
) -> tuple:
    if not view:
        return "", []

    qs = f'"{schema_name}"'
    uid_ph = f"${user_id_param_idx}"

    if view == "asignado":
        return (
            f"""
            INNER JOIN (
                SELECT DISTINCT case_id
                FROM {qs}.case_responsibles
                WHERE user_id = {uid_ph}::uuid
                AND is_active = true
                UNION
                SELECT DISTINCT case_id
                FROM {qs}.case_assignment_tasks
                WHERE assigned_user_id = {uid_ph}::uuid
                AND status = 'open'
            ) cr_view ON cr_view.case_id = c.id
            """,
            [user_id],
        )

    if view == "admin":
        return (
            f"""
            INNER JOIN (
                SELECT cm_t.case_id
                FROM {qs}.case_movements cm_t
                WHERE cm_t.type = 'transfer'
                  AND cm_t.is_active = false
                  AND cm_t.admin_sector_id IN (
                      SELECT sector_id FROM {qs}.users WHERE id = {uid_ph}::uuid
                      UNION
                      SELECT sector_id FROM {qs}.user_sector_permissions
                      WHERE user_id = {uid_ph}::uuid AND can_view = true
                  )
                  AND cm_t.closed_at = (
                      SELECT MAX(cm2.closed_at) FROM {qs}.case_movements cm2
                      WHERE cm2.case_id = cm_t.case_id
                        AND cm2.type = 'transfer' AND cm2.is_active = false
                  )
                UNION
                SELECT cm_c.case_id
                FROM {qs}.case_movements cm_c
                WHERE cm_c.type = 'creation'
                  AND cm_c.admin_sector_id IN (
                      SELECT sector_id FROM {qs}.users WHERE id = {uid_ph}::uuid
                      UNION
                      SELECT sector_id FROM {qs}.user_sector_permissions
                      WHERE user_id = {uid_ph}::uuid AND can_view = true
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM {qs}.case_movements cm_t2
                      WHERE cm_t2.case_id = cm_c.case_id AND cm_t2.type = 'transfer'
                  )
            ) admin_view ON admin_view.case_id = c.id
            """,
            [user_id],
        )

    if view == "actuante":
        return (
            f"""
            INNER JOIN (
                SELECT DISTINCT cm_view.case_id
                FROM {qs}.case_movements cm_view
                WHERE cm_view.type = 'assignment'
                  AND cm_view.is_active = true
                  AND cm_view.assigned_sector_id IN (
                      SELECT sector_id FROM {qs}.users WHERE id = {uid_ph}::uuid
                      UNION
                      SELECT sector_id FROM {qs}.user_sector_permissions
                      WHERE user_id = {uid_ph}::uuid AND can_view = true
                  )
            ) actuante_cases ON actuante_cases.case_id = c.id
            """,
            [user_id],
        )

    if view == "favoritos":
        return (
            f"""
            INNER JOIN {qs}.case_favorites cf_view
                ON cf_view.case_id = c.id
                AND cf_view.user_id = {uid_ph}::uuid
            """,
            [user_id],
        )

    return "", []


async def get_cases_by_user(
    user_id: str,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    status_filter: Optional[str] = None,
    search_filter: Optional[str] = None,
    date_filter: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    sector_filter: Optional[str] = None,
    trata_filter: Optional[str] = None,
    view: Optional[str] = None,
    sort_order: str = "desc",
    *,
    schema_name: str,
    search_flags: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    logger.info(f"Listando expedientes para usuario={user_id}, page={page}")

    try:
        if date_filter and date_filter not in DATE_FILTER_OPTIONS:
            raise ValidationError(
                CASE_INVALID_DATE_FILTER_ERROR.format(options=", ".join(DATE_FILTER_OPTIONS))
            )

        if search_filter is not None:
            search_filter = search_filter.strip()
            if len(search_filter) < CASE_SEARCH_MIN_CHARS:
                if search_filter:
                    logger.info(
                        f"search='{search_filter}' descartado: "
                        f"minimo {CASE_SEARCH_MIN_CHARS} caracteres"
                    )
                search_filter = None

        if search_flags is not None:
            can_global_search = search_flags.get('can_global_search_cases', False)
            user_sector_ids = await _get_user_sector_ids(user_id, schema_name=schema_name)
        else:
            from shared.utils import get_user_global_search_flags
            flags, user_sector_ids = await asyncio.gather(
                get_user_global_search_flags(user_id, schema_name=schema_name),
                _get_user_sector_ids(user_id, schema_name=schema_name),
            )
            can_global_search = flags.get('can_global_search_cases', False)

        is_global_search = bool(
            can_global_search
            and search_filter
            and _looks_like_case_number(search_filter)
        )

        logger.info(
            f"User {user_id[:8]} can_global_search={can_global_search}, "
            f"is_global_search={is_global_search} "
            f"(search_filter={repr(search_filter)})"
        )

        logger.info(f"User {user_id[:8]} sectors: {user_sector_ids}")
        if not user_sector_ids and not is_global_search:
            logger.warning(f"Usuario {user_id} sin sectores - retornando lista vacía")
            return {"cases": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 0}

        if not user_sector_ids:
            user_sector_ids = ['00000000-0000-0000-0000-000000000000']

        resolved_tratas = await _resolve_trata_filter(trata_filter, schema_name=schema_name) if trata_filter else []
        resolved_sector = await _resolve_sector_filter(sector_filter, schema_name=schema_name) if sector_filter else None

        logger.debug(f"Filtros resueltos - trata: {trata_filter} -> {resolved_tratas}, sector: {sector_filter} -> {resolved_sector}, global_search: {is_global_search}")

        offset = (page - 1) * page_size

        where_clause, next_idx = _build_where_conditions(
            search_filter,
            resolved_sector,
            resolved_tratas,
            date_filter,
            date_from,
            date_to,
            is_global_search=is_global_search,
            sector_param=1,
            user_id_param=2,
            param_start=3,
        )

        count_param_start = 2 if is_global_search else 3
        count_user_id_param = 1 if is_global_search else 2
        count_where, count_next_idx = _build_where_conditions(
            search_filter,
            resolved_sector,
            resolved_tratas,
            date_filter,
            date_from,
            date_to,
            is_global_search=is_global_search,
            sector_param=1,
            user_id_param=count_user_id_param,
            param_start=count_param_start,
        )

        view_join_list, view_join_params_list = _build_view_clause(
            view, user_id, schema_name, user_id_param_idx=next_idx
        )

        view_join_count, view_join_params_count = _build_view_clause(
            view, user_id, schema_name, user_id_param_idx=count_next_idx
        )

        list_view_offset = len(view_join_params_list)
        sort_dir = "ASC" if sort_order.lower() == "asc" else "DESC"

        cases_query = get_cases_list_query(
            where_clause,
            view_join=view_join_list,
            sort_dir=sort_dir,
            limit_param_idx=next_idx + list_view_offset,
            offset_param_idx=next_idx + list_view_offset + 1,
        )
        count_query = get_cases_count_query(count_where, view_join=view_join_count)

        where_params = _build_where_params(
            search_filter, resolved_sector, resolved_tratas, date_from, date_to,
        )

        list_params = (
            user_sector_ids,
            user_id,
            *where_params,
            *view_join_params_list,
            page_size,
            offset,
        )

        count_params = (
            [user_id, *where_params, *view_join_params_count]
            if is_global_search
            else [user_sector_ids, user_id, *where_params, *view_join_params_count]
        )

        cache_ttl = _cases_count_cache_ttl()
        cache_key = _build_cases_count_cache_key(schema_name, view, tuple(count_params))
        if cache_ttl > 0:
            total = await get_cached(
                cache_key,
                lambda: _execute_count(count_query, count_params, schema_name),
                ttl=cache_ttl,
                cache_if=lambda t: t != 0,
            )
        else:
            total = await _execute_count(count_query, count_params, schema_name)

        if total == 0:
            return {
                "cases": [], "total": 0, "page": page,
                "page_size": page_size, "total_pages": 0
            }

        results = await fetch_all(cases_query, *list_params, schema_name=schema_name)

        if not results:
            if cache_ttl > 0:
                await invalidate_cache(cache_key)
            total = await _execute_count(count_query, count_params, schema_name)
            return {
                "cases": [], "total": total, "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size if total else 0,
            }

        cases = [_build_case_response(row, schema_name=schema_name) for row in results]

        logger.info(f"Se encontraron {total} expedientes para usuario {user_id}")

        return {
            "cases": cases,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size
        }

    except ValidationError:
        raise
    except Exception as e:
        logger.error(f"Error obteniendo expedientes: {str(e)}")
        raise BusinessLogicError(f"Error obteniendo expedientes: {str(e)}")


async def get_cases_summary(user_id: str, *, schema_name: str) -> Dict[str, Any]:
    logger.info(f"Obteniendo estadísticas de expedientes para usuario={user_id}")

    try:
        result = await fetch_all(
            get_cases_summary_query(),
            user_id, user_id, user_id,
            schema_name=schema_name
        )

        if not result:
            logger.warning(f"Sin resultados de estadísticas para usuario={user_id}")
            return {
                "total_cases": 0,
                "by_status": {"active": 0, "inactive": 0, "archived": 0},
                "created_by_me": 0,
                "departments_involved": 0
            }

        stats = result[0]
        return {
            "total_cases": stats["total_cases"] or 0,
            "by_status": {
                "active": stats["active_cases"] or 0,
                "inactive": stats["inactive_cases"] or 0,
                "archived": stats["archived_cases"] or 0
            },
            "created_by_me": stats["created_by_me"] or 0,
            "departments_involved": stats["departments_involved"] or 0
        }

    except Exception as e:
        logger.error(f"Error obteniendo estadísticas: {str(e)}")
        raise BusinessLogicError(f"Error obteniendo estadísticas: {str(e)}")
