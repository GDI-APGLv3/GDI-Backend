"""Queries para operaciones de expedientes/casos."""

from shared.logging import get_logger

logger = get_logger(__name__)

def get_department_and_municipality_query() -> str:
    """Query para obtener información de departamento y municipio.

    En arquitectura multi-tenant:
    - departments está en el schema del tenant
    - municipalities está en public y se identifica por schema_name

    Parámetros esperados: (department_id, schema_name)
    """
    return """
        SELECT d.acronym as dept_acronym, d.name as dept_name,
               m.acronym as municipality_acronym, m.name as municipality_name
        FROM departments d, public.municipalities m
        WHERE d.id = $1 AND m.schema_name = $2
    """

def get_advisory_lock_query() -> str:
    """Query para adquirir lock transaccional para numeración."""
    return "SELECT pg_advisory_xact_lock(999999)"

def get_next_case_sequence_query() -> str:
    """Query para obtener siguiente número secuencial de caso."""
    return """
        SELECT COALESCE(MAX(
            CAST(SUBSTRING(case_number FROM '\\d{4}-(\\d+)-') AS INTEGER)
        ), 0) + 1 as next_sequence
        FROM cases
        WHERE EXTRACT(YEAR FROM created_at) = $1
    """

def insert_new_case_query() -> str:
    """Query para insertar nuevo caso."""
    return """
        INSERT INTO cases (
            case_template_id, case_number, reference, created_by_user_id,
            filing_department_id, creator_sector_id, owner_sector_id, created_at
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING id, case_number, created_at
    """

def check_case_movement_exists_query() -> str:
    """Query para verificar si existe un movimiento de caso específico."""
    return """
        SELECT 1 FROM case_movements cm
        WHERE cm.case_id = $1 AND cm.movement_type = $2
        AND cm.origin_sector_id = $3 AND cm.destination_sector_id = $4
        LIMIT 1
    """

def get_max_case_movement_date_query() -> str:
    """Query para obtener fecha máxima de movimiento de caso."""
    return """
        SELECT MAX(cm2.closed_at)
        FROM case_movements cm2
        WHERE cm2.case_id = $1 AND cm2.closed_at IS NOT NULL
    """


# =============================================================================
# FUNCIONES DE CONSULTA INDIVIDUAL
# =============================================================================

async def get_case_detail(case_id: str, user_id: str, *, schema_name: str):
    """Obtener detalles completos de un expediente."""
    from services.case_queries import (
        get_case_basic_info_query,
        get_user_sectors_for_case_query,
        get_admin_sector_for_case_query,
        get_assigned_sectors_for_case_query
    )
    from database import fetch_all, fetch_one
    from shared.exceptions import BusinessLogicError

    try:
        logger.info(f"Fetching case detail - Case: {case_id[:8]}, User: {user_id[:8]}")

        # SIEMPRE verificar permisos (skip_permission_check eliminado)
        from services.case_service import CaseService
        if not await CaseService.can_user_view_case(case_id, user_id, schema_name=schema_name):
            logger.warning(f"User {user_id[:8]} denied access to case {case_id[:8]}")
            return None

        # Obtener información básica del expediente
        case_result = await fetch_all(get_case_basic_info_query(), case_id, schema_name=schema_name)
        if not case_result:
            logger.warning(f"Case not found: {case_id[:8]}")
            return None

        case_data = case_result[0]

        sectors_result = await fetch_all(get_user_sectors_for_case_query(), user_id, schema_name=schema_name)
        user_sector_ids = [row['sector_id'] for row in sectors_result if row['sector_id']]

        # Obtener sector administrador del caso
        admin_sector_result = await fetch_all(get_admin_sector_for_case_query(), case_id, schema_name=schema_name)
        admin_sector = None
        admin_sector_id = None
        if admin_sector_result:
            admin_data = admin_sector_result[0]
            admin_sector = {
                "acronym": admin_data['sector_acronym'],
                "department": admin_data['department_name'],
                "sector_color": admin_data.get('sector_color'),
            }
            admin_sector_id = admin_data['sector_id']

        # Obtener sectores asignados (movimientos activos)
        assigned_sectors_result = await fetch_all(get_assigned_sectors_for_case_query(), case_id, schema_name=schema_name)
        assigned_sectors = []
        assigned_sector_ids = []
        for row in assigned_sectors_result:
            assigned_sectors.append({
                "acronym": row['sector_acronym'],
                "department": row['department_name'],
                "sector_color": row.get('sector_color'),
            })
            assigned_sector_ids.append(row['sector_id'])

        # Calcular nivel de acceso
        from services.case_service import CaseService
        access_reason = CaseService._calculate_access_reason(
            user_sector_ids,
            admin_sector_id,
            assigned_sector_ids
        )

        logger.info(f"Case detail retrieved - Access: {access_reason}")

        return {
            "id": case_data['id'],
            "case_number": case_data['case_number'],
            "reference": case_data['reference'],
            "template": {
                "name": case_data['type_name'],
                "acronym": case_data['template_acronym']
            },
            "access_reason": access_reason,
            "admin_sector": admin_sector,
            "assigned_sectors": assigned_sectors
        }

    except Exception as e:
        logger.error(f"Error fetching case detail: {str(e)}")
        raise BusinessLogicError(f"Error obteniendo detalle del expediente: {str(e)}")


async def get_available_templates(user_id: str, *, schema_name: str):
    """Obtener plantillas de expedientes disponibles."""
    from services.case_queries import get_available_templates_query
    from database import fetch_all
    from shared.exceptions import BusinessLogicError

    try:
        results = await fetch_all(get_available_templates_query(), schema_name=schema_name)

        return [
            {
                "id": row['id'],
                "name": row['type_name'],
                "acronym": row['acronym'],
                "description": row.get('description'),
                "filing_department_name": row.get('filing_department_name'),
                "filing_department_acronym": row.get('filing_department_acronym'),
                "filing_department_color": row.get('filing_department_color'),
            }
            for row in results
        ]

    except Exception as e:
        logger.error(f"Error fetching templates: {str(e)}")
        raise BusinessLogicError(f"Error obteniendo plantillas: {str(e)}")


async def get_case_by_exact_number(case_number: str, user_id: str, *, schema_name: str):
    """
    Buscar un expediente por número exacto.
    Devuelve exactamente los mismos datos que get_cases_by_user pero para un expediente específico.
    """
    from database import fetch_all

    try:
        # Obtener sectores del usuario
        user_sectors_query = """
            SELECT DISTINCT
                COALESCE(s.id, s2.id) as sector_id
            FROM users u
            LEFT JOIN sectors s ON u.sector_id = s.id
            LEFT JOIN user_sector_permissions usp ON u.id = usp.user_id
            LEFT JOIN sectors s2 ON usp.sector_id = s2.id
            WHERE u.id = $1
            AND (s.is_active = true OR s2.is_active = true)
        """

        sectors_result = await fetch_all(user_sectors_query, user_id, schema_name=schema_name)
        user_sector_ids = [row['sector_id'] for row in sectors_result if row['sector_id']]

        if not user_sector_ids:
            return None

        # Construir placeholders para sectores del usuario
        sector_placeholders = ",".join([f"${i+2}" for i in range(len(user_sector_ids))])

        # Consulta para buscar el expediente por número exacto
        case_query = f"""
            SELECT
                c.id,
                c.case_number,
                c.reference,
                ct.type_name,
                ct.acronym as case_type,
                1 as total_count,
                -- Calcular last_modified_at (GREATEST de movements y document links)
                GREATEST(
                    COALESCE(
                        (SELECT MAX(cm.created_at)
                         FROM case_movements cm
                         WHERE cm.case_id = c.id),
                        c.created_at
                    ),
                    COALESCE(
                        (SELECT MAX(cod.linking_date)
                         FROM case_official_documents cod
                         WHERE cod.case_id = c.id AND cod.is_active = true),
                        c.created_at
                    )
                ) as last_modified_at,
                -- Obtener admin_sector (último movimiento cerrado creation/transfer)
                (SELECT d2.acronym || '#' || s2.acronym
                 FROM case_movements cm
                 JOIN sectors s2 ON cm.admin_sector_id = s2.id
                 JOIN departments d2 ON s2.department_id = d2.id
                 WHERE cm.case_id = c.id
                   AND cm.is_active = false
                   AND cm.type IN ('creation', 'transfer')
                 ORDER BY cm.closed_at DESC
                 LIMIT 1) as admin_sector_acronym,
                (SELECT d2.name
                 FROM case_movements cm
                 JOIN sectors s2 ON cm.admin_sector_id = s2.id
                 JOIN departments d2 ON s2.department_id = d2.id
                 WHERE cm.case_id = c.id
                   AND cm.is_active = false
                   AND cm.type IN ('creation', 'transfer')
                 ORDER BY cm.closed_at DESC
                 LIMIT 1) as admin_sector_department,
                (SELECT s2.primary_color
                 FROM case_movements cm
                 JOIN sectors s2 ON cm.admin_sector_id = s2.id
                 WHERE cm.case_id = c.id
                   AND cm.is_active = false
                   AND cm.type IN ('creation', 'transfer')
                 ORDER BY cm.closed_at DESC
                 LIMIT 1) as admin_sector_color,
                -- Determinar si tiene transferencias
                EXISTS (
                    SELECT 1 FROM case_movements cm
                    WHERE cm.case_id = c.id AND cm.type = 'transfer'
                ) as has_transfers,
                -- Verificar si es ADMIN por última transferencia
                EXISTS (
                    SELECT 1 FROM case_movements cm
                    WHERE cm.case_id = c.id
                    AND cm.type = 'transfer'
                    AND cm.is_active = false
                    AND cm.admin_sector_id = ANY(ARRAY[{sector_placeholders}]::uuid[])
                    AND cm.closed_at = (
                        SELECT MAX(cm2.closed_at)
                        FROM case_movements cm2
                        WHERE cm2.case_id = c.id
                        AND cm2.type = 'transfer'
                        AND cm2.is_active = false
                    )
                ) as is_admin_by_transfer,
                -- Verificar si es ADMIN por creación (solo si no hay transfers)
                (
                    EXISTS (
                        SELECT 1 FROM case_movements cm
                        WHERE cm.case_id = c.id
                        AND cm.type = 'creation'
                        AND cm.admin_sector_id = ANY(ARRAY[{sector_placeholders}]::uuid[])
                    )
                    AND NOT EXISTS (
                        SELECT 1 FROM case_movements cm
                        WHERE cm.case_id = c.id
                        AND cm.type = 'transfer'
                    )
                ) as is_admin_by_creation
            FROM cases c
            JOIN case_templates ct ON c.case_template_id = ct.id
            WHERE
                c.case_number = ${len(user_sector_ids) * 2 + 2}
                AND c.status = 'active'
                AND (
                    -- Condición 1: ASIGNADO (asignación activa)
                    EXISTS (
                        SELECT 1 FROM case_movements cm
                        WHERE cm.case_id = c.id
                        AND cm.assigned_sector_id = ANY(ARRAY[{sector_placeholders}]::uuid[])
                        AND cm.is_active = true
                    )
                    OR
                    -- Condición 2: ADMIN (última transferencia cerrada)
                    EXISTS (
                        SELECT 1 FROM case_movements cm
                        WHERE cm.case_id = c.id
                        AND cm.type = 'transfer'
                        AND cm.is_active = false
                        AND cm.admin_sector_id = ANY(ARRAY[{sector_placeholders}]::uuid[])
                        AND cm.closed_at = (
                            SELECT MAX(cm2.closed_at)
                            FROM case_movements cm2
                            WHERE cm2.case_id = c.id
                            AND cm2.type = 'transfer'
                            AND cm2.is_active = false
                        )
                    )
                    OR
                    -- Condición 3: ADMIN (creador, solo si no hay transfers)
                    (
                        EXISTS (
                            SELECT 1 FROM case_movements cm
                            WHERE cm.case_id = c.id
                            AND cm.type = 'creation'
                            AND cm.admin_sector_id = ANY(ARRAY[{sector_placeholders}]::uuid[])
                        )
                        AND NOT EXISTS (
                            SELECT 1 FROM case_movements cm
                            WHERE cm.case_id = c.id
                            AND cm.type = 'transfer'
                        )
                    )
                )
        """

        # Parámetros: 2 copias para SELECT (is_admin_by_transfer, is_admin_by_creation)
        # + 3 copias para WHERE (assigned, transfer, creation) + case_number
        params = list(user_sector_ids) * 5 + [case_number]

        results = await fetch_all(case_query, *params, schema_name=schema_name)

        if not results:
            return None

        row = results[0]

        # Determinar access_reason con prioridad: ADMINSECTOR > ASSIGNEDSECTOR
        access_reason = "ASSIGNEDSECTOR"  # Default

        if row['is_admin_by_transfer'] or row['is_admin_by_creation']:
            access_reason = "ADMINSECTOR"

        # Construir admin_sector
        admin_sector = None
        if row['admin_sector_acronym'] and row['admin_sector_department']:
            admin_sector = {
                "acronym": row['admin_sector_acronym'],
                "department": row['admin_sector_department'],
                "sector_color": row.get('admin_sector_color'),
            }

        # Obtener sectores asignados activos (DISTINCT)
        assigned_sectors_query = """
            SELECT DISTINCT
                d.acronym || '#' || s.acronym as sector_acronym,
                d.name as department_name,
                s.primary_color as sector_color
            FROM case_movements cm
            JOIN sectors s ON cm.assigned_sector_id = s.id
            JOIN departments d ON s.department_id = d.id
            WHERE cm.case_id = $1
              AND cm.is_active = true
              AND cm.assigned_sector_id IS NOT NULL
            ORDER BY sector_acronym
        """
        assigned_result = await fetch_all(assigned_sectors_query, row['id'], schema_name=schema_name)

        assigned_sectors = [
            {
                "acronym": asg['sector_acronym'],
                "department": asg['department_name'],
                "sector_color": asg.get('sector_color'),
            }
            for asg in (assigned_result or [])
        ]

        case_data = {
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

        return {
            "case": case_data,
            "found": True,
            "total": 1
        }

    except Exception as e:
        raise Exception(f"Error buscando expediente por número: {str(e)}")


async def get_case_by_exact_number_unrestricted(case_number: str, *, user_id: str = None, schema_name: str):
    """
    Buscar un expediente por número exacto sin restricciones (o con filtro de sector).
    """
    from database import fetch_all

    try:
        # Query caso + flag global en 1 sola query con LEFT JOIN users
        case_query = """
            SELECT
                c.id,
                c.case_number,
                c.reference,
                c.created_at as last_modified_at,
                ct.type_name,
                ct.acronym as case_type,
                u.can_global_search_cases
            FROM cases c
            JOIN case_templates ct ON c.case_template_id = ct.id
            LEFT JOIN users u ON u.id = $1
            WHERE c.case_number = $2
            LIMIT 1
        """

        results = await fetch_all(case_query, user_id, case_number, schema_name=schema_name)

        if not results:
            return None

        row = results[0]
        case_id = row['id']
        has_global = row.get('can_global_search_cases', False) if user_id else True

        # Si user_id proporcionado y sin flag global, verificar sectores
        if user_id and not has_global:
            from services.cases.permissions import get_user_viewable_sector_ids
            user_sector_ids = await get_user_viewable_sector_ids(user_id, schema_name=schema_name)

            if user_sector_ids:
                sector_check_query = """
                    SELECT EXISTS(
                        SELECT 1 FROM case_movements cm
                        WHERE cm.case_id = $1
                          AND cm.is_active = true
                          AND (
                            cm.admin_sector_id = ANY($2::uuid[])
                            OR cm.assigned_sector_id = ANY($2::uuid[])
                          )
                    ) as has_access
                """
                access_result = await fetch_all(
                    sector_check_query,
                    case_id, user_sector_ids,
                    schema_name=schema_name
                )
                if not access_result or not access_result[0].get('has_access', False):
                    return None
            else:
                return None

        # Obtener admin sector desde el último movimiento de administración
        admin_sector_query = """
            SELECT
                d.acronym as department_acronym,
                d.name as department_name,
                s.acronym as sector_acronym,
                s.primary_color as sector_color
            FROM case_movements cm
            JOIN sectors s ON cm.admin_sector_id = s.id
            JOIN departments d ON s.department_id = d.id
            WHERE cm.case_id = $1
              AND cm.admin_sector_id IS NOT NULL
            ORDER BY cm.created_at DESC
            LIMIT 1
        """
        admin_result = await fetch_all(admin_sector_query, case_id, schema_name=schema_name)

        admin_sector = None
        if admin_result:
            admin_data = admin_result[0]
            admin_sector = {
                "acronym": admin_data['department_acronym'] + '#' + admin_data['sector_acronym'],
                "department": admin_data['department_name'],
                "sector_color": admin_data.get('sector_color'),
            }

        # Obtener sectores asignados activos
        assigned_sectors_query = """
            SELECT DISTINCT
                d.acronym || '#' || s.acronym as sector_acronym,
                d.name as department_name,
                s.primary_color as sector_color
            FROM case_movements cm
            JOIN sectors s ON cm.assigned_sector_id = s.id
            JOIN departments d ON s.department_id = d.id
            WHERE cm.case_id = $1
              AND cm.is_active = true
              AND cm.assigned_sector_id IS NOT NULL
            ORDER BY sector_acronym
        """
        assigned_result = await fetch_all(assigned_sectors_query, case_id, schema_name=schema_name)

        assigned_sectors = [
            {
                "acronym": asg['sector_acronym'],
                "department": asg['department_name'],
                "sector_color": asg.get('sector_color'),
            }
            for asg in (assigned_result or [])
        ]

        case_data = {
            "id": row['id'],
            "case_number": row['case_number'],
            "reference": row['reference'],
            "last_modified_at": row['last_modified_at'],
            "case_type": {
                "name": row['type_name'],
                "acronym": row['case_type']
            },
            "access_reason": "PUBLIC_SEARCH",
            "admin_sector": admin_sector,
            "assigned_sectors": assigned_sectors
        }

        return {
            "case": case_data,
            "found": True,
            "total": 1
        }

    except Exception as e:
        raise Exception(f"Error buscando expediente por número (sin restricciones): {str(e)}")
