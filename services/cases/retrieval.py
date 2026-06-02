"""
Módulo de consulta/recuperación de expedientes.
Funciones para listar y buscar expedientes.
"""

from typing import List, Dict, Optional, Any
import json
from shared.logging import get_logger
import re

from database import fetch_all
from services.case_queries import get_cases_list_query, get_cases_count_query, get_cases_summary_query
from services.cache import get_cached
from config.constants import (
    ACCESS_REASON_ADMIN,
    ACCESS_REASON_ASSIGNED,
    DATE_FILTER_OPTIONS,
    DEFAULT_PAGE_SIZE,
    CACHE_TTL_COUNTS,
    CASE_INVALID_DATE_FILTER_ERROR
)
from shared.exceptions import ValidationError, BusinessLogicError

logger = get_logger(__name__)


# ========================================
# HELPERS PRIVADOS
# ========================================

def _is_uuid(value: str) -> bool:
    """Verifica si es un UUID válido."""
    uuid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    return bool(re.match(uuid_pattern, value.lower().strip()))


def _looks_like_case_number(value: str) -> bool:
    """Detecta si un string parece un número de expediente."""
    case_number_pattern = r'^[A-Za-z]+-\d{4}-\d+(-[A-Za-z]+)?\s*$'
    return bool(re.match(case_number_pattern, value.strip()))


async def _resolve_trata_filter(trata_filter: str, *, schema_name: str) -> List[str]:
    """
    Resuelve trata_filter a lista de UUIDs.
    Acepta: UUID, acronym único, o acronyms separados por coma.
    """
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
    """
    Resuelve sector_filter a UUID.
    Acepta: UUID o formato DEPT#SECTOR (ej: "HAC#PRIV").
    """
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
    param_start: int = 3,
) -> tuple:
    """
    Construye las condiciones WHERE para la query.

    Retorna (where_sql, next_param_idx) donde next_param_idx es el índice
    del próximo $N disponible tras los params consumidos por esta función.

    sector_param: índice $N del array de sector_ids (siempre $1)
    param_start: índice del primer param dinámico (post sectors y user_id)
    """
    where_conditions = []
    idx = param_start

    if not is_global_search:
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
        where_conditions.append(sector_conditions)

    where_conditions.append("c.status = 'active'")

    if search_filter:
        where_conditions.append(f"""(
            unaccent(LOWER(c.reference)) LIKE unaccent(${idx})
            OR unaccent(LOWER(c.case_number)) LIKE unaccent(${idx + 1})
            OR similarity(unaccent(LOWER(c.reference)), unaccent(LOWER(${idx + 2}))) > 0.3
            OR EXISTS (
                SELECT 1 FROM case_official_documents cod
                JOIN official_documents od ON cod.official_document_id = od.id
                WHERE cod.case_id = c.id
                AND cod.is_active = true
                AND od.signed_at IS NOT NULL
                AND (
                    unaccent(LOWER(COALESCE(od.official_number, ''))) LIKE unaccent(${idx + 3})
                    OR unaccent(LOWER(COALESCE(od.reference, ''))) LIKE unaccent(${idx + 4})
                    OR unaccent(LOWER(COALESCE(od.content->>'html', ''))) LIKE unaccent(${idx + 5})
                    OR similarity(unaccent(LOWER(COALESCE(od.reference, ''))), unaccent(LOWER(${idx + 6}))) > 0.3
                )
            )
        )""")
        idx += 7

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
    """Retorna la condición SQL para el filtro de fecha."""
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
    """
    Construye los params dinámicos del WHERE (excluye $1=sector_ids y $2=user_id).
    Orden: search(7) + sector(1) + tratas(1) + date_from(1) + date_to(1).
    """
    params = []

    if search_filter:
        search_pattern = f"%{search_filter.lower()}%"
        search_term = search_filter.lower()
        params.extend([search_pattern, search_pattern, search_term,
                       search_pattern, search_pattern, search_pattern, search_term])

    if resolved_sector:
        params.append(resolved_sector)

    if resolved_tratas:
        params.append(resolved_tratas)

    if date_from:
        params.append(date_from)

    if date_to:
        params.append(date_to)

    return params


def _build_query_params(
    user_sector_ids: List[str],
    user_id: Optional[str],
    where_params: list,
    page_size: int,
    offset: int,
) -> tuple:
    """
    Construye params para la list query en orden posicional:
    $1=sector_ids, $2=user_id, $3..=where_params, $N-1=page_size, $N=offset.
    """
    return (
        user_sector_ids,
        user_id or '00000000-0000-0000-0000-000000000000',
        *where_params,
        page_size,
        offset,
    )


def _determine_access_reason(row: Dict[str, Any]) -> str:
    """Determina la razón de acceso al expediente."""
    if row['is_admin_by_transfer'] or row['is_admin_by_creation']:
        return ACCESS_REASON_ADMIN
    return ACCESS_REASON_ASSIGNED


def _build_case_response(row: Dict[str, Any], *, schema_name: str) -> Dict[str, Any]:
    """Construye el objeto de respuesta para un caso."""
    access_reason = _determine_access_reason(row)

    admin_sector = None
    if row['admin_sector_acronym'] and row['admin_sector_department']:
        admin_sector = {
            "acronym": row['admin_sector_acronym'],
            "department": row['admin_sector_department'],
            "sector_color": row['admin_sector_color'],
        }

    # asyncpg decodifica json/jsonb automáticamente via set_type_codec
    assigned_sectors_raw = row['assigned_sectors_json'] or []

    if isinstance(assigned_sectors_raw, str):
        assigned_sectors_raw = json.loads(assigned_sectors_raw) if assigned_sectors_raw else []

    assigned_sectors = [
        {
            "acronym": asg['sector_acronym'],
            "department": asg['department_name'],
            "sector_color": asg.get('sector_color'),  # asg es dict Python (json_build_object)
        }
        for asg in assigned_sectors_raw
    ]

    return {
        "id": row['id'],
        "case_number": row['case_number'],
        "reference": row['reference'],
        "last_modified_at": row['last_modified_at'],
        "case_type": {
            "name": row['type_name'],
            "acronym": row['case_type']
        },
        "access_reason": access_reason,
        "admin_sector": admin_sector,
        "assigned_sectors": assigned_sectors,
        "short_ai_summary": row['short_ai_summary'],
        "ai_summary": row['ai_summary'],
        "is_favorite": bool(row['is_favorite']),
    }


async def _execute_count(count_query: str, where_params: list, schema_name: str) -> int:
    """Ejecuta la count query y retorna el total."""
    result = await fetch_all(count_query, *where_params, schema_name=schema_name)
    return result[0]['total_count'] if result else 0


# Importar función centralizada desde sector_utils
from services.shared.sector_utils import get_user_sector_ids as _get_user_sector_ids


# ========================================
# FUNCIONES PÚBLICAS
# ========================================

def _build_view_clause(
    view: Optional[str],
    user_id: str,
    schema_name: str,
    user_id_param_idx: int,
) -> tuple:
    """
    Construye el fragmento SQL para la vista seleccionada.

    Devuelve (sql_fragment, params_extra) donde params_extra es [user_id]
    cuando se usa un placeholder, o [] si view es None/desconocido.

    El user_id se pasa como parámetro $N::uuid para evitar interpolación directa
    en SQL (previene SQL injection vía bypass de blacklist como dollar-quoting o
    normalización Unicode).
    """
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
    schema_name: str
) -> Dict[str, Any]:
    """Obtiene expedientes que puede ver un usuario con filtros avanzados."""
    logger.info(f"Listando expedientes para usuario={user_id}, page={page}")

    try:
        if date_filter and date_filter not in DATE_FILTER_OPTIONS:
            raise ValidationError(
                CASE_INVALID_DATE_FILTER_ERROR.format(options=", ".join(DATE_FILTER_OPTIONS))
            )

        from shared.utils import get_user_global_search_flags
        flags = await get_user_global_search_flags(user_id, schema_name=schema_name)
        can_global_search = flags.get('can_global_search_cases', False)

        is_global_search = (
            can_global_search
            and (not search_filter or _looks_like_case_number(search_filter))
        )

        logger.info(
            f"User {user_id[:8]} can_global_search={can_global_search}, "
            f"is_global_search={is_global_search} "
            f"(search_filter={repr(search_filter)})"
        )

        # Obtener sectores del usuario
        user_sector_ids = await _get_user_sector_ids(user_id, schema_name=schema_name)

        logger.info(f"User {user_id[:8]} sectors: {user_sector_ids}")
        if not user_sector_ids and not is_global_search:
            logger.warning(f"Usuario {user_id} sin sectores - retornando lista vacía")
            return {"cases": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 0}

        if not user_sector_ids:
            user_sector_ids = ['00000000-0000-0000-0000-000000000000']

        # Resolver filtros de acronym a UUID
        resolved_tratas = await _resolve_trata_filter(trata_filter, schema_name=schema_name) if trata_filter else []
        resolved_sector = await _resolve_sector_filter(sector_filter, schema_name=schema_name) if sector_filter else None

        logger.debug(f"Filtros resueltos - trata: {trata_filter} -> {resolved_tratas}, sector: {sector_filter} -> {resolved_sector}, global_search: {is_global_search}")

        # Construir WHERE dinámico con $N numerados
        # $1=sector_ids (array), $2=user_id (is_favorite en list query)
        # Para count query: params dinámicos arrancan en $2
        # Para list query: params dinámicos arrancan en $3 (después de user_id)
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
            param_start=3,  # list query: $1=sectors, $2=user_id, $3+=dynamic
        )

        # Cuando is_global_search=True no hay $1 (sector) en count query → params arrancan en $1
        count_param_start = 1 if is_global_search else 2
        count_where, count_next_idx = _build_where_conditions(
            search_filter,
            resolved_sector,
            resolved_tratas,
            date_filter,
            date_from,
            date_to,
            is_global_search=is_global_search,
            sector_param=1,
            param_start=count_param_start,
        )

        # list query: $1=sectors, $2=user_id (is_favorite), $3+=where_params
        # view_join usa $next_idx; LIMIT/OFFSET siguen después del param de view
        view_join_list, view_join_params_list = _build_view_clause(
            view, user_id, schema_name, user_id_param_idx=next_idx
        )

        # count query: view_join usa count_next_idx (índice correcto para esa query)
        view_join_count, view_join_params_count = _build_view_clause(
            view, user_id, schema_name, user_id_param_idx=count_next_idx
        )

        # LIMIT y OFFSET se desplazan 1 si hay view param
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

        # Construir params para list query:
        # $1=sectors, $2=user_id, $3..=where_params, $N=view_uid (si view), $N+1=page_size, $N+2=offset
        list_params = (
            user_sector_ids,
            user_id,
            *where_params,
            *view_join_params_list,
            page_size,
            offset,
        )

        # Ejecutar count separado (cacheado 30s)
        import hashlib
        filter_hash = hashlib.md5(str(where_params).encode()).hexdigest()[:8]
        view_key = view or "none"
        cache_key = f"cases_count:{schema_name}:{view_key}:{filter_hash}"

        # Global search: no sector filter en count → no pasar user_sector_ids como $1
        count_params = (
            list(where_params) + view_join_params_count
            if is_global_search
            else [user_sector_ids, *where_params, *view_join_params_count]
        )
        total = await get_cached(
            cache_key,
            lambda: _execute_count(count_query, count_params, schema_name),
            ttl=CACHE_TTL_COUNTS
        )

        if total == 0:
            return {
                "cases": [], "total": 0, "page": page,
                "page_size": page_size, "total_pages": 0
            }

        results = await fetch_all(cases_query, *list_params, schema_name=schema_name)

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
    """Obtiene estadísticas/resumen de expedientes del usuario."""
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
