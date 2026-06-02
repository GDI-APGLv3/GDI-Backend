"""
Endpoint para crear un nuevo documento en estado borrador (draft).
Optimizado siguiendo principios de Clean Code.

Para tipo NOTA:
- Requiere campo 'recipients' con al menos un destinatario TO
- El sender_sector_id se obtiene del sector primario del usuario
"""
from shared.logging import get_logger
from fastapi import APIRouter, Depends
from models.documents.creation import CreateDocumentRequest, CreateDocumentResponse
from services.documents.lifecycle.creation import create_document as create_doc_service
from shared.exceptions import exception_to_http_exception, ValidationError, DatabaseError
from shared.dependencies import get_tenant_schema, get_auth_source
from models.tags import Tags
from auth import get_current_user
from models.schemas import AuthenticatedUser

# === CONFIGURACIÓN ===
logger = get_logger("create_document")

router = APIRouter(tags=[Tags.DOCUMENTOS])


def _get_primary_sector_id(current_user: AuthenticatedUser) -> str | None:
    """Obtiene el sector primario del usuario."""
    for perm in current_user.permissions:
        if perm.is_primary:
            return perm.sector_id
    # Fallback: primer sector con can_edit
    for perm in current_user.permissions:
        if perm.can_edit:
            return perm.sector_id
    return None


@router.post(
    "/documents",
    response_model=CreateDocumentResponse,
    summary="Crear nuevo documento",
    description="""Crea un nuevo documento en estado borrador (draft) y asigna automaticamente al creador como firmante numerador.

## Flujo General de Documentos
1. **POST /documents** - Crear documento (este endpoint)
2. **PATCH /documents/{id}/save** - Guardar contenido y firmantes
3. **POST /documents/{id}/start-signing-process** - Enviar a firma
4. **POST /documents/{id}/super-sign** - Firmar (cada firmante)

## Flujo Especifico para NOTAS

Las NOTAS son documentos con destinatarios (TO, CC, BCC). Los recipients se definen **al momento de crear** el documento, no al guardar.

### Paso 1: Crear NOTA con recipients (este endpoint)
```json
{
    "document_type_acronym": "NOTA",
    "reference": "Convocatoria a reunion",
    "recipients": {
        "to": ["uuid-sector-destino-1", "uuid-sector-destino-2"],
        "cc": ["uuid-sector-en-copia"],
        "bcc": ["uuid-sector-copia-oculta"]
    }
}
```

### Paso 2: Guardar contenido (PATCH /documents/{id}/save)
Solo se guarda el contenido HTML y firmantes. Los recipients ya estan definidos.

### Paso 3: Enviar a firma (POST /documents/{id}/start-signing-process)
Cambia estado a 'sent_to_sign' y genera PDF.

### Paso 4: Firmar (POST /documents/{id}/super-sign)
Al firmar el numerador, la NOTA se oficializa y se notifica a los recipients.

## Requisitos para NOTAS

- El campo **recipients** es OBLIGATORIO para tipo NOTA
- Debe incluir al menos un destinatario en **to** (destinatarios principales)
- **cc** (copia) y **bcc** (copia oculta) son opcionales
- Los valores son UUIDs de sectores (departamentos)
- El sender_sector_id se obtiene automaticamente del sector primario del usuario
"""
)
async def create_document(
    request: CreateDocumentRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema),
    auth_source: str = Depends(get_auth_source)
):
    """
    Crea un nuevo documento en estado draft.

    Args:
        request: Datos del documento (tipo, referencia, recipients para NOTA)
        current_user: Usuario autenticado que crea el documento
        schema_name: Schema del tenant (inyectado por dependency)

    Returns:
        CreateDocumentResponse: Datos del documento creado

    Raises:
        HTTPException: Para errores de validación o base de datos
    """
    try:
        logger.info(f"Usuario {current_user.user_id} creando documento tipo '{request.document_type_acronym}' en schema '{schema_name}'")

        # Preparar parámetros para NOTA
        recipients = None
        sender_sector_id = None

        if request.recipients is not None:
            # Es una NOTA con recipients
            recipients = request.recipients.model_dump()
            sender_sector_id = _get_primary_sector_id(current_user)
            if not sender_sector_id:
                raise ValidationError("No se pudo determinar el sector del usuario para enviar la nota")

        result = await create_doc_service(
            request.document_type_acronym,
            request.reference,
            current_user.user_id,
            schema_name=schema_name,
            recipients=recipients,
            sender_sector_id=sender_sector_id,
            auth_source=auth_source
        )

        logger.info(f"Documento {result['document_id']} creado exitosamente")
        return CreateDocumentResponse(
            document_id=result["document_id"],
            status=result["status"],
            message=result["message"]
        )

    except (ValidationError, DatabaseError) as e:
        logger.warning(f"Error conocido al crear documento: {type(e).__name__} - {e}")
        raise exception_to_http_exception(e)

    except Exception as e:
        logger.error(f"Error inesperado al crear documento: {e}")
        raise exception_to_http_exception(e)