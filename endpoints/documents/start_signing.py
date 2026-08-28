from uuid import UUID
from shared.logging import get_logger
from fastapi import APIRouter, Path, Depends, Request
from models.documents.signing import StartSigningProcessResponse
from models.tags import Tags
from auth import get_current_user
from models.schemas import AuthenticatedUser
from services.documents.signing import start_document_signing_process
from shared.exceptions import ValidationError, ExternalServiceError, exception_to_http_exception
from shared.dependencies import get_tenant_schema

logger = get_logger("start_signing")
router = APIRouter(tags=[Tags.DOCUMENTOS])

@router.post(
    "/documents/{document_id}/start-signing-process",
    response_model=StartSigningProcessResponse,
    summary="Iniciar proceso de firma",
    description="""Inicia el proceso de firma de un documento, cambiando su estado de 'draft' a 'sent_to_sign'.

## Proceso

1. Valida que el usuario sea el creador del documento
2. Verifica que exista al menos un firmante y un numerador
3. Genera el PDF final del documento
4. Cambia el estado a 'sent_to_sign'
5. Notifica a los firmantes que tienen un documento pendiente

## Requisitos

- Solo el **creador** del documento puede iniciar este proceso
- El documento debe estar en estado **draft**
- Debe tener al menos un **firmante** asignado
- Debe tener un **numerador** asignado (firmante con is_numerator=true)

## Flujo para NOTAS

Para documentos tipo NOTA, este endpoint:
1. Genera el PDF de la nota con los recipients definidos
2. Cambia estado a 'sent_to_sign'
3. Los firmantes reciben la nota para firmar

**Importante**: Los recipients (TO, CC, BCC) ya deben estar definidos desde la creacion del documento (POST /documents). No se pueden modificar en este punto.

Una vez que todos los firmantes firmen (via POST /documents/{id}/super-sign), la NOTA se oficializa y los recipients pueden verla en su bandeja de notas recibidas.

## Siguiente Paso

Despues de iniciar el proceso de firma, cada firmante debe llamar a:
- **POST /documents/{id}/super-sign** para firmar el documento
""",
    responses={
        200: {"description": "Proceso de firma iniciado exitosamente"},
        400: {"description": "Documento inválido o sin firmantes/numerador"},
        403: {"description": "Usuario no autorizado (debe ser el creador)"},
        404: {"description": "Documento no encontrado"},
        409: {"description": "Documento no puede firmarse en su estado actual"},
        502: {"description": "Error al generar PDF con servicio externo"}
    }
)
async def start_signing_process(
    request: Request,
    document_id: UUID = Path(..., description="UUID del documento"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
) -> StartSigningProcessResponse:
    """
    Inicia el proceso de firma para un documento.
    
    Args:
        document_id: UUID único del documento
        current_user: Usuario autenticado que inicia el proceso
        
    Returns:
        StartSigningProcessResponse: Resultado del proceso de inicio
        
    Raises:
        HTTPException: Para errores de validación, autorización o servicios externos
    """
    document_id = str(document_id)
    try:
        logger.info(f"Usuario {request.state.tenant_user_id} iniciando proceso de firma para documento {document_id}")

        result = await start_document_signing_process(document_id, request.state.tenant_user_id, schema_name=schema_name)
        
        logger.info(f"Proceso de firma iniciado exitosamente para documento {document_id}")
        return StartSigningProcessResponse(
            success=result["success"],
            message=result["message"]
        )
        
    except (ValidationError, ExternalServiceError) as e:
        logger.warning(f"Error conocido para documento {document_id}: {type(e).__name__} - {e}")
        raise exception_to_http_exception(e)
        
    except Exception as e:
        logger.error(f"Error inesperado al iniciar firma de documento {document_id}: {e}")
        raise exception_to_http_exception(e)