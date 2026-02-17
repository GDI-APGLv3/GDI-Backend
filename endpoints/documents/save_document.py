"""
Endpoint para guardar cambios en un documento editable.
Optimizado siguiendo principios de Clean Code.
"""
from typing import Optional
from shared.logging import get_logger
from fastapi import APIRouter, Path, Depends, Request
from models.documents.editing import SaveDocumentRequest, SaveDocumentResponse
from models.tags import Tags
from auth import get_current_user
from models.schemas import AuthenticatedUser
from services.documents.editing import save_document_changes
from shared.dependencies import get_tenant_schema
from shared.exceptions import (
    exception_to_http_exception, ValidationError, DocumentNotFoundError, DocumentStateError
)


def _get_primary_sector_id(user: AuthenticatedUser) -> Optional[str]:
    """Obtiene el sector primario del usuario para notas."""
    if user.permissions:
        primary = next((p for p in user.permissions if p.is_primary), None)
        if primary:
            return primary.sector_id
        return user.permissions[0].sector_id if user.permissions else None
    return None

# === CONFIGURACIÓN ===
logger = get_logger("save_document")

router = APIRouter(tags=[Tags.DOCUMENTOS])


@router.patch(
    "/documents/{document_id}/save",
    response_model=SaveDocumentResponse,
    summary="Guardar cambios en documento",
    description="""Guarda cambios parciales o completos en un documento editable.

## Campos Actualizables

- **reference**: Asunto o referencia del documento (max 250 caracteres)
- **content**: Contenido HTML del documento
- **signers**: Lista de firmantes con sus roles
- **recipients**: Destinatarios para NOTA (TO, CC, BCC) - solo aplica a documentos tipo NOTA
- **proposed_case_ids**: Lista de UUIDs de expedientes a los que se propone vincular este documento

## Estados Permitidos

Solo funciona con documentos en estado:
- **draft**: Borrador inicial
- **rejected**: Rechazado (puede editarse y reenviarse)

## Recipients para NOTAS

Los **recipients** (destinatarios TO, CC, BCC) de una NOTA ahora pueden editarse en este endpoint.
- Si se envía el campo `recipients`, reemplaza los destinatarios existentes
- Si no se envía, los destinatarios actuales se mantienen sin cambios
- Para otros tipos de documento, el campo `recipients` se ignora

## Propuesta de Vinculación a Expedientes

Los **proposed_case_ids** permiten vincular el documento a uno o más expedientes:
- Un documento puede estar propuesto a **múltiples expedientes diferentes** ✅
- Un documento NO puede estar propuesto múltiples veces al **mismo expediente** ❌ (se deduplicarán automáticamente)
- Si se envía el campo `proposed_case_ids`, reemplaza las propuestas existentes
- Si no se envía, las propuestas actuales se mantienen sin cambios
- Los expedientes se validan (deben existir y no estar archivados)

## Ejemplo de Request

```json
{
    "reference": "Nuevo asunto actualizado",
    "content": "<p>Contenido HTML del documento</p>",
    "signers": [
        {
            "user_id": "uuid-firmante-1",
            "is_numerator": true
        }
    ],
    "recipients": {
        "to": ["uuid-sector-1", "uuid-sector-2"],
        "cc": ["uuid-sector-3"],
        "bcc": []
    },
    "proposed_case_ids": [
        "550e8400-e29b-41d4-a716-446655440010",
        "550e8400-e29b-41d4-a716-446655440011"
    ]
}
```

Todos los campos son opcionales. Solo se actualizan los campos enviados.
""",
    responses={
        200: {"description": "Cambios guardados exitosamente"},
        400: {"description": "Datos inválidos, sin cambios proporcionados, o validación fallida"},
        404: {"description": "Documento no encontrado"},
        409: {"description": "Documento no puede editarse en su estado actual (debe estar en 'draft' o 'rejected')"}
    }
)
async def save_document_changes_endpoint(
    request: Request,
    document_id: str = Path(..., description="UUID del documento"),
    body: SaveDocumentRequest = None,
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
) -> SaveDocumentResponse:
    """
    Guarda cambios en un documento editable.
    
    Args:
        document_id: UUID del documento a modificar
        request: Datos a actualizar (referencia, contenido, firmantes)
        current_user: Usuario autenticado que realiza los cambios
        
    Returns:
        SaveDocumentResponse: Resultado de la operación de guardado
        
    Raises:
        HTTPException: Para errores de validación, documento no encontrado o estado inválido
    """
    try:
        logger.info(f"Usuario {request.state.tenant_user_id} guardando cambios en documento {document_id}")

        # Transformar signers a formato dict si existen
        signers_list = None
        if body.signers:
            signers_list = [
                {
                    "user_id": signer.user_id,
                    "email": signer.email,
                    "is_numerator": signer.is_numerator
                }
                for signer in body.signers
            ]
            logger.debug(f"Actualizando {len(signers_list)} firmantes")

        # Transformar recipients a formato dict si existen (solo para NOTA)
        recipients_dict = None
        sender_sector_id = None
        if body.recipients is not None:
            recipients_dict = body.recipients.model_dump()
            sender_sector_id = _get_primary_sector_id(current_user)
            logger.debug(f"Actualizando recipients: TO={len(recipients_dict.get('to', []))}, CC={len(recipients_dict.get('cc', []))}")

        # Obtener user_id del request state
        user_id = str(request.state.tenant_user_id)

        # Log de proposed_case_ids si existen
        if body.proposed_case_ids is not None:
            logger.debug(f"Proponiendo vinculación a {len(body.proposed_case_ids)} expedientes")

        result = save_document_changes(
            document_id=document_id,
            reference=body.reference,
            content=body.content,
            signers=signers_list,
            recipients=recipients_dict,
            sender_sector_id=sender_sector_id,
            proposed_case_ids=body.proposed_case_ids,
            user_id=user_id,
            schema_name=schema_name
        )
        
        logger.info(f"Cambios guardados exitosamente en documento {document_id}")
        return SaveDocumentResponse(
            success=result["success"],
            message=result["message"],
            document_id=result["document_id"],
            last_modified_at=result.get("last_modified_at")
        )
        
    except (ValidationError, DocumentNotFoundError, DocumentStateError) as e:
        logger.warning(f"Error conocido al guardar documento {document_id}: {type(e).__name__} - {e}")
        raise exception_to_http_exception(e)
        
    except Exception as e:
        logger.error(f"Error inesperado al guardar documento {document_id}: {e}")
        raise exception_to_http_exception(e)