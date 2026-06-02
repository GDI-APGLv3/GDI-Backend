"""Servicios para estados de documentos - REFACTORIZADO
MIGRADO: Fase 6 asyncpg
"""

from shared.logging import get_logger
from typing import List, Dict, Any
from database import fetch_all, fetch_one
from shared.exceptions import DatabaseError
from ..core.queries import (
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


async def get_all_display_states(*, schema_name: str) -> List[Dict[str, Any]]:
    """Obtiene todos los estados de visualizacion desde base de datos.

    BACKEND-08: Reutiliza get_all_state_mappings_query (que ya trae code+name) en vez
    de ejecutar una query separada con get_all_display_states_query (que solo traía name).
    Una sola query a document_display_states para ambas funciones.
    """
    logger.info("Obteniendo estados de visualizacion")

    try:
        rows = await fetch_all(get_all_state_mappings_query(), schema_name=schema_name)
        states = [{"display_state": row["display_state_name"]} for row in rows]
        logger.info(f"Obtenidos {len(states)} estados de visualizacion")
        return states

    except Exception as e:
        logger.error(f"Error obteniendo estados de BD: {e}")
        logger.warning(f"Usando estados por defecto ({len(DEFAULT_STATES)} estados)")
        return DEFAULT_STATES

async def get_display_state_name(state_code: str, *, schema_name: str) -> str:
    """Obtiene el nombre de visualizacion para un codigo de estado."""
    logger.info(f"Obteniendo nombre de estado para codigo: {state_code}")

    try:
        result = await fetch_one(get_display_state_by_code_query(), state_code.upper(), schema_name=schema_name)

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

async def get_all_state_mappings(*, schema_name: str) -> Dict[str, str]:
    """Obtiene todos los mapeos de codigos a nombres desde base de datos."""
    logger.info("Obteniendo mapeos de estados")

    try:
        rows = await fetch_all(get_all_state_mappings_query(), schema_name=schema_name)
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
