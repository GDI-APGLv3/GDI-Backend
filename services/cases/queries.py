
from shared.logging import get_logger

logger = get_logger(__name__)

def get_department_and_municipality_query() -> str:
    return """
        SELECT d.acronym as dept_acronym, d.name as dept_name,
               m.acronym as municipality_acronym, m.name as municipality_name
        FROM departments d, public.municipalities m
        WHERE d.id = $1 AND m.schema_name = $2
    """

def get_advisory_lock_query() -> str:
    return "SELECT pg_advisory_xact_lock(999999, hashtext($1))"

def get_next_case_sequence_query() -> str:
    return """
        SELECT COALESCE(MAX(
            CAST(SUBSTRING(case_number FROM '\\d{4}-(\\d+)-') AS INTEGER)
        ), 0) + 1 as next_sequence
        FROM cases
        WHERE EXTRACT(YEAR FROM created_at) = $1
    """


async def get_case_detail(case_id: str, user_id: str, *, schema_name: str, conn=None):
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

        from services.case_service import CaseService
        if not await CaseService.can_user_view_case(case_id, user_id, schema_name=schema_name, conn=conn):
            logger.warning(f"User {user_id[:8]} denied access to case {case_id[:8]}")
            return None

        basic_info_query = get_case_basic_info_query()
        user_sectors_query = get_user_sectors_for_case_query()
        admin_sector_query = get_admin_sector_for_case_query()
        assigned_sectors_query = get_assigned_sectors_for_case_query()
        favorite_query = (
            "SELECT EXISTS (SELECT 1 FROM case_favorites WHERE case_id = $1 AND user_id = $2) AS is_favorite"
        )

        if conn is not None:
            case_result = await conn.fetch(basic_info_query, case_id)
        else:
            case_result = await fetch_all(basic_info_query, case_id, schema_name=schema_name)
        if not case_result:
            logger.warning(f"Case not found: {case_id[:8]}")
            return None

        case_data = case_result[0]

        if conn is not None:
            sectors_result = await conn.fetch(user_sectors_query, user_id)
        else:
            sectors_result = await fetch_all(user_sectors_query, user_id, schema_name=schema_name)
        user_sector_ids = [row['sector_id'] for row in sectors_result if row['sector_id']]

        if conn is not None:
            admin_sector_result = await conn.fetch(admin_sector_query, case_id)
        else:
            admin_sector_result = await fetch_all(admin_sector_query, case_id, schema_name=schema_name)
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

        if conn is not None:
            assigned_sectors_result = await conn.fetch(assigned_sectors_query, case_id)
        else:
            assigned_sectors_result = await fetch_all(assigned_sectors_query, case_id, schema_name=schema_name)
        assigned_sectors = []
        assigned_sector_ids = []
        for row in assigned_sectors_result:
            assigned_sectors.append({
                "acronym": row['sector_acronym'],
                "department": row['department_name'],
                "sector_color": row.get('sector_color'),
            })
            assigned_sector_ids.append(row['sector_id'])

        from services.case_service import CaseService
        access_reason = CaseService._calculate_access_reason(
            user_sector_ids,
            admin_sector_id,
            assigned_sector_ids
        )

        if conn is not None:
            favorite_result = await conn.fetchrow(favorite_query, case_id, user_id)
        else:
            favorite_result = await fetch_one(favorite_query, case_id, user_id, schema_name=schema_name)
        is_favorite = bool(favorite_result['is_favorite']) if favorite_result else False

        logger.info(f"Case detail retrieved - Access: {access_reason}")

        return {
            "id": case_data['id'],
            "case_number": case_data['case_number'],
            "reference": case_data['reference'],
            "ai_summary": case_data.get('ai_summary'),
            "template": {
                "name": case_data['type_name'],
                "acronym": case_data['template_acronym'],
                "is_reserved": bool(case_data['template_is_reserved']),
            },
            "access_reason": access_reason,
            "admin_sector": admin_sector,
            "assigned_sectors": assigned_sectors,
            "is_favorite": is_favorite
        }

    except Exception as e:
        logger.error(f"Error fetching case detail: {str(e)}")
        raise BusinessLogicError(f"Error obteniendo detalle del expediente: {str(e)}")


async def get_available_templates(user_id: str, *, schema_name: str):
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
    from database import fetch_all

    try:
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

        sector_placeholders = ",".join([f"${i+2}" for i in range(len(user_sector_ids))])

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

        params = list(user_sector_ids) * 5 + [case_number]

        results = await fetch_all(case_query, *params, schema_name=schema_name)

        if not results:
            return None

        row = results[0]

        access_reason = "ASSIGNEDSECTOR"

        if row['is_admin_by_transfer'] or row['is_admin_by_creation']:
            access_reason = "ADMINSECTOR"

        admin_sector = None
        if row['admin_sector_acronym'] and row['admin_sector_department']:
            admin_sector = {
                "acronym": row['admin_sector_acronym'],
                "department": row['admin_sector_department'],
                "sector_color": row.get('admin_sector_color'),
            }

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
    from database import fetch_all

    try:
        case_query = """
            SELECT
                c.id,
                c.case_number,
                c.reference,
                c.created_at as last_modified_at,
                ct.type_name,
                ct.acronym as case_type,
                COALESCE(ct.is_reserved, false) as is_reserved
            FROM cases c
            JOIN case_templates ct ON c.case_template_id = ct.id
            WHERE c.case_number = $1
            LIMIT 1
        """

        results = await fetch_all(case_query, case_number, schema_name=schema_name)

        if not results:
            return None

        row = results[0]
        case_id = row['id']

        if user_id:
            from services.cases.permissions import can_user_view_case
            if not await can_user_view_case(case_id, user_id, schema_name=schema_name):
                if not row['is_reserved']:
                    return None
                return {
                    "case": {
                        "id": case_id,
                        "case_number": row['case_number'],
                        "reference": None,
                        "last_modified_at": None,
                        "case_type": {"name": None, "acronym": None},
                        "access_reason": "RESERVED_NUMBER_MATCH",
                        "admin_sector": None,
                        "assigned_sectors": [],
                        "is_reserved": True,
                        "restricted": True,
                    },
                    "found": True,
                    "total": 1,
                }

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
            "assigned_sectors": assigned_sectors,
            "is_reserved": bool(row['is_reserved']),
        }

        return {
            "case": case_data,
            "found": True,
            "total": 1
        }

    except Exception as e:
        raise Exception(f"Error buscando expediente por número (sin restricciones): {str(e)}")
