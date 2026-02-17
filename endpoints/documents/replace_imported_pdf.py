"""
Endpoint para reemplazar el PDF de un documento importado.

Solo permitido en documentos en estado draft y tipo Importado.
"""

from shared.logging import get_logger
from uuid import UUID
from fastapi import APIRouter, Depends, File, UploadFile, HTTPException
from models.tags import Tags
from auth import get_current_user
from models.schemas import AuthenticatedUser
from shared.dependencies import get_tenant_schema
from shared.exceptions import exception_to_http_exception
from services.documents.importing.import_service import replace_imported_pdf


logger = get_logger("replace_imported_pdf")

router = APIRouter(tags=[Tags.DOCUMENTOS])


@router.put(
    "/documents/{document_id}/imported-pdf",
    summary="Reemplazar PDF de documento importado",
    description="""
    Reemplaza el PDF de un documento importado existente.

    **Restricciones:**
    - Solo permitido en documentos en estado 'draft'
    - Solo para documentos de tipo 'Importado'
    - Solo el creador puede reemplazar el PDF
    - Tamaño máximo: 25MB

    **Flujo:**
    1. Valida permisos y estado
    2. Procesa nuevo PDF con PDFComposer
    3. Elimina PDF anterior de R2
    4. Sube nuevo PDF a R2
    5. Actualiza timestamp en BD

    **Casos de uso:**
    - Usuario se equivocó de archivo
    - Usuario necesita actualizar versión del PDF
    - Usuario corrigió errores en el documento original
    """
)
async def replace_imported_pdf_endpoint(
    document_id: UUID,
    pdf_file: UploadFile = File(..., description="Nuevo archivo PDF"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Reemplaza el PDF de un documento importado.

    Args:
        document_id: UUID del documento
        pdf_file: Nuevo archivo PDF
        current_user: Usuario autenticado
        schema_name: Schema del tenant

    Returns:
        Dict con success, pdf_url

    Raises:
        HTTPException 400: Si archivo inválido o tipo incorrecto
        HTTPException 403: Si usuario no tiene permiso
        HTTPException 404: Si documento no existe
        HTTPException 409: Si estado no es draft
        HTTPException 500: Si falla procesamiento o almacenamiento
    """
    try:
        logger.info(
            f"Usuario {current_user.user_id} reemplazando PDF de documento {document_id} "
            f"en schema '{schema_name}'"
        )

        # Validar content-type
        if not pdf_file.content_type or pdf_file.content_type != 'application/pdf':
            raise HTTPException(
                status_code=400,
                detail=f"El archivo debe ser un PDF. Content-Type recibido: {pdf_file.content_type}"
            )

        result = await replace_imported_pdf(
            document_id=str(document_id),
            pdf_file=pdf_file,
            user_id=current_user.user_id,
            schema_name=schema_name
        )

        logger.info(f"PDF de documento {document_id} reemplazado exitosamente")

        return {
            "success": True,
            "document_id": str(document_id),
            "pdf_url": result["pdf_url"],
            "message": result["message"]
        }

    except HTTPException:
        raise

    except Exception as e:
        logger.error(f"Error reemplazando PDF: {type(e).__name__} - {e}")
        raise exception_to_http_exception(e)
