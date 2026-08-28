
import asyncio
from shared.logging import get_logger
import os

logger = get_logger(__name__)


async def _enqueue_document_resume(document_id: str, schema_name: str) -> None:
    import httpx

    agent_url = os.getenv('AGENT_URL')
    agent_api_key = os.getenv('INTERNAL_API_KEY')

    if not agent_url or not agent_api_key:
        logger.warning("AGENT_URL o INTERNAL_API_KEY no configurado. Saltando generación de resumen.")
        return

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
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
        logger.warning(f"Failed to enqueue resume for {document_id[:8]}...: {e}")


def enqueue_resume_fire_and_forget(document_id: str, schema_name: str) -> None:
    asyncio.create_task(_enqueue_document_resume(document_id, schema_name))


async def _enqueue_record_resume(record_id: str, schema_name: str) -> None:
    import httpx

    agent_url = os.getenv('AGENT_URL')
    agent_api_key = os.getenv('INTERNAL_API_KEY')

    if not agent_url or not agent_api_key:
        logger.warning("AGENT_URL o INTERNAL_API_KEY no configurado. Saltando generación de resumen de legajo.")
        return

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{agent_url}/api/v1/resume-record",
                json={"record_id": record_id, "schema_name": schema_name},
                headers={"X-API-Key": agent_api_key},
            )
            if response.status_code == 202:
                logger.info(f"Legajo {record_id[:8]}... encolado para resumen")
            else:
                logger.warning(f"Resume-record endpoint returned {response.status_code}")
    except Exception as e:
        logger.warning(f"Failed to enqueue resume for record {record_id[:8]}...: {e}")


def enqueue_record_resume_fire_and_forget(record_id: str, schema_name: str) -> None:
    asyncio.create_task(_enqueue_record_resume(record_id, schema_name))
