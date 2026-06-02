"""
Servicio unificado para obtener detalles de documentos en cualquier estado.
Delega automaticamente al servicio apropiado segun el estado del documento.
"""
from typing import Dict, Any, Optional
from database import fetch_one
from shared.exceptions import DocumentNotFoundError, DocumentStateError, ValidationError, AuthorizationError
from shared.validation import validate_uuid
from ..editing import get_document_details_for_editing
from ..signing.details_builder import build_signature_details_response
from ..core.queries import get_document_status_query
from config.constants import (
    UNIFIED_DETAILS_DOCUMENT_NOT_FOUND_MESSAGE,
    UNIFIED_DETAILS_STATE_NOT_SUPPORTED_MESSAGE,
    UNIFIED_DETAILS_USER_ID_REQUIRED_MESSAGE,
    UNIFIED_DETAILS_ERROR_MESSAGE,
    STATE_CATEGORY_MAP
)
from datetime import datetime
from shared.logging import get_logger
logger = get_logger(__name__)


async def _get_document_status(document_id: str, *, schema_name: str) -> str:
    """
    Obtiene el estado actual de un documento.

    Args:
        document_id: UUID del documento
        schema_name: Nombre del esquema de base de datos (multi-tenant)

    Returns:
        Estado del documento (draft, rejected, sent_to_sign, signed, cancelled)

    Raises:
        DocumentNotFoundError: Si el documento no existe
    """
    result = await fetch_one(
        get_document_status_query(),
        document_id,
        schema_name=schema_name,
    )

    if not result:
        raise DocumentNotFoundError(UNIFIED_DETAILS_DOCUMENT_NOT_FOUND_MESSAGE)

    return result['status']


async def get_unified_document_details(
    document_id: str,
    user_id: Optional[str] = None,
    *,
    schema_name: str
) -> Dict[str, Any]:
    """
    Obtiene detalles de un documento delegando al servicio apropiado segun su estado.
    """
    if not validate_uuid(document_id):
        raise ValidationError(f"UUID de documento invalido: {document_id}")

    logger.info(f"Obteniendo detalles para documento {document_id[:8]}...")

    try:
        status = await _get_document_status(document_id, schema_name=schema_name)
        logger.info(f"Estado detectado: {status}")
    except DocumentNotFoundError:
        raise
    except Exception as e:
        logger.error(f"Error al obtener estado del documento {document_id}: {str(e)}")
        raise DocumentNotFoundError(document_id)

    category = STATE_CATEGORY_MAP.get(status)

    if not category:
        logger.warning(f"Estado '{status}' no soportado")
        raise DocumentStateError(
            UNIFIED_DETAILS_STATE_NOT_SUPPORTED_MESSAGE.format(status=status),
            current_state=status,
            required_state="draft, rejected, sent_to_sign o signed"
        )

    logger.info(f"Categoria: {category}")

    try:
        # SEC (IDOR SU-003/004/005 + cierre de borradores): verificar permisos
        # para TODOS los estados, incluidos los editables (draft/rejected) y
        # 'signed'. Antes la rama 'editing' saltaba el chequeo, permitiendo a
        # cualquier usuario del tenant leer un borrador ajeno por document_id;
        # y 'signed' se trataba como publico intra-tenant. Ahora usamos la
        # funcion canonica can_user_view_document (consistente con /content y
        # /geturl_officialdoc), que reconoce creador, mismo sector del creador
        # (preserva edicion colaborativa), firmante, flag global y acceso via
        # expediente.
        if not user_id:
            raise ValidationError(UNIFIED_DETAILS_USER_ID_REQUIRED_MESSAGE)

        if not validate_uuid(user_id):
            raise ValidationError(f"UUID de usuario invalido: {user_id}")

        logger.info(f"Verificando permisos para usuario {user_id[:8]} (estado={status})...")
        from services.documents.permissions import can_user_view_document
        if not await can_user_view_document(document_id, user_id, schema_name=schema_name):
            logger.warning(
                f"Usuario {user_id[:8]} sin permiso para ver documento {document_id[:8]} (estado={status})"
            )
            raise AuthorizationError("No tiene permisos para ver este documento")

        if category == 'editing':
            logger.info("Delegando a servicio de edicion...")
            details = await get_document_details_for_editing(document_id, schema_name=schema_name)
        else:
            logger.info(f"Delegando a servicio de firma para usuario {user_id[:8]}...")
            details = await build_signature_details_response(document_id, user_id, schema_name=schema_name)

        logger.info("Detalles obtenidos exitosamente")

        response = {
            "state_category": category,
            "document_id": document_id,
            "status": status,
            "details": details,
            "timestamp": datetime.now()
        }

        return response

    except (DocumentNotFoundError, DocumentStateError, ValidationError, AuthorizationError):
        raise
    except Exception as e:
        logger.error(f"Error inesperado al obtener detalles: {str(e)}")
        from shared.exceptions import ExternalServiceError
        raise ExternalServiceError(f"{UNIFIED_DETAILS_ERROR_MESSAGE}: {str(e)}")
