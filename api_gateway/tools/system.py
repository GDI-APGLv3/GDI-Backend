"""
Tools MCP para consultas del sistema (lectura y operaciones).
Tipos de documentos, estados y usuarios.
"""
import logging
from typing import Dict, Any
from api_gateway.context import MCPContext
from services.documents.catalog.types import get_all_document_types
from services.documents.catalog.states import get_all_display_states, get_all_state_mappings
from database import execute_query
from shared.exceptions import ValidationError

logger = logging.getLogger(__name__)


def get_document_types(ctx: MCPContext) -> Dict[str, Any]:
    """
    Obtener todos los tipos de documentos activos.

    Args:
        ctx: Contexto MCP con schema_name

    Returns:
        Dict con document_types (lista) y total

    Raises:
        RuntimeError: Si hay error de BD
    """
    logger.info(f"[MCP] get_document_types - schema={ctx.schema_name}")

    try:
        types = get_all_document_types(schema_name=ctx.schema_name)

        logger.info(f"[MCP] get_document_types - {len(types)} tipos encontrados")

        return {
            "document_types": types,
            "total": len(types)
        }

    except Exception as e:
        logger.error(f"[MCP] get_document_types - error: {e}")
        raise RuntimeError(f"Error obteniendo tipos de documentos: {str(e)}")


def get_user_info(ctx: MCPContext, user_id: str) -> Dict[str, Any]:
    """
    Obtener información del usuario actual.

    Args:
        ctx: Contexto MCP con schema_name
        user_id: UUID del usuario

    Returns:
        Dict con:
        - user_id, full_name, email, profile_picture_url
        - sector: sector actual del usuario
        - department: departamento del usuario
        - roles: lista de roles asignados
        - additional_sectors: sectores adicionales con permisos

    Raises:
        ValueError: Si user_id no proporcionado o usuario no existe
        RuntimeError: Si hay error de BD
    """
    logger.info(f"[MCP] get_user_info - user_id={user_id}, schema={ctx.schema_name}")

    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        # Query principal para obtener datos del usuario
        query_user = """
            SELECT
                u.id as user_id,
                u.full_name,
                u.email,
                u.profile_picture_url,
                u.estado,
                u.last_access,
                u.created_at,
                s.id as sector_id,
                s.acronym as sector_acronym,
                d.id as department_id,
                d.name as department_name,
                d.acronym as department_acronym
            FROM users u
            LEFT JOIN sectors s ON u.sector_id = s.id
            LEFT JOIN departments d ON s.department_id = d.id
            WHERE u.id = %s
        """

        user_data = execute_query(query_user, (user_id,), fetch_one=True, schema_name=ctx.schema_name)

        if not user_data:
            raise ValueError(f"Usuario no encontrado: {user_id}")

        # Query para obtener roles
        query_roles = """
            SELECT r.role_name
            FROM user_roles ur
            JOIN public.roles r ON ur.role_id = r.role_id
            WHERE ur.user_id = %s
        """

        roles_data = execute_query(query_roles, (user_id,), schema_name=ctx.schema_name)
        roles = [row["role_name"] for row in roles_data]

        # Query para obtener sectores adicionales con permisos
        query_additional = """
            SELECT
                s.id as sector_id,
                s.acronym as sector_acronym,
                d.acronym as department_acronym,
                d.name as department_name,
                usp.can_view,
                usp.can_edit
            FROM user_sector_permissions usp
            JOIN sectors s ON usp.sector_id = s.id
            JOIN departments d ON s.department_id = d.id
            WHERE usp.user_id = %s AND s.is_active = true
            ORDER BY d.acronym, s.acronym
        """

        additional_data = execute_query(query_additional, (user_id,), schema_name=ctx.schema_name)
        additional_sectors = []
        for row in additional_data:
            additional_sectors.append({
                "sector_id": str(row["sector_id"]),
                "sector_acronym": row["sector_acronym"],
                "department_acronym": row["department_acronym"],
                "department_name": row["department_name"],
                "can_view": row["can_view"],
                "can_edit": row["can_edit"]
            })

        result = {
            "user_id": str(user_data["user_id"]),
            "full_name": user_data["full_name"],
            "email": user_data["email"],
            "profile_picture_url": user_data["profile_picture_url"],
            "estado": user_data["estado"],
            "last_access": str(user_data["last_access"]) if user_data["last_access"] else None,
            "created_at": str(user_data["created_at"]) if user_data["created_at"] else None,
            "sector": {
                "id": str(user_data["sector_id"]) if user_data["sector_id"] else None,
                "acronym": user_data["sector_acronym"],
                "department_id": str(user_data["department_id"]) if user_data["department_id"] else None,
                "department_name": user_data["department_name"],
                "department_acronym": user_data["department_acronym"]
            } if user_data["sector_id"] else None,
            "roles": roles,
            "additional_sectors": additional_sectors
        }

        logger.info(f"[MCP] get_user_info - usuario encontrado: {user_data['full_name']}")
        return result

    except ValueError:
        raise
    except Exception as e:
        logger.error(f"[MCP] get_user_info - error: {e}")
        raise RuntimeError(f"Error obteniendo información del usuario: {str(e)}")


def get_document_states(ctx: MCPContext) -> Dict[str, Any]:
    """
    Obtener catálogo de estados de documentos.

    Args:
        ctx: Contexto MCP con schema_name

    Returns:
        Dict con:
        - states: lista de estados con display_state (nombre) y code (código interno)
        - mappings: diccionario de código → nombre para mapeo rápido
        - total: cantidad de estados

    Raises:
        RuntimeError: Si hay error de BD
    """
    logger.info(f"[MCP] get_document_states - schema={ctx.schema_name}")

    try:
        # Obtener estados de visualización
        states = get_all_display_states(schema_name=ctx.schema_name)

        # Obtener mapeos de códigos
        mappings = get_all_state_mappings(schema_name=ctx.schema_name)

        logger.info(f"[MCP] get_document_states - {len(states)} estados encontrados")

        return {
            "states": states,
            "mappings": mappings,
            "total": len(states)
        }

    except Exception as e:
        logger.error(f"[MCP] get_document_states - error: {e}")
        raise RuntimeError(f"Error obteniendo estados de documentos: {str(e)}")


def get_sectors(ctx: MCPContext) -> Dict[str, Any]:
    """
    Obtener todos los sectores activos con sus departamentos.

    Args:
        ctx: Contexto MCP con schema_name

    Returns:
        Dict con sectores y departamentos

    Raises:
        RuntimeError: Si hay error de BD
    """
    logger.info(f"[MCP] get_sectors - schema={ctx.schema_name}")

    try:
        from services.sector_service import SectorService

        result = SectorService.get_all_sectors_with_departments(schema_name=ctx.schema_name)

        logger.info(f"[MCP] get_sectors - resultado obtenido")

        return result

    except Exception as e:
        logger.error(f"[MCP] get_sectors - error: {e}")
        raise RuntimeError(f"Error obteniendo sectores: {str(e)}")


def get_case_templates(ctx: MCPContext, user_id: str) -> Dict[str, Any]:
    """
    Obtener plantillas de expedientes disponibles para el usuario.

    Args:
        ctx: Contexto MCP con schema_name
        user_id: UUID del usuario

    Returns:
        Dict con templates (lista) y total

    Raises:
        RuntimeError: Si hay error de BD
    """
    logger.info(f"[MCP] get_case_templates - user_id={user_id}, schema={ctx.schema_name}")

    try:
        from services.cases.queries import get_available_templates

        result = get_available_templates(user_id=user_id, schema_name=ctx.schema_name)

        logger.info(f"[MCP] get_case_templates - {len(result)} templates encontrados")

        return {
            "templates": result,
            "total": len(result)
        }

    except Exception as e:
        logger.error(f"[MCP] get_case_templates - error: {e}")
        raise RuntimeError(f"Error obteniendo templates de expedientes: {str(e)}")


def search_users(ctx: MCPContext, search: str, limit: int = 10) -> Dict[str, Any]:
    """
    Buscar usuarios para autocompletado.

    Args:
        ctx: Contexto MCP con schema_name
        search: Término de búsqueda (mínimo 2 caracteres)
        limit: Cantidad máxima de resultados (default 10)

    Returns:
        Dict con resultados de búsqueda

    Raises:
        ValueError: Si search tiene menos de 2 caracteres
        RuntimeError: Si hay error de BD
    """
    logger.info(f"[MCP] search_users - search='{search}', limit={limit}, schema={ctx.schema_name}")

    if not search or len(search) < 2:
        raise ValueError("El término de búsqueda debe tener al menos 2 caracteres")

    try:
        from services.users.search import search_users_for_autocomplete

        result = search_users_for_autocomplete(
            search_query=search,
            limit=limit,
            schema_name=ctx.schema_name
        )

        # Filter response to only full_name and sector for MCP privacy
        if result and "users" in result:
            filtered_users = []
            for user in result["users"]:
                dept = user.get("department_acronym") or ""
                sector = user.get("sector_acronym") or ""
                sector_display = f"{dept}#{sector}" if (dept and sector) else (dept or sector or None)
                filtered_users.append({
                    "full_name": user.get("full_name"),
                    "sector": sector_display,
                })
            result = {
                "users": filtered_users,
                "total_found": result.get("total_found", len(filtered_users)),
            }

        logger.info(f"[MCP] search_users - búsqueda completada")

        return result

    except ValidationError as e:
        logger.error(f"[MCP] search_users - validation error: {e}")
        raise RuntimeError(f"Error de validación: {str(e)}")
    except Exception as e:
        logger.error(f"[MCP] search_users - error: {e}")
        raise RuntimeError(f"Error buscando usuarios: {str(e)}")


def get_dashboard_stats(ctx: MCPContext, user_id: str) -> Dict[str, Any]:
    """
    Obtener estadísticas del dashboard para el usuario.

    Args:
        ctx: Contexto MCP con schema_name
        user_id: UUID del usuario

    Returns:
        Dict con estadísticas del dashboard

    Raises:
        RuntimeError: Si hay error de BD
    """
    logger.info(f"[MCP] get_dashboard_stats - user_id={user_id}, schema={ctx.schema_name}")

    try:
        from services.dashboard_service import DashboardService

        result = DashboardService.get_stats(user_id=user_id, schema_name=ctx.schema_name)

        logger.info(f"[MCP] get_dashboard_stats - estadísticas obtenidas")

        return result

    except Exception as e:
        logger.error(f"[MCP] get_dashboard_stats - error: {e}")
        raise RuntimeError(f"Error obteniendo estadísticas del dashboard: {str(e)}")


def get_dashboard_feed(ctx: MCPContext, user_id: str, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
    """
    Obtener feed de actividad del dashboard para el usuario.

    Args:
        ctx: Contexto MCP con schema_name
        user_id: UUID del usuario
        page: Número de página (default 1)
        page_size: Tamaño de página (default 20)

    Returns:
        Dict con feed de actividad

    Raises:
        RuntimeError: Si hay error de BD
    """
    logger.info(f"[MCP] get_dashboard_feed - user_id={user_id}, schema={ctx.schema_name}, page={page}")

    try:
        from services.dashboard_service import DashboardService

        result = DashboardService.get_feed(
            user_id=user_id,
            page=page,
            page_size=page_size,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] get_dashboard_feed - feed obtenido")

        return result

    except Exception as e:
        logger.error(f"[MCP] get_dashboard_feed - error: {e}")
        raise RuntimeError(f"Error obteniendo feed del dashboard: {str(e)}")


def list_all_users(ctx: MCPContext) -> Dict[str, Any]:
    """
    Listar todos los usuarios activos del tenant.

    Args:
        ctx: Contexto MCP con schema_name

    Returns:
        Dict con:
        - users: lista de usuarios [{user_id, full_name, email}]
        - total: cantidad de usuarios

    Raises:
        RuntimeError: Si hay error de BD
    """
    logger.info(f"[MCP] list_all_users - schema={ctx.schema_name}")

    try:
        from services.users.list import list_all_active_users

        users = list_all_active_users(schema_name=ctx.schema_name)

        logger.info(f"[MCP] list_all_users - {len(users)} usuarios activos encontrados")

        return {
            "users": users,
            "total": len(users)
        }

    except Exception as e:
        logger.error(f"[MCP] list_all_users - error: {e}")
        raise RuntimeError(f"Error listando usuarios: {str(e)}")


def check_signer_permissions(
    ctx: MCPContext,
    user_id_to_check: str,
    document_type_acronym: str
) -> Dict[str, Any]:
    """
    Verificar si un usuario puede firmar como numerador un tipo de documento.
    Verifica rango y sector del usuario contra los requisitos del tipo de documento.

    Args:
        ctx: Contexto MCP con schema_name
        user_id_to_check: UUID del usuario firmante a validar
        document_type_acronym: Acronimo del tipo de documento (ej: DEC, IF, RES)

    Returns:
        Dict con:
        - can_sign: bool
        - has_rank_permission: bool
        - has_sector_permission: bool
        - user_rank: nombre del rango del usuario (o None)
        - required_rank: nombre del rango requerido (o None)
        - document_type: nombre del tipo de documento (o None)
        - message: mensaje descriptivo

    Raises:
        ValueError: Si faltan parametros requeridos
        RuntimeError: Si hay error de BD
    """
    logger.info(f"[MCP] check_signer_permissions - user={user_id_to_check}, doc_type={document_type_acronym}, schema={ctx.schema_name}")

    if not user_id_to_check:
        raise ValueError("user_id_to_check es requerido")
    if not document_type_acronym:
        raise ValueError("document_type_acronym es requerido")

    try:
        # Query adaptada de endpoints/documents/check_signer_permissions.py
        QUERY = """
            SELECT
                ur.name as user_rank_name,
                ur.level as user_rank_level,
                rr.name as required_rank_name,
                rr.level as required_rank_level,
                dt.name as doc_type_name,
                dt.acronym as doc_type_acronym,
                dep.id as user_department_id,
                dep.name as user_department_name,
                CASE
                    WHEN rr.level IS NULL THEN true
                    WHEN ur.level IS NULL THEN false
                    WHEN ur.level <= rr.level THEN true
                    ELSE false
                END as has_rank_permission,
                CASE
                    WHEN NOT EXISTS(
                        SELECT 1 FROM enabled_document_types_by_sector
                        WHERE document_type_id = dt.id
                    ) THEN true
                    WHEN EXISTS(
                        SELECT 1 FROM enabled_document_types_by_sector
                        WHERE document_type_id = dt.id
                        AND sector_id = u.sector_id
                    ) THEN true
                    WHEN EXISTS(
                        SELECT 1 FROM enabled_document_types_by_sector edts
                        WHERE edts.document_type_id = dt.id
                        AND edts.sector_id IN (
                            SELECT usp.sector_id
                            FROM user_sector_permissions usp
                            WHERE usp.user_id = u.id AND usp.can_edit = true
                        )
                    ) THEN true
                    ELSE false
                END as has_sector_permission
            FROM document_types dt
            JOIN users u ON u.id = %s
            LEFT JOIN user_seals us ON u.id = us.user_id
            LEFT JOIN city_seals cs ON us.city_seal_id = cs.id
            LEFT JOIN ranks ur ON cs.rank_id = ur.id
            LEFT JOIN sectors sec ON u.sector_id = sec.id
            LEFT JOIN departments dep ON sec.department_id = dep.id
            LEFT JOIN document_types_allowed_by_rank dtabr ON dt.id = dtabr.document_type_id
            LEFT JOIN ranks rr ON dtabr.rank_id = rr.id
            WHERE dt.acronym = %s
        """

        result = execute_query(
            QUERY,
            (user_id_to_check, document_type_acronym),
            fetch_one=True,
            schema_name=ctx.schema_name
        )

        # Fail-closed: si no hay resultado, denegar (tipo o usuario no existe)
        if not result:
            logger.warning(f"[MCP] check_signer_permissions - no se encontraron datos para user={user_id_to_check}, doc_type={document_type_acronym}")
            return {
                "can_sign": False,
                "has_rank_permission": False,
                "has_sector_permission": False,
                "user_rank": None,
                "required_rank": None,
                "document_type": None,
                "message": f"No se encontraron datos para el usuario o tipo de documento '{document_type_acronym}'. Verificar que ambos existan."
            }

        has_rank = result["has_rank_permission"]
        has_sector = result["has_sector_permission"]
        can_sign = has_rank and has_sector

        # Construir mensaje descriptivo
        if can_sign:
            message = "OK"
        elif not has_rank and not has_sector:
            message = (
                f"Rango insuficiente: el usuario tiene rango "
                f"'{result['user_rank_name'] or 'sin rango'}' "
                f"pero se requiere '{result['required_rank_name']}'. "
                f"Ademas, el sector del usuario no tiene habilitado "
                f"el tipo de documento '{result['doc_type_name']}'."
            )
        elif not has_rank:
            message = (
                f"Rango insuficiente: el usuario tiene rango "
                f"'{result['user_rank_name'] or 'sin rango'}' "
                f"pero se requiere '{result['required_rank_name']}' "
                f"para firmar documentos de tipo '{result['doc_type_name']}'."
            )
        else:
            message = (
                f"Sector no habilitado: el sector del usuario no tiene "
                f"habilitado el tipo de documento '{result['doc_type_name']}'."
            )

        response = {
            "can_sign": can_sign,
            "has_rank_permission": has_rank,
            "has_sector_permission": has_sector,
            "user_rank": result["user_rank_name"],
            "required_rank": result["required_rank_name"],
            "document_type": result["doc_type_name"],
            "message": message
        }

        logger.info(f"[MCP] check_signer_permissions - can_sign={can_sign}, rank={has_rank}, sector={has_sector}")

        return response

    except Exception as e:
        logger.error(f"[MCP] check_signer_permissions - error: {e}")
        raise RuntimeError(f"Error verificando permisos de firma: {str(e)}")
