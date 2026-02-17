"""
Servicio de negocio para el Dashboard (Feed de actividad).
Contiene toda la lógica de negocio para obtener el feed y estadísticas.
"""

from typing import Dict, Any, List, Optional
from shared.logging import get_logger
import math

from database import execute_query
from services.dashboard_queries import (
    get_feed_movements_query,
    get_cases_in_sector_count_query
)
from shared.exceptions import ValidationError, NotFoundError
from config.constants import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from services.shared.sector_utils import get_user_sector_ids

logger = get_logger(__name__)


class DashboardService:
    """Servicio para el Dashboard de actividad"""

    # Alias para backward compatibility
    _get_user_sector_ids = staticmethod(get_user_sector_ids)

    @staticmethod
    def _generate_movement_message(movement_type: str, user_name: str, reason: str) -> str:
        """
        Genera un mensaje legible para el movimiento.

        Args:
            movement_type: Tipo de movimiento (creation, transfer, etc.)
            user_name: Nombre del usuario que realizó el movimiento
            reason: Razón del movimiento

        Returns:
            Mensaje legible
        """
        messages = {
            'creation': f"{user_name} creó el expediente",
            'transfer': f"{user_name} transfirió el expediente",
            'assignment': f"{user_name} asignó el expediente",
            'document_link': f"{user_name} vinculó un documento",
            'status_change': f"{user_name} cambió el estado del expediente",
            'subsanacion': f"{user_name} subsanó un documento"
        }

        return messages.get(movement_type, f"{user_name} realizó una acción")

    @staticmethod
    def get_feed(
        user_id: str,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
        *,
        schema_name: str
    ) -> Dict[str, Any]:
        """
        Obtiene el feed de movimientos para el usuario.

        Args:
            user_id: UUID del usuario
            page: Numero de pagina (1-based)
            page_size: Elementos por pagina
            schema_name: Schema del tenant (requerido)

        Returns:
            Dict con items, total, page, page_size, total_pages
        """

        # Validar paginación
        page = max(1, page)
        page_size = min(max(1, page_size), MAX_PAGE_SIZE)
        offset = (page - 1) * page_size

        logger.info(f"Getting feed for user {user_id[:8]}... page={page}, size={page_size}")

        # Obtener sectores del usuario
        sector_ids = DashboardService._get_user_sector_ids(user_id, schema_name=schema_name)

        if not sector_ids:
            logger.warning(f"User {user_id[:8]}... has no viewable sectors")
            return {
                "items": [],
                "total": 0,
                "page": page,
                "page_size": page_size,
                "total_pages": 0
            }

        # Ejecutar query del feed
        # La query usa CTEs internas para sectores del usuario, solo necesita user_id 2 veces
        # y luego page_size y offset
        query = get_feed_movements_query("")
        params = (user_id, user_id, page_size, offset)

        results = execute_query(query, params, schema_name=schema_name)

        if not results:
            return {
                "items": [],
                "total": 0,
                "page": page,
                "page_size": page_size,
                "total_pages": 0
            }

        # Procesar resultados
        total = results[0]['total_count'] if results else 0
        total_pages = math.ceil(total / page_size) if total > 0 else 0

        items = []
        for row in results:
            user_name = row.get('user_name') or 'Sistema'
            movement_type = row.get('movement_type', 'unknown')

            item = {
                "movement_id": str(row['movement_id']),
                "movement_type": movement_type,
                "message": DashboardService._generate_movement_message(
                    movement_type,
                    user_name,
                    row.get('reason', '')
                ),
                "reason": row.get('reason', ''),
                "created_at": row['created_at'].isoformat() if row.get('created_at') else None,
                "is_active": row.get('is_active', False),
                "case": {
                    "id": str(row['case_id']),
                    "case_number": row.get('case_number', ''),
                    "reference": row.get('case_reference', ''),
                    "case_type": row.get('case_type', ''),
                    "case_type_name": row.get('case_type_name', ''),
                    "ai_summary": row.get('case_ai_summary')
                },
                "user": {
                    "name": user_name,
                    "photo_url": row.get('user_photo'),
                    "sector": row.get('user_sector', '')
                },
                "supporting_document": None
            }

            # Agregar documento de soporte si existe
            if row.get('doc_number'):
                item["supporting_document"] = {
                    "official_number": row['doc_number'],
                    "reference": row.get('doc_reference', ''),
                    "ai_summary": row.get('doc_ai_summary')
                }

            items.append(item)

        logger.info(f"Found {total} movements, returning {len(items)} items")

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages
        }

    @staticmethod
    def get_stats(user_id: str, *, schema_name: str) -> Dict[str, Any]:
        """
        Obtiene estadisticas del dashboard para el usuario.

        Args:
            user_id: UUID del usuario
            schema_name: Schema del tenant (requerido)

        Returns:
            Dict con cases_in_sector
        """

        logger.info(f"Getting stats for user {user_id[:8]}...")

        # Contar expedientes donde el usuario es admin
        query = get_cases_in_sector_count_query()
        result = execute_query(query, (user_id,), fetch_one=True, schema_name=schema_name)

        cases_in_sector = result['count'] if result else 0

        logger.info(f"User has {cases_in_sector} cases in sector")

        return {
            "cases_in_sector": cases_in_sector
        }
