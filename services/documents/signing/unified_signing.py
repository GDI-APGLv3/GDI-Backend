"""
Servicio unificado de firma de documentos.
Detecta automáticamente si el usuario es firmante común o numerador
y ejecuta la lógica correspondiente.
Optimizado siguiendo principios de Clean Code.
"""

from shared.logging import get_logger
from typing import Dict, Any
from database import get_db_connection
from shared.exceptions import (
    DocumentNotFoundError, ValidationError, DocumentStateError,
    AuthorizationError
)
from datetime import datetime
from ..core.queries import get_signer_role_and_document_status_query

# === CONFIGURACIÓN ===
logger = get_logger("unified_signing")

# Importar servicios existentes que vamos a reutilizar
from .signing import sign_document
from .numerator import sign_document_as_numerator


async def super_sign_document(document_id: str, user_id: str, *, schema_name: str) -> Dict[str, Any]:
    """
    Servicio unificado de firma que detecta automáticamente
    si el usuario es firmante común o numerador.

    ## Flujo:
    1. Query inicial: Obtiene is_numerator y validaciones
    2. Validaciones comunes (usuario existe, está pending, etc.)
    3. Bifurcación de lógica:
       - Si is_numerator = false → Llama a sign_document()
       - Si is_numerator = true → Valida pendientes y llama a sign_document_as_numerator()
    4. Adapta la respuesta al formato SuperSignResponse

    Args:
        document_id: UUID del documento a firmar
        user_id: UUID del usuario que va a firmar
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Dict con estructura SuperSignResponse

    Raises:
        DocumentNotFoundError: Si el documento no existe
        ValidationError: Si las validaciones fallan
        DocumentStateError: Si el documento no está en estado correcto
        AuthorizationError: Si el usuario no tiene permisos
    """

    logger.info(f"Iniciando proceso de firma unificada para documento {document_id[:8]}... por usuario {user_id[:8]}...")

    # ========================================================================
    # PASO 1: QUERY INICIAL - Obtener toda la información necesaria
    # ========================================================================

    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                get_signer_role_and_document_status_query(),
                (document_id, document_id, user_id)
            )
            result = cursor.fetchone()

            if not result:
                logger.warning(f"Usuario {user_id[:8]}... no es firmante del documento {document_id[:8]}...")
                raise AuthorizationError(
                    f"Usuario '{user_id}' no está registrado como firmante del documento '{document_id}'"
                )

    # ========================================================================
    # PASO 2: VALIDACIONES COMUNES
    # ========================================================================

    is_numerator = result['is_numerator']
    signer_status = result['signer_status']
    doc_status = result['doc_status']
    pending_common_signers = result['pending_common_signers']

    logger.info(f"is_numerator: {is_numerator}")
    logger.info(f"signer_status: {signer_status}")
    logger.info(f"doc_status: {doc_status}")
    logger.info(f"pending_common_signers: {pending_common_signers}")

    # VALIDACIÓN 1: Primero validar que el documento está en estado correcto
    if doc_status != 'sent_to_sign':
        logger.error("Documento no está en estado sent_to_sign")
        raise DocumentStateError(
            f"Documento en estado '{doc_status}' no puede firmarse. "
            f"El documento debe estar enviado a firma primero. Use /start-signing-process.",
            current_state=doc_status,
            required_state="sent_to_sign"
        )

    # VALIDACIÓN 2: Validar que el firmante aún no ha firmado
    # Aceptar None como equivalente a 'pending' (status inicial)
    if signer_status not in ['pending', None]:
        logger.error("Usuario ya firmó este documento")
        raise ValidationError(
            f"El usuario ya firmó este documento (status: {signer_status})"
        )

    # ========================================================================
    # PASO 3: BIFURCACIÓN DE LÓGICA
    # ========================================================================

    if not is_numerator:
        # ====================================================================
        # RAMA A: FIRMANTE COMÚN
        # ====================================================================
        logger.info("Ejecutando lógica de firmante común")

        # Llamar al servicio existente de firmante común
        result = await sign_document(document_id, user_id, schema_name=schema_name)

        logger.info(f"Firmante común - Resultado: {result.get('success')}")

        # Adaptar respuesta al formato SuperSignResponse
        return {
            "success": result["success"],
            "message": result["message"],
            "document_id": document_id,
            "signature_id": result["signature_id"],
            "document_status": result["document_status"],
            "signed_at": datetime.now().isoformat(),
            "is_numerator": False,
            "official_number": None,
            "signed_pdf_url": None
        }

    else:
        # ====================================================================
        # RAMA B: NUMERADOR
        # ====================================================================
        logger.info("Ejecutando lógica de numerador")

        # Validación específica de numerador: Todos los comunes deben haber firmado
        if pending_common_signers > 0:
            logger.error(f"Aún hay {pending_common_signers} firmantes comunes pendientes")
            raise ValidationError(
                f"Aún hay {pending_common_signers} firmante(s) pendiente(s). "
                "El numerador debe firmar al final."
            )

        logger.info("Todos los firmantes comunes han firmado, procediendo con numerador")

        # Llamar al servicio existente de numerador
        result = await sign_document_as_numerator(document_id, user_id, schema_name=schema_name)

        logger.info(f"Numerador - Resultado: {result.get('success')}")
        logger.info(f"Numerador - Official number: {result.get('official_number')}")

        # Adaptar respuesta al formato SuperSignResponse
        # Extraer signed_pdf_url de la estructura anidada de api_result
        signed_pdf_url = None
        if result.get("api_result"):
            signed_pdf_url = (
                result["api_result"].get("signed_pdf_url") or
                result["api_result"].get("url_pdf_firmado_1")
            )

        return {
            "success": result["success"],
            "message": result["message"],
            "document_id": result["document_id"],
            "signature_id": result["numerator_id"],
            "document_status": result.get("document_status", "signed"),
            "signed_at": datetime.now().isoformat(),
            "is_numerator": True,
            "official_number": result.get("official_number"),
            "signed_pdf_url": signed_pdf_url
        }
