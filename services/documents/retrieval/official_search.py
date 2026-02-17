"""
Servicio para busqueda de documentos oficiales por numero exacto.
Busca unicamente en la tabla official_documents sin restricciones de usuario.
Optimizado siguiendo principios de Clean Code.
"""

from shared.logging import get_logger
from typing import Dict, Any, Optional
from database import get_db_connection
from shared.exceptions import ValidationError
from ..core.queries import search_official_document_by_number_query

# === CONFIGURACION ===
logger = get_logger(__name__)

def search_official_document_by_number(doc_number: str, *, user_id: str = None, schema_name: str) -> Dict[str, Any]:
    """
    Busca un documento oficial por su numero exacto.
    Si user_id es None: busqueda global sin restricciones.
    Si user_id se proporciona: filtra por sectores del usuario (signer_sector_ids).

    Args:
        doc_number: Numero oficial del documento (ej: "ANEXO-2025-00000002-SMG-ADGEN")
        user_id: ID del usuario para filtrar por sectores (opcional)
        schema_name: Nombre del esquema de base de datos (multi-tenant)

    Returns:
        Dict con informacion del documento o None si no se encuentra

    Raises:
        ValidationError: Si el numero de documento no es valido
    """
    logger.info(f"Buscando documento oficial por numero: {doc_number[:15]}...")

    # Validar formato basico del numero
    if not doc_number or len(doc_number.strip()) == 0:
        raise ValidationError("El numero de documento no puede estar vacio")

    doc_number = doc_number.strip()

    try:
        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                # Query centralizada que obtiene toda la informacion necesaria del documento oficial
                cursor.execute(search_official_document_by_number_query(), (doc_number,))
                result = cursor.fetchone()

                if not result:
                    logger.info(f"No se encontro documento con numero: {doc_number[:15]}...")
                    return {
                        "found": False,
                        "document": None,
                        "search_term": doc_number
                    }

                # Si user_id proporcionado, verificar que el documento tenga relacion con sectores del usuario
                if user_id:
                    from services.cases.permissions import get_user_viewable_sector_ids
                    user_sector_ids = get_user_viewable_sector_ids(user_id, schema_name=schema_name)

                    # Verificar si signer_sector_ids tiene interseccion con sectores del usuario
                    cursor.execute(
                        """
                        SELECT EXISTS(
                            SELECT 1 FROM official_documents
                            WHERE id = %s
                              AND signer_sector_ids && %s::uuid[]
                        ) as has_access
                        """,
                        (result['document_id'], user_sector_ids)
                    )
                    access_check = cursor.fetchone()
                    if not access_check or not access_check['has_access']:
                        logger.info(f"Usuario {user_id[:8]} sin acceso a documento {doc_number[:15]}")
                        return {
                            "found": False,
                            "document": None,
                            "search_term": doc_number
                        }

                logger.info(f"Documento oficial encontrado: {result['document_id'][:8]}...")

                # Construir respuesta con exactamente la misma estructura que UserDocumentInfo
                document_info = {
                    "id": result["document_id"],
                    "reference": result["official_number"],  # Para documentos oficiales, reference es el numero oficial
                    "display_status": "Firmado",  # Los documentos oficiales siempre estan firmados
                    "updated_at": result["updated_at"].isoformat() if result["updated_at"] else None,
                    "document_type": {
                        "name": result["document_type_name"] or "Documento",
                        "acronym": result["document_type_acronym"] or "DOC"
                    },
                    "user_role": "public",  # Para documentos oficiales, acceso publico
                    # Para documentos oficiales, el ultimo editor es el numerador (quien lo oficializo)
                    "last_editor_name": result["numerator_name"] or result["creator_name"],
                    "last_editor_profile_picture_id": None,  # Por simplicidad, no incluir foto en busqueda publica
                    "official_number": result["official_number"]  # Siempre presente en documentos oficiales
                }

                return {
                    "found": True,
                    "document": document_info,
                    "search_term": doc_number
                }

    except Exception as e:
        logger.error(f"Error en busqueda: {str(e)}")
        raise ValidationError(f"Error al buscar documento oficial: {str(e)}")
