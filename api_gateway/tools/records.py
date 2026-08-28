from shared.logging import get_logger
from typing import Dict, Any, Optional
from api_gateway.context import MCPContext
from services.rlm.records import get_record, list_records
from services.rlm.registries import list_registries

logger = get_logger(__name__)


async def search_records(
    ctx: MCPContext,
    family_code: Optional[str] = None,
    search: Optional[str] = None,
    state: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
) -> Dict[str, Any]:
    logger.info(f"[MCP] search_records - schema={ctx.schema_name}, family={family_code}, search={search}")

    if page_size > 100:
        raise ValueError("page_size máximo es 100")

    return await list_records(
        user_id=ctx.user_id,
        schema_name=ctx.schema_name,
        registry_code=family_code,
        state=state,
        search=search,
        page=page,
        page_size=page_size,
    )


async def get_record_detail(
    ctx: MCPContext,
    record_id: str,
) -> Dict[str, Any]:
    logger.info(f"[MCP] get_record - schema={ctx.schema_name}, record_id={record_id}")

    return await get_record(
        record_id=record_id,
        user_id=ctx.user_id,
        schema_name=ctx.schema_name,
    )


async def get_registry_families(
    ctx: MCPContext,
) -> Dict[str, Any]:
    logger.info(f"[MCP] get_registry_families - schema={ctx.schema_name}")

    return await list_registries(
        user_id=ctx.user_id,
        schema_name=ctx.schema_name,
    )
