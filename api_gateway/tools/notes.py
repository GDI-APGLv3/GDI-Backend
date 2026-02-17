"""
Tools MCP para notas (lectura).
Reutiliza servicios existentes de notas.
"""
import logging
from typing import Dict, Any, Optional, List
from api_gateway.context import MCPContext
from services.notes.retrieval import get_received_notes_multi_sector, get_sent_notes_multi_sector, get_archived_notes_multi_sector
from services.shared.sector_utils import get_user_sector_ids
from shared.exceptions import ValidationError, AuthorizationError

logger = logging.getLogger(__name__)


def get_notes(
    ctx: MCPContext,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    unread_only: bool = False,
    search: Optional[str] = None
) -> Dict[str, Any]:
    """
    Obtener notas recibidas del usuario.

    Args:
        ctx: Contexto MCP con schema_name
        user_id: UUID del usuario
        page: Numero de pagina (default 1)
        page_size: Tamano de pagina (default 20)
        unread_only: Si True, solo notas no leidas
        search: Termino de busqueda (opcional)

    Returns:
        Dict con notes y pagination
    """
    logger.info(f"[MCP] get_notes - user_id={user_id}, schema={ctx.schema_name}, page={page}")

    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        # Resolver sector_ids del usuario
        sector_ids = get_user_sector_ids(user_id, schema_name=ctx.schema_name)

        if not sector_ids:
            logger.warning(f"[MCP] get_notes - usuario sin sectores")
            return {
                "notes": [],
                "pagination": {"page": page, "page_size": page_size, "total": 0, "total_pages": 0}
            }

        result = get_received_notes_multi_sector(
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

    except Exception as e:
        logger.error(f"[MCP] get_notes - error: {e}")
        raise RuntimeError(f"Error obteniendo notas: {str(e)}")


def get_sent_notes(
    ctx: MCPContext,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None
) -> Dict[str, Any]:
    """
    Obtener notas enviadas por el usuario.

    Args:
        ctx: Contexto MCP con schema_name
        user_id: UUID del usuario
        page: Numero de pagina (default 1)
        page_size: Tamano de pagina (default 20)
        search: Termino de busqueda (opcional)

    Returns:
        Dict con notes y pagination
    """
    logger.info(f"[MCP] get_sent_notes - user_id={user_id}, schema={ctx.schema_name}, page={page}")

    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        # Resolver sector_ids del usuario (mismo patron que get_notes)
        sector_ids = get_user_sector_ids(user_id, schema_name=ctx.schema_name)

        if not sector_ids:
            logger.warning(f"[MCP] get_sent_notes - usuario sin sectores")
            return {
                "notes": [],
                "pagination": {"page": page, "page_size": page_size, "total": 0, "total_pages": 0}
            }

        result = get_sent_notes_multi_sector(
            sector_ids=sector_ids,
            schema_name=ctx.schema_name,
            page=page,
            page_size=page_size,
            search=search
        )

        total = result.get("pagination", {}).get("total", 0)
        logger.info(f"[MCP] get_sent_notes - {total} notas enviadas encontradas")

        return result

    except Exception as e:
        logger.error(f"[MCP] get_sent_notes - error: {e}")
        raise RuntimeError(f"Error obteniendo notas enviadas: {str(e)}")


def get_archived_notes(
    ctx: MCPContext,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None
) -> Dict[str, Any]:
    """
    Obtener notas archivadas.

    Args:
        ctx: Contexto MCP con schema_name
        user_id: UUID del usuario
        page: Numero de pagina (default 1)
        page_size: Tamano de pagina (default 20)
        search: Termino de busqueda (opcional)

    Returns:
        Dict con notes y pagination
    """
    logger.info(f"[MCP] get_archived_notes - user_id={user_id}, schema={ctx.schema_name}, page={page}")

    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        # Resolver sector_ids del usuario (mismo patron que get_notes)
        sector_ids = get_user_sector_ids(user_id, schema_name=ctx.schema_name)

        if not sector_ids:
            logger.warning(f"[MCP] get_archived_notes - usuario sin sectores")
            return {
                "notes": [],
                "pagination": {"page": page, "page_size": page_size, "total": 0, "total_pages": 0}
            }

        result = get_archived_notes_multi_sector(
            sector_ids=sector_ids,
            schema_name=ctx.schema_name,
            page=page,
            page_size=page_size,
            search=search
        )

        total = result.get("pagination", {}).get("total", 0)
        logger.info(f"[MCP] get_archived_notes - {total} notas archivadas encontradas")

        return result

    except Exception as e:
        logger.error(f"[MCP] get_archived_notes - error: {e}")
        raise RuntimeError(f"Error obteniendo notas archivadas: {str(e)}")


def _get_user_permissions_for_notes(user_id: str, *, schema_name: str) -> List[Dict[str, Any]]:
    """
    Construye la lista de permisos del usuario para notas desde la BD.
    Replica la logica de endpoints/notes/helpers.py pero sin depender de AuthenticatedUser.

    Args:
        user_id: UUID del usuario
        schema_name: Schema del tenant (keyword-only)

    Returns:
        Lista de dicts con {sector_id, can_view, can_edit, is_primary}
    """
    from database import execute_query

    # Obtener sector principal + sectores adicionales con permisos
    query = """
        -- Sector principal (siempre can_view=true, can_edit=true)
        SELECT
            s.id as sector_id,
            true as can_view,
            true as can_edit,
            true as is_primary
        FROM users u
        JOIN sectors s ON u.sector_id = s.id
        WHERE u.id = %s AND s.is_active = true

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
        WHERE u.id = %s AND s2.is_active = true
    """

    results = execute_query(query, (user_id, user_id), schema_name=schema_name)

    permissions = []
    for row in results:
        permissions.append({
            'sector_id': str(row['sector_id']),
            'can_view': row['can_view'],
            'can_edit': row['can_edit'],
            'is_primary': row['is_primary']
        })

    return permissions


def get_note_detail(
    ctx: MCPContext,
    note_id: str,
    user_id: str
) -> Dict[str, Any]:
    """
    Obtener detalle de una nota especifica.

    Args:
        ctx: Contexto MCP con schema_name
        note_id: UUID del documento (nota oficial)
        user_id: UUID del usuario solicitante

    Returns:
        Dict con detalle completo de la nota

    Raises:
        ValueError: Si faltan parametros requeridos
        AuthorizationError: Si el usuario no tiene acceso a la nota
        RuntimeError: Si hay error
    """
    logger.info(f"[MCP] get_note_detail - note_id={note_id}, user_id={user_id}, schema={ctx.schema_name}")

    if not note_id:
        raise ValueError("note_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        from services.notes import get_note_detail_multi_sector

        # Construir user_permissions desde la BD (replica logica de endpoint)
        user_permissions = _get_user_permissions_for_notes(user_id, schema_name=ctx.schema_name)

        if not user_permissions:
            raise ValueError("No se pudo determinar los permisos del usuario")

        viewable_count = sum(1 for p in user_permissions if p.get('can_view'))
        if viewable_count == 0:
            raise ValueError("El usuario no tiene sectores con permiso de visualizacion")

        result = get_note_detail_multi_sector(
            document_id=note_id,
            requesting_user_id=user_id,
            user_permissions=user_permissions,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] get_note_detail - detalle obtenido para nota {note_id}")

        return result

    except (AuthorizationError, ValueError):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_note_detail - error: {e}")
        raise RuntimeError(f"Error obteniendo detalle de nota: {str(e)}")
