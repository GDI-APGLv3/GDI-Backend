"""Servicios para tipos de documentos - REFACTORIZADO
MIGRADO: Fase 6 asyncpg
"""

from shared.logging import get_logger
from typing import List, Dict, Any, Optional
from database import fetch_all
from ..core.queries import get_all_document_types_query

logger = get_logger(__name__)


async def get_all_document_types(schema_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Obtiene todos los tipos de documento activos.

    Args:
        schema_name: Nombre del schema del tenant (ej: '100_test')
    """
    logger.info(f"Obteniendo tipos de documentos para schema: {schema_name}")

    rows = await fetch_all(get_all_document_types_query(), schema_name=schema_name)
    types = [dict(row) for row in rows]

    logger.info(f"Obtenidos {len(types)} tipos de documentos")
    return types
