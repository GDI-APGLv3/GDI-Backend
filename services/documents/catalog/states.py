"""Servicios para estados de documentos - REFACTORIZADO"""

from shared.logging import get_logger
from typing import List, Dict, Any
from database import get_db_connection
from shared.exceptions import DatabaseError
from ..core.queries import (
    get_all_display_states_query,
    get_display_state_by_code_query,
    get_all_state_mappings_query
)

logger = get_logger(__name__)

DEFAULT_STATES = [
    {"display_state": "En edicion"},
    {"display_state": "En proceso de firma"},
    {"display_state": "Firmar ahora"},
    {"display_state": "Firmado"},
    {"display_state": "Rechazado"}
]

STATE_CODE_MAPPING = {
    "draft": "En edicion",
    "editing": "En edicion",
    "pending_signature": "En proceso de firma",
    "signing_process": "En proceso de firma",
    "sent_to_sign": "Firmar ahora",
    "sign_now": "Firmar ahora",
    "signed": "Firmado",
    "completed": "Firmado",
    "rejected": "Rechazado"
}


def get_all_display_states(*, schema_name: str) -> List[Dict[str, Any]]:
    """Obtiene todos los estados de visualizacion desde base de datos."""
    logger.info("Obteniendo estados de visualizacion")

    try:
        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                cursor.execute(get_all_display_states_query())
                rows = cursor.fetchall()
                states = [dict(row) for row in rows]

                logger.info(f"Obtenidos {len(states)} estados de visualizacion")
                return states

    except Exception as e:
        logger.error(f"Error obteniendo estados de BD: {e}")
        logger.warning(f"Usando estados por defecto ({len(DEFAULT_STATES)} estados)")
        return DEFAULT_STATES

def get_display_state_name(state_code: str, *, schema_name: str) -> str:
    """Obtiene el nombre de visualizacion para un codigo de estado."""
    logger.info(f"Obteniendo nombre de estado para codigo: {state_code}")

    try:
        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                cursor.execute(get_display_state_by_code_query(), (state_code.upper(),))
                result = cursor.fetchone()

                if result:
                    return result['display_state_name']

    except Exception as e:
        logger.error(f"Error obteniendo estado de BD: {e}")

    fallback = STATE_CODE_MAPPING.get(
        state_code.lower(),
        state_code.replace("_", " ").title()
    )
    logger.warning(f"Usando mapeo por defecto para '{state_code}': {fallback}")
    return fallback

def get_all_state_mappings(*, schema_name: str) -> Dict[str, str]:
    """Obtiene todos los mapeos de codigos a nombres desde base de datos."""
    logger.info("Obteniendo mapeos de estados")

    try:
        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                cursor.execute(get_all_state_mappings_query())
                rows = cursor.fetchall()
                mappings = {
                    row['display_state_code'].lower(): row['display_state_name']
                    for row in rows
                }
                logger.info(f"Obtenidos {len(mappings)} mapeos de estados")
                return mappings

    except Exception as e:
        logger.error(f"Error obteniendo mapeos de BD: {e}")
        logger.warning(f"Usando mapeo por defecto ({len(STATE_CODE_MAPPING)} estados)")
        return STATE_CODE_MAPPING
