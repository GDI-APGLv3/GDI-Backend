"""Servicios para tipos de documentos - REFACTORIZADO"""

from shared.logging import get_logger
from typing import List, Dict, Any, Optional
from database import get_db_connection
from ..core.queries import get_all_document_types_query

logger = get_logger(__name__)


def get_all_document_types(schema_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Obtiene todos los tipos de documento activos.

    Args:
        schema_name: Nombre del schema del tenant (ej: '100_test')

    NOTA: Usa get_db_connection(schema_name) para compatibilidad con PgBouncer transaction mode.
    """
    logger.info(f"Obteniendo tipos de documentos para schema: {schema_name}")

    # CORREGIDO: Usar schema_name en get_db_connection para SET LOCAL automatico
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            cursor.execute(get_all_document_types_query())
            rows = cursor.fetchall()
            types = [dict(row) for row in rows]

            logger.info(f"Obtenidos {len(types)} tipos de documentos")
            return types
