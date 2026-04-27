"""
Tools MCP para memos (lectura).
Reutiliza servicios existentes de memos.
A diferencia de notes.py, no necesita resolver sector_ids (usa user_id directo).
"""
import logging
from typing import Dict, Any, Optional
from api_gateway.context import MCPContext
from services.memos.retrieval import get_received_memos, get_sent_memos, get_archived_memos
from shared.exceptions import AuthorizationError, NotFoundError

logger = logging.getLogger(__name__)


def get_memos(
    ctx: MCPContext,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None
) -> Dict[str, Any]:
    """
    Obtener memos recibidos del usuario.

    Args:
        ctx: Contexto MCP con schema_name
        user_id: UUID del usuario
        page: Numero de pagina (default 1)
        page_size: Tamano de pagina (default 20)
        search: Termino de busqueda (opcional)

    Returns:
        Dict con memos y pagination
    """
    logger.info(f"[MCP] get_memos - user_id={user_id}, schema={ctx.schema_name}, page={page}")

    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        result = get_received_memos(
            user_id,
            schema_name=ctx.schema_name,
            page=page,
            page_size=page_size,
            search=search
        )

        total = result.get("pagination", {}).get("total", 0)
        logger.info(f"[MCP] get_memos - {total} memos encontrados")

        return result

    except Exception as e:
        logger.error(f"[MCP] get_memos - error: {e}")
        raise RuntimeError(f"Error obteniendo memos: {str(e)}")


def get_sent_memos_tool(
    ctx: MCPContext,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None
) -> Dict[str, Any]:
    """
    Obtener memos enviados por el usuario.

    Args:
        ctx: Contexto MCP con schema_name
        user_id: UUID del usuario
        page: Numero de pagina (default 1)
        page_size: Tamano de pagina (default 20)
        search: Termino de busqueda (opcional)

    Returns:
        Dict con memos y pagination
    """
    logger.info(f"[MCP] get_sent_memos - user_id={user_id}, schema={ctx.schema_name}, page={page}")

    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        result = get_sent_memos(
            user_id,
            schema_name=ctx.schema_name,
            page=page,
            page_size=page_size,
            search=search
        )

        total = result.get("pagination", {}).get("total", 0)
        logger.info(f"[MCP] get_sent_memos - {total} memos enviados encontrados")

        return result

    except Exception as e:
        logger.error(f"[MCP] get_sent_memos - error: {e}")
        raise RuntimeError(f"Error obteniendo memos enviados: {str(e)}")


def get_archived_memos_tool(
    ctx: MCPContext,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None
) -> Dict[str, Any]:
    """
    Obtener memos archivados.

    Args:
        ctx: Contexto MCP con schema_name
        user_id: UUID del usuario
        page: Numero de pagina (default 1)
        page_size: Tamano de pagina (default 20)
        search: Termino de busqueda (opcional)

    Returns:
        Dict con memos y pagination
    """
    logger.info(f"[MCP] get_archived_memos - user_id={user_id}, schema={ctx.schema_name}, page={page}")

    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        result = get_archived_memos(
            user_id,
            schema_name=ctx.schema_name,
            page=page,
            page_size=page_size,
            search=search
        )

        total = result.get("pagination", {}).get("total", 0)
        logger.info(f"[MCP] get_archived_memos - {total} memos archivados encontrados")

        return result

    except Exception as e:
        logger.error(f"[MCP] get_archived_memos - error: {e}")
        raise RuntimeError(f"Error obteniendo memos archivados: {str(e)}")


def get_memo_detail(
    ctx: MCPContext,
    memo_id: str,
    user_id: str
) -> Dict[str, Any]:
    """
    Obtener detalle de un memo especifico.

    Args:
        ctx: Contexto MCP con schema_name
        memo_id: UUID del documento (memo oficial)
        user_id: UUID del usuario solicitante

    Returns:
        Dict con detalle completo del memo

    Raises:
        ValueError: Si faltan parametros requeridos
        AuthorizationError: Si el usuario no tiene acceso al memo
        RuntimeError: Si hay error
    """
    logger.info(f"[MCP] get_memo_detail - memo_id={memo_id}, user_id={user_id}, schema={ctx.schema_name}")

    if not memo_id:
        raise ValueError("memo_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        from services.memos import get_memo_detail as _get_memo_detail

        result = _get_memo_detail(
            document_id=memo_id,
            requesting_user_id=user_id,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] get_memo_detail - detalle obtenido para memo {memo_id}")

        return result

    except (AuthorizationError, NotFoundError, ValueError):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_memo_detail - error: {e}")
        raise RuntimeError(f"Error obteniendo detalle de memo: {str(e)}")
