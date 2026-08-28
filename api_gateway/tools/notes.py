from shared.logging import get_logger
from typing import Dict, Any, Optional, List
from api_gateway.context import MCPContext
from services.notes.retrieval import get_received_notes_multi_sector, get_sent_notes_multi_sector, get_archived_notes_multi_sector
from services.shared.sector_utils import get_user_sector_ids
from shared.exceptions import AuthorizationError, NotFoundError, GDIBaseException

logger = get_logger(__name__)


async def get_notes(
    ctx: MCPContext,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    unread_only: bool = False,
    search: Optional[str] = None
) -> Dict[str, Any]:
    logger.info(f"[MCP] get_notes - user_id={user_id}, schema={ctx.schema_name}, page={page}")

    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        sector_ids = await get_user_sector_ids(user_id, schema_name=ctx.schema_name)

        if not sector_ids:
            logger.warning(f"[MCP] get_notes - usuario sin sectores")
            return {
                "notes": [],
                "pagination": {"page": page, "page_size": page_size, "total": 0, "total_pages": 0}
            }

        result = await get_received_notes_multi_sector(
            sector_ids=sector_ids,
            schema_name=ctx.schema_name,
            page=page,
            page_size=page_size,
            unread_only=unread_only,
            search=search
        )

        total = result.get("pagination", {}).get("total", 0)
        logger.info(f"[MCP] get_notes - {total} notas encontradas")

        return result

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_notes - error: {e}")
        raise RuntimeError("Error obteniendo notas")


async def get_sent_notes(
    ctx: MCPContext,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None
) -> Dict[str, Any]:
    logger.info(f"[MCP] get_sent_notes - user_id={user_id}, schema={ctx.schema_name}, page={page}")

    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        sector_ids = await get_user_sector_ids(user_id, schema_name=ctx.schema_name)

        if not sector_ids:
            logger.warning(f"[MCP] get_sent_notes - usuario sin sectores")
            return {
                "notes": [],
                "pagination": {"page": page, "page_size": page_size, "total": 0, "total_pages": 0}
            }

        result = await get_sent_notes_multi_sector(
            sector_ids=sector_ids,
            schema_name=ctx.schema_name,
            page=page,
            page_size=page_size,
            search=search
        )

        total = result.get("pagination", {}).get("total", 0)
        logger.info(f"[MCP] get_sent_notes - {total} notas enviadas encontradas")

        return result

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_sent_notes - error: {e}")
        raise RuntimeError("Error obteniendo notas enviadas")


async def get_archived_notes(
    ctx: MCPContext,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None
) -> Dict[str, Any]:
    logger.info(f"[MCP] get_archived_notes - user_id={user_id}, schema={ctx.schema_name}, page={page}")

    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        sector_ids = await get_user_sector_ids(user_id, schema_name=ctx.schema_name)

        if not sector_ids:
            logger.warning(f"[MCP] get_archived_notes - usuario sin sectores")
            return {
                "notes": [],
                "pagination": {"page": page, "page_size": page_size, "total": 0, "total_pages": 0}
            }

        result = await get_archived_notes_multi_sector(
            sector_ids=sector_ids,
            schema_name=ctx.schema_name,
            page=page,
            page_size=page_size,
            search=search
        )

        total = result.get("pagination", {}).get("total", 0)
        logger.info(f"[MCP] get_archived_notes - {total} notas archivadas encontradas")

        return result

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_archived_notes - error: {e}")
        raise RuntimeError("Error obteniendo notas archivadas")


async def _get_user_permissions_for_notes(user_id: str, *, schema_name: str) -> List[Dict[str, Any]]:
    from database import fetch_all

    query = """
        -- Sector principal (siempre can_view=true, can_edit=true)
        SELECT
            s.id as sector_id,
            true as can_view,
            true as can_edit,
            true as is_primary
        FROM users u
        JOIN sectors s ON u.sector_id = s.id
        WHERE u.id = $1 AND s.is_active = true

        UNION ALL

        -- Sectores adicionales con permisos
        SELECT
            s2.id as sector_id,
            usp.can_view,
            usp.can_edit,
            false as is_primary
        FROM users u
        JOIN user_sector_permissions usp ON u.id = usp.user_id
        JOIN sectors s2 ON usp.sector_id = s2.id
        WHERE u.id = $2 AND s2.is_active = true
    """

    results = await fetch_all(query, user_id, user_id, schema_name=schema_name)

    permissions = []
    for row in results:
        permissions.append({
            'sector_id': str(row['sector_id']),
            'can_view': row['can_view'],
            'can_edit': row['can_edit'],
            'is_primary': row['is_primary']
        })

    return permissions


async def get_note_detail(
    ctx: MCPContext,
    note_id: str,
    user_id: str
) -> Dict[str, Any]:
    logger.info(f"[MCP] get_note_detail - note_id={note_id}, user_id={user_id}, schema={ctx.schema_name}")

    if not note_id:
        raise ValueError("note_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        from services.notes import get_note_detail_multi_sector

        user_permissions = await _get_user_permissions_for_notes(user_id, schema_name=ctx.schema_name)

        if not user_permissions:
            raise ValueError("No se pudo determinar los permisos del usuario")

        viewable_count = sum(1 for p in user_permissions if p.get('can_view'))
        if viewable_count == 0:
            raise ValueError("El usuario no tiene sectores con permiso de visualizacion")

        result = await get_note_detail_multi_sector(
            document_id=note_id,
            requesting_user_id=user_id,
            user_permissions=user_permissions,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] get_note_detail - detalle obtenido para nota {note_id}")

        return result

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_note_detail - error: {e}")
        raise RuntimeError("Error obteniendo detalle de nota")
