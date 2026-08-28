
from shared.logging import get_logger
from typing import List, Dict, Any, Optional
from database import fetch_all
from ..core.queries import get_all_document_types_query

logger = get_logger(__name__)


async def get_all_document_types(schema_name: Optional[str] = None) -> List[Dict[str, Any]]:
    logger.info(f"Obteniendo tipos de documentos para schema: {schema_name}")

    rows = await fetch_all(get_all_document_types_query(), schema_name=schema_name)
    types = []
    for row in rows:
        t = dict(row)
        t['is_public'] = t.pop('visibility', None) == 'publico'
        types.append(t)

    logger.info(f"Obtenidos {len(types)} tipos de documentos")
    return types
