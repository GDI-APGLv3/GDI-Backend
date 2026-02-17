"""
Endpoint unificado para firma de documentos.
Detecta automáticamente si el usuario es firmante común o numerador.
Optimizado siguiendo principios de Clean Code.
"""
from shared.logging import get_logger
from fastapi import APIRouter, Path, Depends, Request
from typing import Dict, Any
from models.documents.unified_signing import SuperSignResponse
from models.tags import Tags
from auth import get_current_user
from models.schemas import AuthenticatedUser
from services.documents.signing.unified_signing import super_sign_document
from services.documents.core.queries import get_user_info_for_signing_query
from database import execute_query
from shared.exceptions import (
    DocumentNotFoundError, DocumentStateError, ValidationError,
    AuthorizationError, ExternalServiceError, exception_to_http_exception
)
from shared.validation import validate_uuid
from shared.dependencies import get_tenant_schema

# === CONFIGURACIÓN ===
logger = get_logger("super_sign")

router = APIRouter(tags=[Tags.DOCUMENTOS])

@router.post(
    "/documents/{document_id}/super-sign",
    response_model=SuperSignResponse,
    summary="Firma unificada de documento",
    description="Endpoint unificado que detecta automáticamente si el usuario es firmante común o numerador y ejecuta la lógica correspondiente",
    dependencies=[Depends(get_current_user)]
)
async def super_sign(
    request: Request,
    document_id: str = Path(..., description="UUID del documento a firmar"),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
) -> Dict[str, Any]:
    """
    Firma unificada de documento con detección automática del rol.

    ## 🎯 Funcionalidad:

    Este endpoint **unifica** la firma de documentos para firmantes comunes y numeradores.
    Detecta automáticamente el rol del usuario consultando la base de datos y ejecuta
    la lógica correspondiente.

    ## 🔄 Proceso Automático:

    1. **Consulta inicial**: Obtiene el rol del usuario (`is_numerator`) desde `document_signers`
    2. **Validaciones comunes**:
       - Usuario está registrado como firmante del documento
       - Usuario aún no ha firmado (`status = 'pending'`)
       - Documento está en estado `sent_to_sign`

    3. **Bifurcación automática**:

       ### A. Si `is_numerator = false` (Firmante Común):
       - Estampa firma digital vía Legal Orchestrator
       - Actualiza estado del firmante a `signed`
       - Documento permanece en estado `sent_to_sign`
       - Devuelve respuesta con `official_number = null`

       ### B. Si `is_numerator = true` (Numerador):
       - **Validación adicional**: Todos los firmantes comunes deben haber firmado
       - Genera número oficial único (ej: `ANEXO-2025-00000002-SMG-ADGEN`)
       - Reserva número en `official_documents`
       - Estampa firma digital y numera vía Legal Orchestrator
       - Mueve PDF de bucket `tosign` a bucket `oficial`
       - Renombra PDF de UUID a número oficial
       - Actualiza documento a estado `signed`
       - Devuelve respuesta con `official_number` y `signed_pdf_url`

    ## ✅ Validaciones Automáticas:

    **Para todos:**
    - ✅ Usuario debe estar en `document_signers`
    - ✅ `status = 'pending'` (aún no firmó)
    - ✅ Documento en estado `sent_to_sign`

    **Solo para numerador:**
    - ✅ Todos los firmantes comunes deben haber firmado
    - ✅ Solo puede firmar al final del proceso

    ## 📊 Respuesta Unificada:

    ### Firmante Común:
    ```json
    {
        "success": true,
        "message": "Documento firmado exitosamente",
        "document_id": "uuid",
        "signature_id": "uuid",
        "document_status": "sent_to_sign",
        "signed_at": "2025-01-15T10:30:00",
        "is_numerator": false,
        "official_number": null,
        "signed_pdf_url": null
    }
    ```

    ### Numerador:
    ```json
    {
        "success": true,
        "message": "Documento firmado y numerado exitosamente por el numerador",
        "document_id": "uuid",
        "signature_id": "uuid",
        "document_status": "signed",
        "signed_at": "2025-01-15T10:35:00",
        "is_numerator": true,
        "official_number": "ANEXO-2025-00000002-SMG-ADGEN",
        "signed_pdf_url": "https://cloudflare-r2.../ANEXO-2025-00000002-SMG-ADGEN.pdf"
    }
    ```

    ## 🔐 Ventajas de la Unificación:

    - ✨ **Un solo endpoint** para el frontend
    - ✨ **Detección automática** del rol (sin lógica en frontend)
    - ✨ **Respuesta consistente** (misma estructura para ambos casos)
    - ✨ **Menos código** en el cliente
    - ✨ **Menor probabilidad de error** (backend decide la lógica)

    ## ⚠️ Errores Comunes:

    - **400**: UUIDs inválidos o validaciones fallidas
    - **403**: Usuario no es firmante del documento
    - **404**: Documento no encontrado
    - **409**: Documento no está en estado correcto
    - **422**: Numerador intenta firmar con firmantes pendientes
    - **500**: Error interno o falla en API de estampado

    ## Parámetros:
    - **document_id**: UUID del documento a firmar
    - **current_user**: Usuario autenticado (automático vía Auth0/Testing mode)

    ## Respuesta:
    Estructura `SuperSignResponse` con campos comunes y opcionales según el rol.
    """

    try:
        logger.info(f"Usuario {request.state.tenant_user_id[:8]}... iniciando super firma para documento {document_id[:8]}...")

        # Buscar información del usuario autenticado en BD
        user_result = execute_query(
            get_user_info_for_signing_query(),
            (request.state.tenant_user_id,),
            schema_name=schema_name
        )

        if not user_result:
            logger.warning(f"Usuario {request.state.tenant_user_id} no encontrado en BD")
            raise ValidationError("Usuario no encontrado en el sistema")

        user_data = user_result[0]
        db_user_id = str(user_data['user_id'])
        
        logger.debug(f"Usuario validado: {user_data['full_name']}")

        # Validar formato UUID usando utilidad compartida
        if not validate_uuid(document_id):
            raise ValidationError("document_id debe ser un UUID válido")
        if not validate_uuid(db_user_id):
            raise ValidationError("user_id debe ser un UUID válido")

        # Llamar al servicio unificado
        logger.info("Ejecutando lógica de firma unificada...")
        result = await super_sign_document(document_id, db_user_id, schema_name=schema_name)

        logger.info(f"Firma completada exitosamente. Tipo: {'numerador' if result.get('is_numerator') else 'común'}")
        
        # Formatear respuesta usando el modelo Pydantic
        return SuperSignResponse(**result)

    except (DocumentNotFoundError, DocumentStateError, ValidationError,
            AuthorizationError, ExternalServiceError) as e:
        logger.warning(f"Error conocido en super firma: {type(e).__name__} - {e}")
        raise exception_to_http_exception(e)
        
    except Exception as e:
        logger.error(f"Error inesperado en super firma: {e}")
        raise exception_to_http_exception(ExternalServiceError(f"Error interno del servidor: {str(e)}"))
