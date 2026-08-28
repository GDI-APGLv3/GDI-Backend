from shared.logging import get_logger
from typing import Dict, Any
from api_gateway.context import MCPContext
from services.documents.catalog.types import get_all_document_types
from services.documents.catalog.states import get_all_display_states, get_all_state_mappings
from database import fetch_one, fetch_all
from shared.exceptions import ValidationError, GDIBaseException

logger = get_logger(__name__)


async def get_document_types(ctx: MCPContext) -> Dict[str, Any]:
    logger.info(f"[MCP] get_document_types - schema={ctx.schema_name}")

    try:
        types = await get_all_document_types(schema_name=ctx.schema_name)

        logger.info(f"[MCP] get_document_types - {len(types)} tipos encontrados")

        return {
            "document_types": types,
            "total": len(types)
        }

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_document_types - error: {e}")
        raise RuntimeError("Error obteniendo tipos de documentos")


async def get_user_info(ctx: MCPContext, user_id: str) -> Dict[str, Any]:
    logger.info(f"[MCP] get_user_info - user_id={user_id}, schema={ctx.schema_name}")

    if not user_id:
        raise ValueError("user_id es requerido")

    try:
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
            WHERE u.id = $1
        """

        user_data = await fetch_one(query_user, user_id, schema_name=ctx.schema_name)

        if not user_data:
            raise ValueError(f"Usuario no encontrado: {user_id}")

        query_roles = """
            SELECT r.role_name
            FROM user_roles ur
            JOIN public.roles r ON ur.role_id = r.role_id
            WHERE ur.user_id = $1
        """

        roles_data = await fetch_all(query_roles, user_id, schema_name=ctx.schema_name)
        roles = [row["role_name"] for row in roles_data]

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
            WHERE usp.user_id = $1 AND s.is_active = true
            ORDER BY d.acronym, s.acronym
        """

        additional_data = await fetch_all(query_additional, user_id, schema_name=ctx.schema_name)
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
            "last_access": user_data["last_access"].isoformat() if user_data["last_access"] else None,
            "created_at": user_data["created_at"].isoformat() if user_data["created_at"] else None,
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

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_user_info - error: {e}")
        raise RuntimeError("Error obteniendo información del usuario")


async def get_document_states(ctx: MCPContext) -> Dict[str, Any]:
    logger.info(f"[MCP] get_document_states - schema={ctx.schema_name}")

    try:
        states = await get_all_display_states(schema_name=ctx.schema_name)

        mappings = await get_all_state_mappings(schema_name=ctx.schema_name)

        logger.info(f"[MCP] get_document_states - {len(states)} estados encontrados")

        return {
            "states": states,
            "mappings": mappings,
            "total": len(states)
        }

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_document_states - error: {e}")
        raise RuntimeError("Error obteniendo estados de documentos")


async def get_sectors(ctx: MCPContext) -> Dict[str, Any]:
    logger.info(f"[MCP] get_sectors - schema={ctx.schema_name}")

    try:
        from services.sector_service import SectorService

        result = await SectorService.get_all_sectors_with_departments(schema_name=ctx.schema_name)

        logger.info(f"[MCP] get_sectors - resultado obtenido")

        return result

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_sectors - error: {e}")
        raise RuntimeError("Error obteniendo sectores")


async def get_case_templates(ctx: MCPContext, user_id: str) -> Dict[str, Any]:
    logger.info(f"[MCP] get_case_templates - user_id={user_id}, schema={ctx.schema_name}")

    try:
        from services.cases.queries import get_available_templates

        result = await get_available_templates(user_id=user_id, schema_name=ctx.schema_name)

        logger.info(f"[MCP] get_case_templates - {len(result)} templates encontrados")

        return {
            "templates": result,
            "total": len(result)
        }

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_case_templates - error: {e}")
        raise RuntimeError("Error obteniendo templates de expedientes")


async def search_users(ctx: MCPContext, search: str, limit: int = 10) -> Dict[str, Any]:
    logger.info(f"[MCP] search_users - search='{search}', limit={limit}, schema={ctx.schema_name}")

    if not search or len(search) < 2:
        raise ValueError("El término de búsqueda debe tener al menos 2 caracteres")

    try:
        from services.users.search import search_users_for_autocomplete

        result = await search_users_for_autocomplete(
            search_query=search,
            limit=limit,
            schema_name=ctx.schema_name
        )

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

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] search_users - error: {e}")
        raise RuntimeError("Error buscando usuarios")


async def get_dashboard_stats(ctx: MCPContext, user_id: str) -> Dict[str, Any]:
    logger.info(f"[MCP] get_dashboard_stats - user_id={user_id}, schema={ctx.schema_name}")

    try:
        from services.dashboard_service import DashboardService

        result = await DashboardService.get_stats(user_id=user_id, schema_name=ctx.schema_name)

        logger.info(f"[MCP] get_dashboard_stats - estadísticas obtenidas")

        return result

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_dashboard_stats - error: {e}")
        raise RuntimeError("Error obteniendo estadísticas del dashboard")


async def get_dashboard_feed(ctx: MCPContext, user_id: str, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
    logger.info(f"[MCP] get_dashboard_feed - user_id={user_id}, schema={ctx.schema_name}, page={page}")

    try:
        from services.dashboard_service import DashboardService

        result = await DashboardService.get_feed(
            user_id=user_id,
            page=page,
            page_size=page_size,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] get_dashboard_feed - feed obtenido")

        return result

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_dashboard_feed - error: {e}")
        raise RuntimeError("Error obteniendo feed del dashboard")


async def list_all_users(ctx: MCPContext) -> Dict[str, Any]:
    logger.info(f"[MCP] list_all_users - schema={ctx.schema_name}")

    try:
        from services.users.list import list_all_active_users

        users = await list_all_active_users(schema_name=ctx.schema_name)

        logger.info(f"[MCP] list_all_users - {len(users)} usuarios activos encontrados")

        return {
            "users": users,
            "total": len(users)
        }

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] list_all_users - error: {e}")
        raise RuntimeError("Error listando usuarios")


async def check_signer_permissions(
    ctx: MCPContext,
    user_id_to_check: str,
    document_type_acronym: str
) -> Dict[str, Any]:
    logger.info(
        f"[MCP] check_signer_permissions - user={user_id_to_check}, "
        f"doc_type={document_type_acronym}, schema={ctx.schema_name}"
    )

    if not user_id_to_check:
        raise ValueError("user_id_to_check es requerido")
    if not document_type_acronym:
        raise ValueError("document_type_acronym es requerido")

    try:
        type_row = await fetch_one(
            "SELECT id, name FROM document_types WHERE acronym = $1",
            document_type_acronym,
            schema_name=ctx.schema_name,
        )

        if not type_row:
            logger.warning(
                f"[MCP] check_signer_permissions - tipo '{document_type_acronym}' "
                f"no encontrado. Fail-closed."
            )
            return {
                "can_sign": False,
                "has_rank_permission": False,
                "has_sector_permission": False,
                "user_rank": None,
                "required_rank": None,
                "document_type": None,
                "message": (
                    f"No se encontraron datos para el tipo de documento "
                    f"'{document_type_acronym}'. Verificar que exista."
                ),
            }

        document_type_id: int = type_row["id"]
        document_type_name: str = type_row["name"]

        from services.documents.signing.numbering_permissions import (
            can_user_number_document_type,
        )

        has_rank, has_sector, reason = await can_user_number_document_type(
            user_id_to_check,
            document_type_id,
            schema_name=ctx.schema_name,
        )

        can_sign = has_rank and has_sector

        logger.info(
            f"[MCP] check_signer_permissions - can_sign={can_sign}, "
            f"rank={has_rank}, sector={has_sector}"
        )

        return {
            "can_sign": can_sign,
            "has_rank_permission": has_rank,
            "has_sector_permission": has_sector,
            "user_rank": None,
            "required_rank": None,
            "document_type": document_type_name,
            "message": reason,
        }

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] check_signer_permissions - error: {e}")
        raise RuntimeError("Error verificando permisos de firma")
