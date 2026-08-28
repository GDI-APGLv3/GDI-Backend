
from shared.logging import get_logger
from typing import Dict, Any
from database import fetch_one
from shared.exceptions import ValidationError
from ..core.queries import search_official_document_by_number_query

logger = get_logger(__name__)

async def search_official_document_by_number(
    doc_number: str, *, user_id: str = None, exclude_reserved: bool = False, schema_name: str
) -> Dict[str, Any]:
    logger.info(f"Buscando documento oficial por numero: {doc_number[:15]}...")

    if not doc_number or len(doc_number.strip()) == 0:
        raise ValidationError("El numero de documento no puede estar vacio")

    doc_number = doc_number.strip()

    try:
        result = await fetch_one(
            search_official_document_by_number_query(),
            doc_number,
            schema_name=schema_name,
        )

        if not result:
            logger.info(f"No se encontro documento con numero: {doc_number[:15]}...")
            return {
                "found": False,
                "document": None,
                "search_term": doc_number
            }

        if exclude_reserved:
            _is_reserved = result.get("document_type_is_reserved")
            if _is_reserved is None or _is_reserved:
                logger.info(
                    "Documento %s... excluido por exclude_reserved (reservado=%s)",
                    doc_number[:15],
                    "sin determinar" if _is_reserved is None else "sí",
                )
                return {
                    "found": False,
                    "document": None,
                    "search_term": doc_number
                }

        if user_id:
            doc_base_type = (result['document_base_type'] or '').upper()
            if doc_base_type == 'MEMO':
                access_check = await fetch_one(
                    """
                    SELECT EXISTS(
                        SELECT 1 FROM memo_recipients mr
                        WHERE mr.document_id = $1
                          AND (mr.sender_user_id = $2 OR mr.recipient_user_id = $2)
                    ) as has_access
                    """,
                    result['document_id'],
                    user_id,
                    schema_name=schema_name,
                )
                if not access_check or not access_check['has_access']:
                    logger.info(f"Usuario {user_id[:8]} sin acceso a MEMO {doc_number[:15]}")
                    return {
                        "found": False,
                        "document": None,
                        "search_term": doc_number
                    }
            else:
                from services.cases.permissions import get_user_viewable_sector_ids
                user_sector_ids = await get_user_viewable_sector_ids(user_id, schema_name=schema_name)

                access_check = await fetch_one(
                    """
                    SELECT EXISTS(
                        SELECT 1 FROM official_documents
                        WHERE id = $1
                          AND signer_sector_ids && $2::uuid[]
                    ) as has_access
                    """,
                    result['document_id'],
                    user_sector_ids,
                    schema_name=schema_name,
                )
                if not access_check or not access_check['has_access']:
                    logger.info(f"Usuario {user_id[:8]} sin acceso a documento {doc_number[:15]}")
                    return {
                        "found": False,
                        "document": None,
                        "search_term": doc_number
                    }

        logger.info(f"Documento oficial encontrado: {result['document_id'][:8]}...")

        document_info = {
            "id": result["document_id"],
            "reference": result["official_number"],
            "display_status": "Firmado",
            "updated_at": result["updated_at"].isoformat() if result["updated_at"] else None,
            "document_type": {
                "name": result["document_type_name"] or "Documento",
                "acronym": result["document_type_acronym"] or "DOC"
            },
            "user_role": "public",
            "last_editor_name": result["numerator_name"] or result["creator_name"],
            "last_editor_profile_picture_id": None,
            "official_number": result["official_number"]
        }

        return {
            "found": True,
            "document": document_info,
            "search_term": doc_number
        }

    except ValidationError:
        raise
    except Exception as e:
        logger.error(f"Error en busqueda: {str(e)}", exc_info=True)
        raise ValidationError("Error al buscar documento oficial")
