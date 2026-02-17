"""
Módulo de consulta/recuperación de expedientes.
Funciones para listar y buscar expedientes.

Este módulo contiene todas las funciones relacionadas con la obtención y filtrado
de expedientes para un usuario, incluyendo helpers para resolución de filtros
y construcción de queries dinámicas.
"""

from typing import List, Dict, Optional, Any
import json
from shared.logging import get_logger
import re

from database import execute_query
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


def _resolve_trata_filter(trata_filter: str, *, schema_name: str) -> List[str]:
    """
    Resuelve trata_filter a lista de UUIDs.
    Acepta: UUID, acronym único, o acronyms separados por coma.

    Ejemplos:
    - "uuid-aqui" -> ["uuid-aqui"]
    - "HABI" -> ["uuid-de-habi"]
    - "HABI,EEVAR" -> ["uuid-de-habi", "uuid-de-eevar"]

    Args:
        trata_filter: Filtro de tipo de expediente (UUID o acronym)
        schema_name: Schema de la base de datos (keyword-only)

    Returns:
        Lista de UUIDs de case_templates
    """
    if not trata_filter:
        return []

    # Si es UUID, retornar directo
    if _is_uuid(trata_filter):
        return [trata_filter]

    # Parsear acronyms separados por coma
    acronyms = [a.strip().upper() for a in trata_filter.split(',') if a.strip()]
    if not acronyms:
        return []

    # Buscar UUIDs de case_templates por acronym
    query = """
        SELECT id FROM case_templates
        WHERE UPPER(acronym) = ANY(%s) AND is_active = true
    """
    results = execute_query(query, (acronyms,), schema_name=schema_name)
    return [str(r['id']) for r in results] if results else []


def _resolve_sector_filter(sector_filter: str, *, schema_name: str) -> Optional[str]:
    """
    Resuelve sector_filter a UUID.
    Acepta: UUID o formato DEPT#SECTOR (ej: "HAC#PRIV").

    Ejemplos:
    - "uuid-aqui" -> "uuid-aqui"
    - "HAC#PRIV" -> "uuid-del-sector"
    - "HAC" -> "uuid-del-sector" (fallback por acronym solo)

    Args:
        sector_filter: Filtro de sector (UUID o formato DEPT#SECTOR)
        schema_name: Schema de la base de datos (keyword-only)

    Returns:
        UUID del sector o None si no se encuentra
    """
    if not sector_filter:
        return None

    # Si es UUID, retornar directo
    if _is_uuid(sector_filter):
        return sector_filter

    # Parsear formato DEPT#SECTOR
    if '#' in sector_filter:
        parts = sector_filter.split('#')
        if len(parts) == 2:
            dept_acronym, sector_acronym = parts[0].strip().upper(), parts[1].strip().upper()
            query = """
                SELECT s.id
                FROM sectors s
                JOIN departments d ON s.department_id = d.id
                WHERE UPPER(d.acronym) = %s
                AND UPPER(s.acronym) = %s
                AND s.is_active = true
            """
            results = execute_query(query, (dept_acronym, sector_acronym), schema_name=schema_name)
            if results:
                return str(results[0]['id'])

    # Fallback: buscar solo por sector_acronym
    query = """
        SELECT s.id FROM sectors s
        WHERE UPPER(s.acronym) = %s AND s.is_active = true
        LIMIT 1
    """
    results = execute_query(query, (sector_filter.strip().upper(),), schema_name=schema_name)
    return str(results[0]['id']) if results else None


def _build_where_conditions(
    sector_placeholders: str,
    search_filter: Optional[str],
    resolved_sector: Optional[str],
    resolved_tratas: List[str],
    date_filter: Optional[str],
    date_from: Optional[str],
    date_to: Optional[str]
) -> str:
    """Construye las condiciones WHERE para la query.

    Args:
        sector_placeholders: Placeholders %s para sectores del usuario
        search_filter: Texto de búsqueda libre
        resolved_sector: UUID del sector ya resuelto (o None)
        resolved_tratas: Lista de UUIDs de templates ya resueltos (puede estar vacía)
        date_filter: Filtro de fecha predefinido (hoy, ayer, etc)
        date_from: Fecha desde (formato ISO)
        date_to: Fecha hasta (formato ISO)

    Returns:
        String con las condiciones WHERE completas
    """
    where_conditions = []

    # Condición de sectores del usuario (obligatoria)
    sector_conditions = f"""
        (
            EXISTS (
                SELECT 1 FROM case_movements cm
                WHERE cm.case_id = c.id
                AND cm.assigned_sector_id IN ({sector_placeholders})
                AND cm.is_active = true
            )
            OR
            EXISTS (
                SELECT 1 FROM case_movements cm
                WHERE cm.case_id = c.id
                AND cm.type = 'transfer'
                AND cm.is_active = false
                AND cm.admin_sector_id IN ({sector_placeholders})
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
                    AND cm.admin_sector_id IN ({sector_placeholders})
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
        where_conditions.append("""(
            unaccent(LOWER(c.reference)) LIKE unaccent(%s)
            OR unaccent(LOWER(c.case_number)) LIKE unaccent(%s)
            OR similarity(unaccent(LOWER(c.reference)), unaccent(LOWER(%s))) > 0.3
            OR EXISTS (
                SELECT 1 FROM case_official_documents cod
                JOIN official_documents od ON cod.official_document_id = od.id
                WHERE cod.case_id = c.id
                AND cod.is_active = true
                AND (
                    unaccent(LOWER(COALESCE(od.official_number, ''))) LIKE unaccent(%s)
                    OR unaccent(LOWER(COALESCE(od.reference, ''))) LIKE unaccent(%s)
                    OR unaccent(LOWER(COALESCE(od.content->>'html', ''))) LIKE unaccent(%s)
                    OR similarity(unaccent(LOWER(COALESCE(od.reference, ''))), unaccent(LOWER(%s))) > 0.3
                )
            )
        )""")

    if resolved_sector:
        where_conditions.append("""
            EXISTS (
                SELECT 1 FROM case_movements cm_sector
                WHERE cm_sector.case_id = c.id
                AND cm_sector.assigned_sector_id = %s
                AND cm_sector.is_active = true
            )
        """)

    if resolved_tratas:
        # Filtrar por tipo de expediente (case_template)
        # Cast explícito a uuid[] porque los IDs vienen como strings
        where_conditions.append("""
            c.case_template_id = ANY(%s::uuid[])
        """)

    if date_filter:
        date_condition = _get_date_filter_condition(date_filter)
        if date_condition:
            where_conditions.append(date_condition)

    if date_from:
        where_conditions.append("""DATE(GREATEST(
            COALESCE(
                (SELECT MAX(cm.created_at) FROM case_movements cm WHERE cm.case_id = c.id),
                c.created_at
            ),
            COALESCE(
                (SELECT MAX(cod.linking_date) FROM case_official_documents cod
                 WHERE cod.case_id = c.id AND cod.is_active = true),
                c.created_at
            )
        )) >= %s""")

    if date_to:
        where_conditions.append("""DATE(GREATEST(
            COALESCE(
                (SELECT MAX(cm.created_at) FROM case_movements cm WHERE cm.case_id = c.id),
                c.created_at
            ),
            COALESCE(
                (SELECT MAX(cod.linking_date) FROM case_official_documents cod
                 WHERE cod.case_id = c.id AND cod.is_active = true),
                c.created_at
            )
        )) <= %s""")

    return "WHERE " + " AND ".join(where_conditions)


def _get_date_filter_condition(date_filter: str) -> Optional[str]:
    """Retorna la condición SQL para el filtro de fecha.

    Args:
        date_filter: Filtro predefinido (hoy, ayer, ultimos_7_dias, ultimos_30_dias)

    Returns:
        Condición SQL o None si no es válido
    """
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
    user_sector_ids: List[str],
    search_filter: Optional[str],
    resolved_sector: Optional[str],
    resolved_tratas: List[str],
    date_from: Optional[str],
    date_to: Optional[str],
) -> list:
    """Construye los parámetros de la cláusula WHERE (sin paginación ni SELECT).
    Reutilizable para count query y list query.

    Args:
        user_sector_ids: Lista de UUIDs de sectores del usuario
        search_filter: Texto de búsqueda
        resolved_sector: UUID del sector ya resuelto (o None)
        resolved_tratas: Lista de UUIDs de templates ya resueltos
        date_from: Fecha desde
        date_to: Fecha hasta

    Returns:
        Lista con parámetros del WHERE
    """
    params = []

    # UUIDs para WHERE (3 veces los sector_ids)
    params.extend(user_sector_ids * 3)

    # Filtros adicionales
    if search_filter:
        search_pattern = f"%{search_filter.lower()}%"
        search_term = search_filter.lower()
        # 7 params: ref(LIKE), case_num(LIKE), ref(sim), od.num(LIKE), od.ref(LIKE), content(LIKE), od.ref(sim)
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
    search_filter: Optional[str],
    resolved_sector: Optional[str],
    resolved_tratas: List[str],
    date_from: Optional[str],
    date_to: Optional[str],
    page_size: int,
    offset: int
) -> tuple:
    """Construye la tupla de parámetros para la query de listado en el orden correcto.

    Args:
        user_sector_ids: Lista de UUIDs de sectores del usuario
        search_filter: Texto de búsqueda
        resolved_sector: UUID del sector ya resuelto (o None)
        resolved_tratas: Lista de UUIDs de templates ya resueltos (puede estar vacía)
        date_from: Fecha desde
        date_to: Fecha hasta
        page_size: Tamaño de página
        offset: Offset para paginación

    Returns:
        Tupla con todos los parámetros en el orden correcto para la query
    """
    params = []

    # 1. UUIDs para WHERE (3 veces los sector_ids)
    params.extend(user_sector_ids * 3)

    # 2. UUIDs para SELECT (2 veces los sector_ids para is_admin_by_transfer y is_admin_by_creation)
    params.extend(user_sector_ids * 2)

    # 3. Filtros adicionales
    if search_filter:
        search_pattern = f"%{search_filter.lower()}%"
        search_term = search_filter.lower()
        # 7 params: ref(LIKE), case_num(LIKE), ref(sim), od.num(LIKE), od.ref(LIKE), content(LIKE), od.ref(sim)
        params.extend([search_pattern, search_pattern, search_term,
                       search_pattern, search_pattern, search_pattern, search_term])

    if resolved_sector:
        params.append(resolved_sector)

    if resolved_tratas:
        # Pasar lista para ANY()
        params.append(resolved_tratas)

    if date_from:
        params.append(date_from)

    if date_to:
        params.append(date_to)

    # 4. Paginación
    params.extend([page_size, offset])

    return tuple(params)


def _determine_access_reason(row: Dict[str, Any]) -> str:
    """Determina la razón de acceso al expediente.

    Args:
        row: Fila de resultado de la query con campos is_admin_by_transfer e is_admin_by_creation

    Returns:
        Constante ACCESS_REASON_ADMIN o ACCESS_REASON_ASSIGNED
    """
    if row['is_admin_by_transfer'] or row['is_admin_by_creation']:
        return ACCESS_REASON_ADMIN
    return ACCESS_REASON_ASSIGNED


def _build_case_response(row: Dict[str, Any], *, schema_name: str) -> Dict[str, Any]:
    """Construye el objeto de respuesta para un caso.

    Args:
        row: Fila de resultado de la query
        schema_name: Schema de la base de datos (keyword-only)

    Returns:
        Diccionario con la respuesta formateada del caso
    """
    access_reason = _determine_access_reason(row)

    admin_sector = None
    if row['admin_sector_acronym'] and row['admin_sector_department']:
        admin_sector = {
            "acronym": row['admin_sector_acronym'],
            "department": row['admin_sector_department'],
            "sector_color": row.get('admin_sector_color'),
        }

    # Sectores asignados ya vienen en la query principal (optimización N+1)
    assigned_sectors_raw = row.get('assigned_sectors_json') or []

    # psycopg2 puede retornar string o list dependiendo de la versión
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
        "assigned_sectors": assigned_sectors
    }


def _execute_count(count_query: str, where_params: list, schema_name: str) -> int:
    """Ejecuta la count query y retorna el total."""
    result = execute_query(count_query, tuple(where_params), schema_name=schema_name)
    return result[0]['total_count'] if result else 0


# Importar función centralizada desde sector_utils
from services.shared.sector_utils import get_user_sector_ids as _get_user_sector_ids


# ========================================
# FUNCIONES PÚBLICAS
# ========================================

def get_cases_by_user(
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
    *,
    schema_name: str
) -> Dict[str, Any]:
    """Obtiene expedientes que puede ver un usuario con filtros avanzados.

    Args:
        user_id: UUID del usuario
        page: Número de página (1-indexed)
        page_size: Cantidad de resultados por página
        status_filter: Filtro por estado (actualmente no usado)
        search_filter: Texto de búsqueda libre
        date_filter: Filtro de fecha predefinido
        date_from: Fecha desde (ISO format)
        date_to: Fecha hasta (ISO format)
        sector_filter: Filtro de sector (UUID o DEPT#SECTOR)
        trata_filter: Filtro de tipo de expediente (UUID o acronym)
        schema_name: Schema de la base de datos (keyword-only)

    Returns:
        Dict con cases (lista), total, page, page_size, total_pages

    Raises:
        ValidationError: Si date_filter no es válido
        BusinessLogicError: Si hay error en la consulta
    """
    logger.info(f"Listando expedientes para usuario={user_id}, page={page}")

    try:
        # Validar date_filter si está presente
        if date_filter and date_filter not in DATE_FILTER_OPTIONS:
            raise ValidationError(
                CASE_INVALID_DATE_FILTER_ERROR.format(options=", ".join(DATE_FILTER_OPTIONS))
            )

        # Obtener sectores del usuario
        user_sector_ids = _get_user_sector_ids(user_id, schema_name=schema_name)

        if not user_sector_ids:
            logger.warning(f"Usuario {user_id} sin sectores - retornando lista vacía")
            return {"cases": [], "total": 0, "page": page, "page_size": page_size, "total_pages": 0}

        # Resolver filtros de acronym a UUID
        resolved_tratas = _resolve_trata_filter(trata_filter, schema_name=schema_name) if trata_filter else []
        resolved_sector = _resolve_sector_filter(sector_filter, schema_name=schema_name) if sector_filter else None

        logger.debug(f"Filtros resueltos - trata: {trata_filter} -> {resolved_tratas}, sector: {sector_filter} -> {resolved_sector}")

        # Construir query
        sector_placeholders = ",".join(["%s"] * len(user_sector_ids))
        where_clause = _build_where_conditions(
            sector_placeholders,
            search_filter,
            resolved_sector,
            resolved_tratas,
            date_filter,
            date_from,
            date_to
        )

        cases_query = get_cases_list_query(sector_placeholders, where_clause)
        count_query = get_cases_count_query(sector_placeholders, where_clause)

        # Construir parámetros
        offset = (page - 1) * page_size

        # Params para WHERE (reutilizados en count y list)
        where_params = _build_where_params(
            user_sector_ids, search_filter, resolved_sector,
            resolved_tratas, date_from, date_to
        )

        # Params para listado (WHERE + SELECT extras + paginación)
        list_params = _build_query_params(
            user_sector_ids,
            search_filter,
            resolved_sector,
            resolved_tratas,
            date_from,
            date_to,
            page_size,
            offset
        )

        # Ejecutar count separado (cacheado 30s)
        import hashlib
        filter_hash = hashlib.md5(str(where_params).encode()).hexdigest()[:8]
        cache_key = f"cases_count:{schema_name}:{filter_hash}"

        total = get_cached(
            cache_key,
            lambda: _execute_count(count_query, where_params, schema_name),
            ttl=CACHE_TTL_COUNTS
        )

        # Si total es 0 o la página está más allá, retornar vacío
        if total == 0:
            return {
                "cases": [], "total": 0, "page": page,
                "page_size": page_size, "total_pages": 0
            }

        # Ejecutar query de listado
        results = execute_query(cases_query, list_params, schema_name=schema_name)

        # Construir respuesta
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


def get_cases_summary(user_id: str, *, schema_name: str) -> Dict[str, Any]:
    """Obtiene estadísticas/resumen de expedientes del usuario.

    NOTA: Esta función actualmente tiene un bug en case_service.py.
    La query get_cases_summary_query() no existe. Debería ser implementada
    en services/case_queries.py o la lógica debería ser embebida aquí.

    Args:
        user_id: UUID del usuario
        schema_name: Schema de la base de datos (keyword-only)

    Returns:
        Dict con estadísticas: total_cases, by_status, created_by_me, departments_involved

    Raises:
        BusinessLogicError: Si hay error en la consulta
    """
    logger.info(f"Obteniendo estadísticas de expedientes para usuario={user_id}")

    try:
        # Ejecutar query de estadísticas
        result = execute_query(
            get_cases_summary_query(),
            (user_id, user_id, user_id),
            schema_name=schema_name
        )

        # Si no hay resultados, retornar valores en cero
        if not result:
            logger.warning(f"Sin resultados de estadísticas para usuario={user_id}")
            return {
                "total_cases": 0,
                "by_status": {"active": 0, "inactive": 0, "archived": 0},
                "created_by_me": 0,
                "departments_involved": 0
            }

        # Parsear resultado
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
