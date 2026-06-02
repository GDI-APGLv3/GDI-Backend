"""
Servicio para obtener URLs de documentos oficiales - REFACTORIZADO
"""

from shared.logging import get_logger
from typing import Dict, Any
from fastapi.concurrency import run_in_threadpool

from database import fetch_one
from shared.exceptions import DocumentNotFoundError, ValidationError, DocumentStateError
from shared.validation import validate_document_id
from config.constants import SIGNED_DOCUMENT_STATE, CLOUDFLARE_URL_EXPIRATION
from ..core.queries import get_official_document_info_query

logger = get_logger(__name__)


async def get_official_document_url(document_id: str, *, schema_name: str) -> Dict[str, Any]:
    """Obtiene URL temporal de descarga para documento oficial firmado."""
    logger.info(f"Obteniendo URL de documento oficial {document_id}")

    validation_error = await validate_document_id(document_id, schema_name=schema_name)
    if validation_error:
        raise ValidationError(validation_error)

    official_number = await _fetch_official_document_info(document_id, schema_name=schema_name)
    pdf_url = await _generate_cloudflare_url(official_number, schema_name=schema_name)

    logger.info(f"URL generada exitosamente para documento {document_id}")

    return {
        "pdf_url": pdf_url,
        "official_number": official_number,
        "document_id": document_id,
        "expires_in": CLOUDFLARE_URL_EXPIRATION
    }


async def _fetch_official_document_info(document_id: str, *, schema_name: str) -> str:
    """Obtiene informacion del documento oficial y valida su estado."""
    doc_data = await fetch_one(
        get_official_document_info_query(),
        document_id,
        schema_name=schema_name,
    )

    if not doc_data:
        raise DocumentNotFoundError(
            f"Documento {document_id} no existe o no es un documento oficial"
        )

    official_number = doc_data["official_number"]
    status = doc_data["status"]

    if not official_number:
        raise ValidationError("El documento no tiene numero oficial asignado")

    if status != SIGNED_DOCUMENT_STATE:
        raise DocumentStateError(
            f"Documento debe estar firmado para descargar",
            current_state=status,
            required_state=SIGNED_DOCUMENT_STATE
        )

    return official_number


async def _generate_cloudflare_url(official_number: str, *, schema_name: str) -> str:
    """Genera URL firmada desde Cloudflare R2."""
    from services.storage.cloudflare import get_tenant_r2_client
    r2_client = await get_tenant_r2_client(schema_name=schema_name)
    pdf_url = await run_in_threadpool(r2_client.get_oficial_url, official_number)

    if not pdf_url:
        raise ValidationError("No se pudo obtener la URL del documento desde Cloudflare R2")

    return pdf_url
