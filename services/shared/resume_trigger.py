"""Resume trigger - Encola documentos para generacion de resumen."""

import asyncio
from shared.logging import get_logger
import os

logger = get_logger(__name__)


async def _enqueue_document_resume(document_id: str, schema_name: str) -> None:
    """Encola documento para generar resumen automatico. Best-effort, no bloquea.

    Args:
        document_id: UUID del documento
        schema_name: Schema del tenant
    """
    import httpx

    agent_url = os.getenv('AGENT_URL')
    agent_api_key = os.getenv('INTERNAL_API_KEY')

    # Best-effort: si no está configurado, solo loguear y retornar
    if not agent_url or not agent_api_key:
        logger.warning("AGENT_URL o INTERNAL_API_KEY no configurado. Saltando generación de resumen.")
        return

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{agent_url}/api/v1/resume-document",
                json={"document_id": document_id, "schema_name": schema_name},
                headers={"X-API-Key": agent_api_key},
            )
            if response.status_code == 202:
                logger.info(f"Documento {document_id[:8]}... encolado para resumen")
            else:
                logger.warning(f"Resume endpoint returned {response.status_code}")
    except Exception as e:
        # Best-effort: log y continua sin fallar el proceso principal
        logger.warning(f"Failed to enqueue resume for {document_id[:8]}...: {e}")


def enqueue_resume_fire_and_forget(document_id: str, schema_name: str) -> None:
    """Fire-and-forget: encola resumen async sin bloquear el proceso principal.

    Args:
        document_id: UUID del documento
        schema_name: Schema del tenant
    """
    asyncio.create_task(_enqueue_document_resume(document_id, schema_name))
