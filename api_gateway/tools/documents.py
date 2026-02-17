"""
Tools MCP para documentos (lectura y operaciones).
Reutiliza servicios existentes para búsqueda, detalle y operaciones de documentos.
"""
import logging
from typing import Dict, Any, Optional, List
from api_gateway.context import MCPContext
from services.users.user_documents import get_user_documents
from services.documents.unified_details import get_unified_document_details
from services.documents.lifecycle.creation import create_document as create_doc_service
from services.documents.editing import save_document_changes
from services.documents.signing import start_document_signing_process
from services.documents.signing.unified_signing import super_sign_document
from services.documents.lifecycle.rejection import reject_document as reject_doc_service
from services.documents.lifecycle.deletion import delete_document as delete_doc_service
from shared.exceptions import ValidationError, NotFoundError, DocumentNotFoundError, DocumentStateError, AuthorizationError
from database import execute_query

logger = logging.getLogger(__name__)


def _get_linked_case(document_id: str, schema_name: str) -> Optional[Dict[str, Any]]:
    """
    Busca el expediente vinculado a un documento (oficial o borrador).

    Args:
        document_id: UUID del documento
        schema_name: Schema de la municipalidad

    Returns:
        Dict con case_id, case_number, reference si está vinculado, None si no
    """
    # Primero buscar en documentos oficiales (por ID del documento oficial)
    query_official = """
        SELECT
            c.id as case_id,
            c.case_number,
            c.reference as case_reference,
            cod.linking_date,
            'official' as link_type
        FROM case_official_documents cod
        JOIN cases c ON c.id = cod.case_id
        WHERE cod.official_document_id = %s AND cod.is_active = true
        LIMIT 1
    """

    result = execute_query(query_official, (document_id,), fetch_one=True, schema_name=schema_name)

    if result:
        return {
            "case_id": str(result["case_id"]),
            "case_number": result["case_number"],
            "case_reference": result["case_reference"],
            "linked_date": str(result["linking_date"]) if result["linking_date"] else None,
            "link_type": "official"
        }

    # Si no está como oficial, buscar como propuesto (borrador)
    query_proposed = """
        SELECT
            c.id as case_id,
            c.case_number,
            c.reference as case_reference,
            cpd.proposing_date,
            'proposed' as link_type
        FROM case_proposed_documents cpd
        JOIN cases c ON c.id = cpd.case_id
        WHERE cpd.document_draft_id = %s AND cpd.is_active = true
        LIMIT 1
    """

    result = execute_query(query_proposed, (document_id,), fetch_one=True, schema_name=schema_name)

    if result:
        return {
            "case_id": str(result["case_id"]),
            "case_number": result["case_number"],
            "case_reference": result["case_reference"],
            "linked_date": str(result["proposing_date"]) if result["proposing_date"] else None,
            "link_type": "proposed"
        }

    return None


def search_documents(
    ctx: MCPContext,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None,
    status: Optional[str] = None,
    document_type: Optional[str] = None,
    case_id: Optional[str] = None,
    min_signers: Optional[int] = None
) -> Dict[str, Any]:
    """
    Buscar documentos con filtros.

    Args:
        ctx: Contexto MCP con schema_name
        user_id: ID de usuario (REQUERIDO para permisos)
        page: Número de página (default 1)
        page_size: Tamaño de página (default 20, max 100)
        search: Texto de búsqueda (número de documento)
        status: Filtro de estado visual (pending, sent_to_sign, signed, rejected)
        document_type: Filtro por tipo (acronym del tipo)
        case_id: UUID del expediente para filtrar documentos vinculados (opcional)
        min_signers: Cantidad mínima de firmantes (opcional, ej: 2 para docs con 2+ firmas)

    Returns:
        Dict con documents (lista), pagination (total, page, page_size, total_pages)

    Raises:
        ValueError: Si page_size > 100 o user_id no proporcionado
    """
    logger.info(f"[MCP] search_documents - schema={ctx.schema_name}, page={page}, user_id={user_id}, case_id={case_id}")

    # Validaciones
    if page_size > 100:
        raise ValueError("page_size máximo es 100")

    if not user_id:
        raise ValueError("user_id es requerido para search_documents (necesario para permisos)")

    # Reutilizar get_user_documents (servicio refactorizado)
    try:
        result = get_user_documents(
            user_id=user_id,
            status_filter=status,
            doc_number=search,  # doc_number hace búsqueda exacta
            document_type=document_type,
            case_id=case_id,
            min_signers=min_signers,
            page=page,
            page_size=page_size,
            schema_name=ctx.schema_name
        )

        total = result["pagination"]["total"]
        logger.info(f"[MCP] search_documents - encontrados {total} documentos")

        return result

    except Exception as e:
        logger.error(f"[MCP] search_documents - error: {e}")
        raise RuntimeError(f"Error buscando documentos: {str(e)}")


async def get_document(
    ctx: MCPContext,
    document_id: str,
    user_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Obtener detalle completo de un documento en cualquier estado.
    Incluye el expediente vinculado si existe.

    Args:
        ctx: Contexto MCP con schema_name
        document_id: UUID del documento
        user_id: UUID del usuario (opcional, requerido para docs en proceso de firma)

    Returns:
        Dict con detalles del documento según su estado:
        - state_category: 'editing' o 'signing'
        - document_id, status, details, timestamp
        - linked_case: expediente vinculado (si existe)

    Raises:
        DocumentNotFoundError: Si el documento no existe
        ValidationError: Si el documento no puede ser accedido
        RuntimeError: Si hay error de BD
    """
    logger.info(f"[MCP] get_document - document_id={document_id}, schema={ctx.schema_name}, user_id={user_id}")

    try:
        # Usar servicio unificado que funciona con cualquier estado
        result = await get_unified_document_details(
            document_id=document_id,
            user_id=user_id,
            schema_name=ctx.schema_name
        )

        # Buscar expediente vinculado
        linked_case = _get_linked_case(document_id, ctx.schema_name)
        if linked_case:
            result["linked_case"] = linked_case
            logger.info(f"[MCP] get_document - expediente vinculado: {linked_case['case_number']}")
        else:
            result["linked_case"] = None
            logger.info(f"[MCP] get_document - sin expediente vinculado")

        logger.info(f"[MCP] get_document - documento encontrado, estado: {result.get('status')}")
        return result

    except DocumentNotFoundError as e:
        logger.error(f"[MCP] get_document - documento no encontrado: {e}")
        raise
    except DocumentStateError as e:
        logger.error(f"[MCP] get_document - estado no soportado: {e}")
        raise
    except ValidationError as e:
        logger.error(f"[MCP] get_document - error de validación: {e}")
        raise
    except Exception as e:
        logger.error(f"[MCP] get_document - error: {e}")
        raise RuntimeError(f"Error obteniendo documento: {str(e)}")


def get_pending_signatures(
    ctx: MCPContext,
    user_id: str
) -> Dict[str, Any]:
    """
    Obtener documentos pendientes de firma donde es el turno del usuario.
    Solo muestra documentos donde el usuario es el PRÓXIMO firmante.

    Args:
        ctx: Contexto MCP con schema_name
        user_id: UUID del usuario

    Returns:
        Dict con:
        - pending_signatures: lista de documentos pendientes
        - total: cantidad de documentos pendientes

    Raises:
        ValueError: Si user_id no proporcionado
        RuntimeError: Si hay error de BD
    """
    logger.info(f"[MCP] get_pending_signatures - user_id={user_id}, schema={ctx.schema_name}")

    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        # Query para obtener documentos donde el usuario es el próximo firmante
        # Un usuario es "próximo" si:
        # 1. Su status es 'pending'
        # 2. Todos los firmantes con signing_order menor ya firmaron
        query = """
            WITH pending_docs AS (
                SELECT
                    ds.id as signer_id,
                    ds.document_id,
                    ds.is_numerator,
                    ds.signing_order,
                    ds.status as signer_status,
                    d.id as draft_id,
                    d.reference,
                    d.document_number,
                    d.sent_to_sign_at,
                    d.status as doc_status,
                    dt.acronym as document_type_acronym,
                    dt.name as document_type_name,
                    u_creator.full_name as creator_name,
                    u_creator.profile_picture_url as creator_photo
                FROM document_signers ds
                JOIN document_draft d ON ds.document_id = d.id
                JOIN document_types dt ON d.document_type_id = dt.id
                JOIN users u_creator ON d.created_by = u_creator.id
                WHERE ds.user_id = %s
                  AND ds.status = 'pending'
                  AND d.status = 'sent_to_sign'
            )
            SELECT
                pd.*,
                CASE
                    WHEN pd.is_numerator = true THEN 'numerator'
                    ELSE 'signer'
                END as signer_role,
                -- Verificar si es el turno del usuario
                NOT EXISTS (
                    SELECT 1 FROM document_signers ds2
                    WHERE ds2.document_id = pd.document_id
                      AND ds2.signing_order < pd.signing_order
                      AND ds2.status = 'pending'
                      AND ds2.is_numerator = pd.is_numerator
                ) as is_my_turn
            FROM pending_docs pd
            WHERE NOT EXISTS (
                SELECT 1 FROM document_signers ds2
                WHERE ds2.document_id = pd.document_id
                  AND ds2.signing_order < pd.signing_order
                  AND ds2.status = 'pending'
                  AND ds2.is_numerator = pd.is_numerator
            )
            ORDER BY pd.sent_to_sign_at DESC
        """

        results = execute_query(query, (user_id,), schema_name=ctx.schema_name)

        pending_signatures = []
        for row in results:
            pending_signatures.append({
                "document_id": str(row["document_id"]),
                "reference": row["reference"],
                "document_number": row["document_number"],
                "document_type": {
                    "acronym": row["document_type_acronym"],
                    "name": row["document_type_name"]
                },
                "signer_role": row["signer_role"],
                "signing_order": row["signing_order"],
                "sent_to_sign_at": str(row["sent_to_sign_at"]) if row["sent_to_sign_at"] else None,
                "creator": {
                    "name": row["creator_name"],
                    "photo_url": row["creator_photo"]
                }
            })

        logger.info(f"[MCP] get_pending_signatures - {len(pending_signatures)} documentos pendientes")

        return {
            "pending_signatures": pending_signatures,
            "total": len(pending_signatures)
        }

    except Exception as e:
        logger.error(f"[MCP] get_pending_signatures - error: {e}")
        raise RuntimeError(f"Error obteniendo firmas pendientes: {str(e)}")


def get_document_content(
    ctx: MCPContext,
    document_id: str
) -> Dict[str, Any]:
    """
    Obtener contenido HTML de un documento oficial.

    Args:
        ctx: Contexto MCP con schema_name
        document_id: UUID del documento oficial

    Returns:
        Dict con document_id, official_number, reference, content (html),
        document_type, signed_at

    Raises:
        DocumentNotFoundError: Si el documento no existe
        RuntimeError: Si hay error de BD
    """
    logger.info(f"[MCP] get_document_content - document_id={document_id}, schema={ctx.schema_name}")

    try:
        from services.documents.content import get_official_document_content

        result = get_official_document_content(document_id, ctx.schema_name)

        logger.info(f"[MCP] get_document_content - contenido obtenido ({len(result['content']['html'])} chars)")
        return result

    except DocumentNotFoundError as e:
        logger.error(f"[MCP] get_document_content - documento no encontrado: {e}")
        raise

    except Exception as e:
        logger.error(f"[MCP] get_document_content - error: {e}")
        raise RuntimeError(f"Error obteniendo contenido: {str(e)}")


def create_document(
    ctx: MCPContext,
    document_type_acronym: str,
    reference: str,
    user_id: str,
    case_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Crear un nuevo documento en estado borrador (draft).

    Args:
        ctx: Contexto MCP con schema_name
        document_type_acronym: Acrónimo del tipo de documento (INF, DICT, etc.)
        reference: Descripción/referencia del documento
        user_id: UUID del usuario creador
        case_id: UUID del expediente a vincular (opcional)

    Returns:
        Dict con:
        - success: bool
        - document_id: UUID del documento creado
        - status: "draft"
        - message: mensaje de confirmación

    Raises:
        ValueError: Si faltan parámetros requeridos
        ValidationError: Si el tipo de documento no es válido
        RuntimeError: Si hay error
    """
    logger.info(f"[MCP] create_document - type={document_type_acronym}, user={user_id}, schema={ctx.schema_name}")

    if not document_type_acronym:
        raise ValueError("document_type_acronym es requerido")
    if not reference:
        raise ValueError("reference es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        result = create_doc_service(
            document_type_acronym=document_type_acronym,
            reference=reference,
            creator_id=user_id,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] create_document - documento creado: {result.get('document_id')}")

        response = {
            "success": True,
            "document_id": result.get("document_id"),
            "status": result.get("status", "draft"),
            "message": result.get("message", "Documento creado exitosamente")
        }

        # Si se proporciona case_id, vincular el documento al expediente
        if case_id:
            try:
                link_query = """
                    INSERT INTO case_proposed_documents (case_id, document_draft_id, proposing_date, is_active)
                    VALUES (%s, %s, NOW(), true)
                """
                execute_query(
                    link_query,
                    (case_id, result.get("document_id")),
                    schema_name=ctx.schema_name
                )
                response["linked_to_case"] = case_id
                logger.info(f"[MCP] create_document - documento vinculado a expediente {case_id}")
            except Exception as link_error:
                logger.warning(f"[MCP] create_document - error vinculando a expediente: {link_error}")
                response["link_error"] = str(link_error)

        return response

    except ValidationError:
        raise
    except Exception as e:
        logger.error(f"[MCP] create_document - error: {e}")
        raise RuntimeError(f"Error creando documento: {str(e)}")


def save_document(
    ctx: MCPContext,
    document_id: str,
    user_id: str,
    content: Optional[str] = None,
    reference: Optional[str] = None,
    signers: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Guardar cambios en un documento borrador.

    Args:
        ctx: Contexto MCP con schema_name
        document_id: UUID del documento
        user_id: UUID del usuario (para validación)
        content: Contenido HTML del documento (opcional)
        reference: Nueva referencia/descripción (opcional)
        signers: Lista de firmantes [{user_id, email, is_numerator}] (opcional)

    Returns:
        Dict con:
        - success: bool
        - document_id: UUID del documento
        - message: mensaje de confirmación
        - last_modified_at: fecha de última modificación

    Raises:
        ValueError: Si document_id está vacío o no hay cambios
        DocumentNotFoundError: Si el documento no existe
        DocumentStateError: Si el documento no está en estado editable
        RuntimeError: Si hay error
    """
    logger.info(f"[MCP] save_document - document_id={document_id}, user={user_id}, schema={ctx.schema_name}")

    if not document_id:
        raise ValueError("document_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    # Verificar que hay al menos un cambio
    if content is None and reference is None and signers is None:
        raise ValueError("Debe proporcionar al menos un campo a actualizar (content, reference o signers)")

    try:
        result = save_document_changes(
            document_id=document_id,
            reference=reference,
            content=content,
            signers=signers,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] save_document - cambios guardados en documento {document_id}")

        return {
            "success": result.get("success", True),
            "document_id": result.get("document_id"),
            "message": result.get("message", "Cambios guardados exitosamente"),
            "last_modified_at": str(result.get("last_modified_at")) if result.get("last_modified_at") else None
        }

    except (DocumentNotFoundError, DocumentStateError, ValidationError):
        raise
    except Exception as e:
        logger.error(f"[MCP] save_document - error: {e}")
        raise RuntimeError(f"Error guardando documento: {str(e)}")


async def start_signing(
    ctx: MCPContext,
    document_id: str,
    user_id: str
) -> Dict[str, Any]:
    """
    Iniciar proceso de firma de un documento.
    Cambia el estado de 'draft' a 'sent_to_sign'.

    Args:
        ctx: Contexto MCP con schema_name
        document_id: UUID del documento
        user_id: UUID del usuario que inicia la firma (debe ser el creador)

    Returns:
        Dict con:
        - success: bool
        - message: mensaje de confirmación

    Raises:
        ValueError: Si faltan parámetros
        ValidationError: Si el documento no tiene firmantes/numerador
        AuthorizationError: Si el usuario no es el creador
        DocumentStateError: Si el documento no está en estado 'draft'
        RuntimeError: Si hay error
    """
    logger.info(f"[MCP] start_signing - document_id={document_id}, user={user_id}, schema={ctx.schema_name}")

    if not document_id:
        raise ValueError("document_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        result = await start_document_signing_process(
            document_id=document_id,
            user_id=user_id,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] start_signing - proceso de firma iniciado para documento {document_id}")

        return {
            "success": result.get("success", True),
            "message": result.get("message", "Proceso de firma iniciado exitosamente")
        }

    except (ValidationError, AuthorizationError, DocumentStateError):
        raise
    except Exception as e:
        logger.error(f"[MCP] start_signing - error: {e}")
        raise RuntimeError(f"Error iniciando proceso de firma: {str(e)}")


async def sign_document(
    ctx: MCPContext,
    document_id: str,
    user_id: str
) -> Dict[str, Any]:
    """
    Firmar un documento como el usuario actual.
    Ejecuta la firma digital del usuario sobre el documento.

    Args:
        ctx: Contexto MCP con schema_name
        document_id: UUID del documento a firmar
        user_id: UUID del usuario firmante

    Returns:
        Dict con resultado de la firma

    Raises:
        ValueError: Si faltan parametros requeridos
        DocumentNotFoundError: Si el documento no existe
        DocumentStateError: Si el documento no esta en estado firmable
        AuthorizationError: Si el usuario no tiene permisos para firmar
        RuntimeError: Si hay error
    """
    logger.info(f"[MCP] sign_document - document_id={document_id}, user={user_id}, schema={ctx.schema_name}")

    if not document_id:
        raise ValueError("document_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        result = await super_sign_document(
            document_id=document_id,
            user_id=user_id,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] sign_document - documento {document_id} firmado por {user_id}")

        return result

    except (DocumentNotFoundError, DocumentStateError, AuthorizationError, ValidationError):
        raise
    except Exception as e:
        logger.error(f"[MCP] sign_document - error: {e}")
        raise RuntimeError(f"Error firmando documento: {str(e)}")


def reject_document(
    ctx: MCPContext,
    document_id: str,
    user_id: str,
    reason: str
) -> Dict[str, Any]:
    """
    Rechazar un documento proporcionando una razon.
    El documento vuelve a estado editable para correcciones.

    Args:
        ctx: Contexto MCP con schema_name
        document_id: UUID del documento a rechazar
        user_id: UUID del usuario que rechaza
        reason: Motivo del rechazo

    Returns:
        Dict con resultado del rechazo

    Raises:
        ValueError: Si faltan parametros requeridos
        DocumentNotFoundError: Si el documento no existe
        DocumentStateError: Si el documento no esta en estado rechazable
        AuthorizationError: Si el usuario no tiene permisos para rechazar
        RuntimeError: Si hay error
    """
    logger.info(f"[MCP] reject_document - document_id={document_id}, user={user_id}, schema={ctx.schema_name}")

    if not document_id:
        raise ValueError("document_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")
    if not reason:
        raise ValueError("reason es requerido")

    try:
        result = reject_doc_service(
            document_id=document_id,
            user_id=user_id,
            reason=reason,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] reject_document - documento {document_id} rechazado por {user_id}")

        return result

    except (DocumentNotFoundError, DocumentStateError, AuthorizationError, ValidationError):
        raise
    except Exception as e:
        logger.error(f"[MCP] reject_document - error: {e}")
        raise RuntimeError(f"Error rechazando documento: {str(e)}")


def delete_document(
    ctx: MCPContext,
    document_id: str,
    user_id: str
) -> Dict[str, Any]:
    """
    Eliminar un documento borrador.
    Solo se pueden eliminar documentos en estado draft.

    Args:
        ctx: Contexto MCP con schema_name
        document_id: UUID del documento a eliminar
        user_id: UUID del usuario que elimina

    Returns:
        Dict con resultado de la eliminacion

    Raises:
        ValueError: Si faltan parametros requeridos
        DocumentNotFoundError: Si el documento no existe
        DocumentStateError: Si el documento no esta en estado eliminable
        AuthorizationError: Si el usuario no tiene permisos para eliminar
        RuntimeError: Si hay error
    """
    logger.info(f"[MCP] delete_document - document_id={document_id}, user={user_id}, schema={ctx.schema_name}")

    if not document_id:
        raise ValueError("document_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        result = delete_doc_service(
            document_id=document_id,
            user_id=user_id,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] delete_document - documento {document_id} eliminado por {user_id}")

        return result

    except (DocumentNotFoundError, DocumentStateError, AuthorizationError, ValidationError):
        raise
    except Exception as e:
        logger.error(f"[MCP] delete_document - error: {e}")
        raise RuntimeError(f"Error eliminando documento: {str(e)}")


def search_document_by_number(
    ctx: MCPContext,
    doc_number: str,
    user_id: str
) -> Dict[str, Any]:
    """
    Buscar documento oficial por numero exacto (ej: INF-2026-00000060-TXST-TESO).

    Args:
        ctx: Contexto MCP con schema_name
        doc_number: Numero oficial del documento (ej: "INF-2026-00000060-TXST-TESO")
        user_id: UUID del usuario (no se usa para filtrar, busqueda global)

    Returns:
        Dict con found (bool), document (info o None), search_term

    Raises:
        ValueError: Si doc_number esta vacio
        RuntimeError: Si hay error de BD
    """
    logger.info(f"[MCP] search_document_by_number - doc_number={doc_number}, schema={ctx.schema_name}")

    if not doc_number:
        raise ValueError("doc_number es requerido")

    try:
        from services.documents.retrieval.official_search import search_official_document_by_number

        result = search_official_document_by_number(
            doc_number,
            user_id=None,
            schema_name=ctx.schema_name
        )

        found = result.get("found", False)
        logger.info(f"[MCP] search_document_by_number - encontrado: {found}")

        return result

    except ValidationError as e:
        logger.error(f"[MCP] search_document_by_number - error de validacion: {e}")
        raise
    except Exception as e:
        logger.error(f"[MCP] search_document_by_number - error: {e}")
        raise RuntimeError(f"Error buscando documento por numero: {str(e)}")


async def get_signature_details(
    ctx: MCPContext,
    document_id: str,
    user_id: str
) -> Dict[str, Any]:
    """
    Obtener detalles de firma de un documento (firmantes, estado de cada uno).

    Args:
        ctx: Contexto MCP con schema_name
        document_id: UUID del documento
        user_id: UUID del usuario solicitante

    Returns:
        Dict con document (info), current_signer, signature_progress, can_sign

    Raises:
        ValueError: Si faltan parametros requeridos
        DocumentNotFoundError: Si el documento no existe
        RuntimeError: Si hay error
    """
    logger.info(f"[MCP] get_signature_details - document_id={document_id}, user_id={user_id}, schema={ctx.schema_name}")

    if not document_id:
        raise ValueError("document_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        from services.documents.signing.details_builder import build_signature_details_response

        result = await build_signature_details_response(
            document_id,
            user_id,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] get_signature_details - detalles obtenidos para documento {document_id}")

        return result

    except DocumentNotFoundError as e:
        logger.error(f"[MCP] get_signature_details - documento no encontrado: {e}")
        raise
    except Exception as e:
        logger.error(f"[MCP] get_signature_details - error: {e}")
        raise RuntimeError(f"Error obteniendo detalles de firma: {str(e)}")
