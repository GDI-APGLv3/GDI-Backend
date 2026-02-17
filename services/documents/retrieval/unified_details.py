"""
Servicio unificado para obtener detalles de documentos en cualquier estado.
Delega automaticamente al servicio apropiado segun el estado del documento.
"""
from typing import Dict, Any, Optional
from database import get_db_connection
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




def _get_document_status(document_id: str, *, schema_name: str) -> str:
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
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            cursor.execute(get_document_status_query(), (document_id,))
            result = cursor.fetchone()

            if not result:
                raise DocumentNotFoundError(UNIFIED_DETAILS_DOCUMENT_NOT_FOUND_MESSAGE)

            return result['status']


def _check_user_can_view_document(document_id: str, user_id: str, *, schema_name: str) -> None:
    """
    Verifica si el usuario tiene permiso para ver el documento.

    El usuario puede ver el documento SI:
    - Es el creador del documento (created_by), O
    - Es firmante del documento (existe en document_signers), O
    - Tiene permiso can_view en el sector del creador

    Args:
        document_id: UUID del documento
        user_id: UUID del usuario que intenta ver
        schema_name: Schema del tenant

    Raises:
        AuthorizationError: Si el usuario no tiene permiso para ver el documento
    """
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            # Query para verificar permisos
            check_query = """
                SELECT
                    d.created_by,
                    creator.sector_id as creator_sector_id,
                    EXISTS(
                        SELECT 1 FROM document_signers ds
                        WHERE ds.document_id = %s AND ds.user_id = %s
                    ) as is_signer,
                    EXISTS(
                        SELECT 1 FROM user_sector_permissions usp
                        WHERE usp.user_id = %s
                        AND usp.sector_id = creator.sector_id
                        AND usp.can_view = true
                    ) as has_sector_permission
                FROM document_draft d
                LEFT JOIN users creator ON d.created_by = creator.id
                WHERE d.id = %s
            """

            cursor.execute(check_query, (document_id, user_id, user_id, document_id))
            result = cursor.fetchone()

            if not result:
                raise DocumentNotFoundError(document_id)

            # Verificar si es creador
            if str(result['created_by']) == str(user_id):
                logger.debug(f"Usuario {user_id[:8]} es creador del documento {document_id[:8]}")
                return

            # Verificar si es firmante
            if result['is_signer']:
                logger.debug(f"Usuario {user_id[:8]} es firmante del documento {document_id[:8]}")
                return

            # Verificar si tiene permiso en el sector
            if result['has_sector_permission']:
                logger.debug(f"Usuario {user_id[:8]} tiene permiso can_view en sector del documento {document_id[:8]}")
                return

            # Si ninguna condición se cumple, denegar acceso
            logger.warning(f"Usuario {user_id[:8]} sin permiso para ver documento {document_id[:8]}")
            raise AuthorizationError(f"No tiene permisos para ver este documento")


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
        status = _get_document_status(document_id, schema_name=schema_name)
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
        if category == 'editing':
            logger.info("Delegando a servicio de edicion...")
            details = get_document_details_for_editing(document_id, schema_name=schema_name)
        else:
            if not user_id:
                raise ValidationError(UNIFIED_DETAILS_USER_ID_REQUIRED_MESSAGE)

            if not validate_uuid(user_id):
                raise ValidationError(f"UUID de usuario invalido: {user_id}")

            # Documentos firmados son publicos dentro del tenant
            if status == 'signed':
                logger.info(f"Documento {document_id[:8]} firmado - acceso publico para {user_id[:8]}")
            else:
                # Solo verificar permisos para documentos en proceso de firma (sent_to_sign)
                logger.info(f"Verificando permisos para usuario {user_id[:8]}...")
                _check_user_can_view_document(document_id, user_id, schema_name=schema_name)

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
