
from shared.logging import get_logger
from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException
from models.tags import Tags
from auth import get_current_user
from models.schemas import AuthenticatedUser
from shared.dependencies import get_tenant_schema
from shared.exceptions import exception_to_http_exception
from services.documents.importing.import_service import create_imported_document


logger = get_logger("create_imported")

router = APIRouter(tags=[Tags.DOCUMENTOS])


@router.post(
    "/documents/import",
    summary="Crear documento importado",
    description="""
    Crea un documento importado subiendo un PDF existente.

    **Flujo:**
    1. Valida que el tipo de documento sea de tipo 'Importado'
    2. Procesa el PDF con PDFComposer (agrega hoja de firmas)
    3. Almacena en R2
    4. Crea registro en BD (content=NULL)

    **Restricciones:**
    - Tamaño máximo: 10MB
    - Formato: PDF válido
    - Tipo de documento: debe ser 'Importado' (ej: ANEXO)

    **Casos de uso:**
    - Usuario sube un informe en PDF
    - Usuario importa un documento escaneado
    - Usuario adjunta un PDF de terceros para firmar
    """
)
async def create_imported_document_endpoint(
    document_type_acronym: str = Form(..., description="Acrónimo del tipo de documento (ej: ANEXO)"),
    reference: str = Form(..., description="Referencia del documento"),
    pdf_file: UploadFile = File(..., description="Archivo PDF a importar"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
):
    """
    Crea un documento importado subiendo un PDF.

    Args:
        document_type_acronym: Acrónimo del tipo de documento
        reference: Referencia del documento
        pdf_file: Archivo PDF subido
        current_user: Usuario autenticado
        schema_name: Schema del tenant

    Returns:
        Dict con document_id, status, pdf_url

    Raises:
        HTTPException 400: Si tipo no es Importado o archivo inválido
        HTTPException 404: Si tipo de documento no existe
        HTTPException 500: Si falla procesamiento o almacenamiento
    """
    try:
        logger.info(
            f"Usuario {current_user.user_id} creando documento importado "
            f"tipo '{document_type_acronym}' en schema '{schema_name}'"
        )

        if not pdf_file.content_type or pdf_file.content_type != 'application/pdf':
            raise HTTPException(
                status_code=400,
                detail=f"El archivo debe ser un PDF. Content-Type recibido: {pdf_file.content_type}"
            )

        MAX_SIZE = 10 * 1024 * 1024

        result = await create_imported_document(
            user_id=current_user.user_id,
            document_type_acronym=document_type_acronym,
            reference=reference,
            pdf_file=pdf_file,
            schema_name=schema_name
        )

        logger.info(f"Documento importado {result['document_id']} creado exitosamente")

        return {
            "success": True,
            "document_id": result["document_id"],
            "status": result["status"],
            "pdf_url": result["pdf_url"],
            "message": result["message"]
        }

    except HTTPException:
        raise

    except Exception as e:
        logger.error(f"Error creando documento importado: {type(e).__name__} - {e}")
        raise exception_to_http_exception(e)
