"""
Endpoint para descargar todos los documentos de un expediente como ZIP.
"""

import asyncio
import zipfile
import tempfile

import httpx
from fastapi import APIRouter, Path, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse

from auth import get_current_user
from models.schemas import AuthenticatedUser
from services.case_service import CaseService
from services.cases.documents import get_case_documents
from shared.exceptions import (
    exception_to_http_exception,
    NotFoundError,
    ValidationError,
    BusinessLogicError,
)
from shared.utils import get_authenticated_user
from shared.dependencies import get_tenant_schema
from shared.logging import get_logger
from config.constants import CASE_NOT_FOUND_ERROR, USER_UNAUTHENTICATED_ERROR

logger = get_logger(__name__)
router = APIRouter(tags=["expedientes"])


async def _download_pdf(
    client: httpx.AsyncClient,
    order_number: int,
    official_number: str,
    pdf_url: str,
) -> tuple[int, str, bytes] | None:
    """
    Descarga un PDF desde R2 usando la URL presignada.

    Retorna (order_number, official_number, bytes) o None si falla.
    """
    try:
        response = await client.get(pdf_url, timeout=30.0)
        response.raise_for_status()
        return (order_number, official_number, response.content)
    except Exception as exc:
        logger.warning(
            f"No se pudo descargar el documento {official_number} "
            f"(order={order_number}): {exc}"
        )
        return None


@router.get("/{case_id}/download-zip")
async def download_case_zip(
    request: Request,
    case_id: str = Path(..., description="ID del expediente"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
):
    """
    Descarga todos los documentos oficiales del expediente como un archivo ZIP.

    Los PDFs se nombran con formato: "001 - {official_number}.pdf"
    El ZIP se nombra con el número del expediente.
    """
    try:
        # 1. Validar usuario autenticado
        tenant_user_id = getattr(request.state, "tenant_user_id", None)
        if not tenant_user_id:
            raise ValidationError(USER_UNAUTHENTICATED_ERROR)

        logger.info(f"Download ZIP request - Case: {case_id[:8]}, User: {tenant_user_id[:8]}")

        db_user_id = await get_authenticated_user(tenant_user_id, schema_name=schema_name)

        # 2. Verificar permisos de visualización (404 para no revelar existencia)
        if not await CaseService.can_user_view_case(case_id, db_user_id, schema_name=schema_name):
            logger.warning(f"Access denied for ZIP download: user={db_user_id[:8]}, case={case_id[:8]}")
            raise NotFoundError(CASE_NOT_FOUND_ERROR)

        # 3. Obtener número del expediente (para el nombre del ZIP)
        from database import fetch_all
        from services.case_queries import get_case_number_query

        case_row = await fetch_all(get_case_number_query(), case_id, schema_name=schema_name)
        if not case_row:
            raise NotFoundError(CASE_NOT_FOUND_ERROR)
        case_number = case_row[0]["case_number"]

        # 4. Obtener documentos del expediente
        documents_data = await get_case_documents(case_id, schema_name=schema_name)
        official_docs = documents_data.get("official", [])

        # Filtrar solo documentos activos con official_number y pdf_url
        docs_to_download = [
            d for d in official_docs
            if d.get("is_active") and d.get("official_number") and d.get("pdf_url")
        ]

        # Ordenar por order_number ASC
        docs_to_download.sort(key=lambda d: d.get("order", 0))

        if not docs_to_download:
            raise HTTPException(
                status_code=404,
                detail="El expediente no tiene documentos vinculados"
            )

        logger.info(
            f"Downloading ZIP for case {case_number}: "
            f"{len(docs_to_download)} documents to include"
        )

        # 5. Descargar todos los PDFs en paralelo
        async with httpx.AsyncClient() as client:
            tasks = [
                _download_pdf(
                    client,
                    doc["order"],
                    doc["official_number"],
                    doc["pdf_url"],
                )
                for doc in docs_to_download
            ]
            results = await asyncio.gather(*tasks)

        # Filtrar los que fallaron (None)
        successful_downloads = [r for r in results if r is not None]

        if not successful_downloads:
            logger.error(f"All PDF downloads failed for case {case_number}")
            raise HTTPException(
                status_code=502,
                detail="No se pudo descargar ningún documento del expediente"
            )

        # Ordenar por order_number (por si asyncio.gather alteró el orden)
        successful_downloads.sort(key=lambda x: x[0])

        logger.info(
            f"Downloaded {len(successful_downloads)}/{len(docs_to_download)} "
            f"PDFs for case {case_number}"
        )

        # 6. Construir ZIP en memoria (SpooledTemporaryFile para expedientes grandes)
        MAX_ZIP_BYTES = 500 * 1024 * 1024  # 500 MB
        total_bytes = sum(len(r[2]) for r in successful_downloads)
        if total_bytes > MAX_ZIP_BYTES:
            raise BusinessLogicError(
                f"El expediente supera el límite de descarga "
                f"({total_bytes // (1024*1024)} MB > 500 MB máximo)"
            )

        tmp = tempfile.SpooledTemporaryFile(max_size=50_000_000)

        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_STORED) as zf:
            for order_number, official_number, pdf_bytes in successful_downloads:
                filename = f"{order_number:03d} - {official_number}.pdf"
                zf.writestr(filename, pdf_bytes)

        tmp.seek(0)

        # 7. Nombre seguro para el archivo ZIP (remover caracteres problemáticos)
        safe_case_number = case_number.replace("/", "-").replace("\\", "-")
        zip_filename = f"{safe_case_number}.zip"

        logger.info(f"Serving ZIP: {zip_filename} ({len(successful_downloads)} files)")

        def iterfile():
            chunk_size = 65536  # 64 KB
            while True:
                chunk = tmp.read(chunk_size)
                if not chunk:
                    break
                yield chunk
            tmp.close()

        return StreamingResponse(
            iterfile(),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{zip_filename}"'
            },
        )

    except HTTPException:
        raise
    except (ValidationError, NotFoundError, BusinessLogicError) as e:
        logger.error(f"Error in download_case_zip: {str(e)}")
        raise exception_to_http_exception(e)
    except Exception as e:
        logger.error(f"Unexpected error in download_case_zip: {str(e)}", exc_info=True)
        raise exception_to_http_exception(
            BusinessLogicError("Error al generar el ZIP del expediente")
        )
