from shared.logging import get_logger
from typing import Dict, Any, Optional
from api_gateway.context import MCPContext
from services.memos.retrieval import get_received_memos, get_sent_memos, get_archived_memos
from shared.exceptions import AuthorizationError, NotFoundError, GDIBaseException

logger = get_logger(__name__)


async def get_memos(
    ctx: MCPContext,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None
) -> Dict[str, Any]:
    logger.info(f"[MCP] get_memos - user_id={user_id}, schema={ctx.schema_name}, page={page}")

    if not user_id:
        raise ValueError("user_id es requerido")
    if page_size > 100:
        raise ValueError("page_size máximo es 100")

    try:
        result = await get_received_memos(
            user_id,
            schema_name=ctx.schema_name,
            page=page,
            page_size=page_size,
            search=search
        )

        total = result.get("pagination", {}).get("total", 0)
        logger.info(f"[MCP] get_memos - {total} memos encontrados")

        return result

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_memos - error: {e}")
        raise RuntimeError("Error obteniendo memos")


async def get_sent_memos_tool(
    ctx: MCPContext,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None
) -> Dict[str, Any]:
    logger.info(f"[MCP] get_sent_memos - user_id={user_id}, schema={ctx.schema_name}, page={page}")

    if not user_id:
        raise ValueError("user_id es requerido")
    if page_size > 100:
        raise ValueError("page_size máximo es 100")

    try:
        result = await get_sent_memos(
            user_id,
            schema_name=ctx.schema_name,
            page=page,
            page_size=page_size,
            search=search
        )

        total = result.get("pagination", {}).get("total", 0)
        logger.info(f"[MCP] get_sent_memos - {total} memos enviados encontrados")

        return result

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_sent_memos - error: {e}")
        raise RuntimeError("Error obteniendo memos enviados")


async def get_archived_memos_tool(
    ctx: MCPContext,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None
) -> Dict[str, Any]:
    logger.info(f"[MCP] get_archived_memos - user_id={user_id}, schema={ctx.schema_name}, page={page}")

    if not user_id:
        raise ValueError("user_id es requerido")
    if page_size > 100:
        raise ValueError("page_size máximo es 100")

    try:
        result = await get_archived_memos(
            user_id,
            schema_name=ctx.schema_name,
            page=page,
            page_size=page_size,
            search=search
        )

        total = result.get("pagination", {}).get("total", 0)
        logger.info(f"[MCP] get_archived_memos - {total} memos archivados encontrados")

        return result

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_archived_memos - error: {e}")
        raise RuntimeError("Error obteniendo memos archivados")


async def get_memo_detail(
    ctx: MCPContext,
    memo_id: str,
    user_id: str
) -> Dict[str, Any]:
    logger.info(f"[MCP] get_memo_detail - memo_id={memo_id}, user_id={user_id}, schema={ctx.schema_name}")

    if not memo_id:
        raise ValueError("memo_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        from services.memos import get_memo_detail as _get_memo_detail

        result = await _get_memo_detail(
            document_id=memo_id,
            requesting_user_id=user_id,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] get_memo_detail - detalle obtenido para memo {memo_id}")

        return result

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_memo_detail - error: {e}")
        raise RuntimeError("Error obteniendo detalle de memo")
