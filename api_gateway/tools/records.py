"""
Tools MCP para legajos (records) del módulo RLM.
Reutiliza servicios existentes de services/rlm/.
"""
import logging
from typing import Dict, Any, Optional
from api_gateway.context import MCPContext
from services.rlm.records import get_record, list_records
from services.rlm.registries import list_registries

logger = logging.getLogger(__name__)


def search_records(
    ctx: MCPContext,
    family_code: Optional[str] = None,
    search: Optional[str] = None,
    state: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    """
    Buscar legajos con filtros.

    Args:
        ctx: Contexto MCP con schema_name y user_id
        family_code: Código de registro (ARQ, LUM, ORD)
        search: Texto de búsqueda
        state: Filtro por estado
        page: Número de página
        page_size: Tamaño de página (max 100)

    Returns:
        Dict con records, total, page, page_size, total_pages
    """
    logger.info(f"[MCP] search_records - schema={ctx.schema_name}, family={family_code}, search={search}")

    if page_size > 100:
        raise ValueError("page_size máximo es 100")

    return list_records(
        user_id=ctx.user_id,
        schema_name=ctx.schema_name,
        registry_code=family_code,
        state=state,
        search=search,
        page=page,
        page_size=page_size,
    )


def get_record_detail(
    ctx: MCPContext,
    record_id: str,
) -> Dict[str, Any]:
    """
    Obtener detalle completo de un legajo.

    Args:
        ctx: Contexto MCP
        record_id: UUID del legajo

    Returns:
        Dict con detalle del legajo + permisos
    """
    logger.info(f"[MCP] get_record - schema={ctx.schema_name}, record_id={record_id}")

    return get_record(
        record_id=record_id,
        user_id=ctx.user_id,
        schema_name=ctx.schema_name,
    )


def get_registry_families(
    ctx: MCPContext,
) -> Dict[str, Any]:
    """
    Listar familias de registros disponibles.

    Args:
        ctx: Contexto MCP

    Returns:
        Dict con registries y total
    """
    logger.info(f"[MCP] get_registry_families - schema={ctx.schema_name}")

    return list_registries(
        user_id=ctx.user_id,
        schema_name=ctx.schema_name,
    )
