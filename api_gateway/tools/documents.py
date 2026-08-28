from shared.logging import get_logger
from typing import Dict, Any, Optional, List
from api_gateway.context import MCPContext
from services.document_service import get_user_documents as _get_user_documents_impl
from services.documents.unified_details import get_unified_document_details
from services.documents.lifecycle.creation import create_document as create_doc_service
from services.documents.editing import save_document_changes
from services.documents.signing import start_document_signing_process
from services.documents.signing.unified_signing import super_sign_document
from services.documents.lifecycle.rejection import reject_document as reject_doc_service
from services.documents.lifecycle.deletion import delete_document as delete_doc_service
from shared.exceptions import ValidationError, DocumentNotFoundError, DocumentStateError, AuthorizationError, GDIBaseException, SpecialLaneBusyError, SignerTurnPendingError
from services.documents.permissions import can_user_view_document
from services.documents.retrieval.pending_signatures import _is_my_turn_condition
from database import fetch_one, fetch_all
from api_gateway.tools._sanitize import strip_storage_urls

logger = get_logger(__name__)


async def _resolve_sender_sector_id(user_id: str, *, schema_name: str):
    return await fetch_one(
        """
        SELECT sector_id FROM (
            SELECT s.id as sector_id, 1 as priority
            FROM users u
            JOIN sectors s ON u.sector_id = s.id
            WHERE u.id = $1 AND s.is_active = true

            UNION ALL

            SELECT s.id as sector_id, 2 as priority
            FROM user_sector_permissions usp
            JOIN sectors s ON usp.sector_id = s.id
            WHERE usp.user_id = $2 AND usp.can_edit = true AND s.is_active = true
        ) sub
        ORDER BY priority
        LIMIT 1
        """,
        user_id, user_id, schema_name=schema_name
    )


async def _get_linked_case(document_id: str, user_id: str, schema_name: str) -> Optional[Dict[str, Any]]:
    from services.cases.permissions import can_user_view_case

    query_official = """
        SELECT
            c.id as case_id,
            c.case_number,
            c.reference as case_reference,
            cod.linking_date,
            ct.is_reserved,
            'official' as link_type
        FROM case_official_documents cod
        JOIN cases c ON c.id = cod.case_id
        JOIN case_templates ct ON ct.id = c.case_template_id
        WHERE cod.official_document_id = $1 AND cod.is_active = true
        LIMIT 1
    """

    result = await fetch_one(query_official, document_id, schema_name=schema_name)

    if result:
        if result["is_reserved"] and not await can_user_view_case(
            str(result["case_id"]), user_id, schema_name=schema_name
        ):
            return None
        return {
            "case_id": str(result["case_id"]),
            "case_number": result["case_number"],
            "case_reference": result["case_reference"],
            "linked_date": str(result["linking_date"]) if result["linking_date"] else None,
            "link_type": "official"
        }

    query_proposed = """
        SELECT
            c.id as case_id,
            c.case_number,
            c.reference as case_reference,
            cpd.proposing_date,
            ct.is_reserved,
            'proposed' as link_type
        FROM case_proposed_documents cpd
        JOIN cases c ON c.id = cpd.case_id
        JOIN case_templates ct ON ct.id = c.case_template_id
        WHERE cpd.document_draft_id = $1 AND cpd.is_active = true
        LIMIT 1
    """

    result = await fetch_one(query_proposed, document_id, schema_name=schema_name)

    if result:
        if result["is_reserved"] and not await can_user_view_case(
            str(result["case_id"]), user_id, schema_name=schema_name
        ):
            return None
        return {
            "case_id": str(result["case_id"]),
            "case_number": result["case_number"],
            "case_reference": result["case_reference"],
            "linked_date": str(result["proposing_date"]) if result["proposing_date"] else None,
            "link_type": "proposed"
        }

    return None


async def search_documents(
    ctx: MCPContext,
    user_id: str,
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None,
    status: Optional[str] = None,
    document_type: Optional[str] = None,
    case_id: Optional[str] = None,
    min_signers: Optional[int] = None,
    sector_filter: Optional[str] = None,
    text_search: Optional[str] = None,
    date_filter: Optional[str] = None
) -> Dict[str, Any]:
    logger.info(f"[MCP] search_documents - schema={ctx.schema_name}, page={page}, user_id={user_id}, case_id={case_id}")

    if page_size > 100:
        raise ValueError("page_size máximo es 100")

    if not user_id:
        raise ValueError("user_id es requerido para search_documents (necesario para permisos)")

    try:
        raw = await _get_user_documents_impl(
            user_id=user_id,
            status_filter=status,
            doc_number=search,
            document_type=document_type,
            case_id=case_id,
            min_signers=min_signers,
            sector_filter=sector_filter,
            search=text_search,
            date_filter=date_filter,
            page=page,
            page_size=page_size,
            schema_name=ctx.schema_name
        )

        has_next = raw["has_next"]
        logger.info(
            f"[MCP] search_documents - pagina {raw['page']} "
            f"(docs={len(raw['documents'])}, has_next={has_next})"
        )

        _DATETIME_FIELDS = ("last_modified_at",)
        serialized_docs = []
        for doc in raw["documents"]:
            d = dict(doc)
            for field in _DATETIME_FIELDS:
                val = d.get(field)
                if val is not None and hasattr(val, "isoformat"):
                    d[field] = val.isoformat()
            serialized_docs.append(d)

        return {
            "documents": serialized_docs,
            "pagination": {
                "total": raw.get("total"),
                "page": raw["page"],
                "page_size": raw["page_size"],
                "total_pages": raw.get("total_pages"),
                "has_next": raw["has_next"],
                "has_previous": raw["has_previous"],
            },
            "filters_applied": {
                "status": status,
                "document_type": document_type,
                "doc_number": search,
                "case_id": case_id,
                "min_signers": min_signers,
                "sector_filter": sector_filter,
                "text_search": text_search,
                "date_filter": date_filter,
            },
        }

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] search_documents - error: {e}")
        raise RuntimeError("Error buscando documentos")


async def get_document(
    ctx: MCPContext,
    document_id: str,
    user_id: str
) -> Dict[str, Any]:
    logger.info(f"[MCP] get_document - document_id={document_id}, schema={ctx.schema_name}, user_id={user_id}")

    if not user_id:
        raise ValueError("user_id es requerido")

    exists = await fetch_one(
        """
        SELECT 1 FROM document_draft WHERE id = $1 AND is_deleted = false
        UNION ALL
        SELECT 1 FROM official_documents WHERE id = $1 AND signed_at IS NOT NULL
        LIMIT 1
        """,
        document_id,
        schema_name=ctx.schema_name
    )
    if not exists:
        raise DocumentNotFoundError(f"Documento {document_id} no encontrado")

    if not await can_user_view_document(document_id, user_id, schema_name=ctx.schema_name):
        raise ValueError("Usuario no tiene permisos para ver este documento")

    try:
        result = await get_unified_document_details(
            document_id=document_id,
            user_id=user_id,
            schema_name=ctx.schema_name
        )

        linked_case = await _get_linked_case(document_id, user_id, ctx.schema_name)
        if linked_case:
            result["linked_case"] = linked_case
            logger.info(f"[MCP] get_document - expediente vinculado: {linked_case['case_number']}")
        else:
            result["linked_case"] = None
            logger.info(f"[MCP] get_document - sin expediente vinculado")

        logger.info(f"[MCP] get_document - documento encontrado, estado: {result.get('status')}")
        return strip_storage_urls(result)

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_document - error: {e}")
        raise RuntimeError("Error obteniendo documento")


async def get_pending_signatures(
    ctx: MCPContext,
    user_id: str
) -> Dict[str, Any]:
    logger.info(f"[MCP] get_pending_signatures - user_id={user_id}, schema={ctx.schema_name}")

    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        query = f"""
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
                WHERE ds.user_id = $1
                  AND ds.status = 'pending'
                  AND d.status = 'sent_to_sign'
            )
            SELECT
                pd.*,
                CASE
                    WHEN pd.is_numerator = true THEN 'numerator'
                    ELSE 'signer'
                END as signer_role,
                -- ¿Es el turno del usuario? (definición única, GDI-187)
                {_is_my_turn_condition('pd')} as is_my_turn
            FROM pending_docs pd
            WHERE {_is_my_turn_condition('pd')}
            ORDER BY pd.sent_to_sign_at DESC
        """

        results = await fetch_all(query, user_id, schema_name=ctx.schema_name)

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

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_pending_signatures - error: {e}")
        raise RuntimeError("Error obteniendo firmas pendientes")


async def get_document_content(
    ctx: MCPContext,
    document_id: str,
    user_id: str
) -> Dict[str, Any]:
    logger.info(f"[MCP] get_document_content - document_id={document_id}, schema={ctx.schema_name}, user_id={user_id}")

    if not user_id:
        raise ValueError("user_id es requerido")

    if not await can_user_view_document(document_id, user_id, schema_name=ctx.schema_name):
        raise ValueError("Usuario no tiene permisos para ver este documento")

    try:
        from services.documents.retrieval.content import get_official_document_content

        result = await get_official_document_content(document_id, ctx.schema_name)

        logger.info(f"[MCP] get_document_content - contenido obtenido ({len(result['content']['html'])} chars)")
        return result

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_document_content - error: {e}")
        raise RuntimeError("Error obteniendo contenido")


async def create_document(
    ctx: MCPContext,
    document_type_acronym: str,
    reference: str,
    user_id: str,
    case_id: Optional[str] = None,
    recipients: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    logger.info(f"[MCP] create_document - type={document_type_acronym}, user={user_id}, schema={ctx.schema_name}")

    if not document_type_acronym:
        raise ValueError("document_type_acronym es requerido")
    if not reference:
        raise ValueError("reference es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        sender_sector_id = None
        if recipients:
            user_sector = await _resolve_sender_sector_id(user_id, schema_name=ctx.schema_name)
            if user_sector:
                sender_sector_id = str(user_sector['sector_id'])

        result = await create_doc_service(
            document_type_acronym=document_type_acronym,
            reference=reference,
            creator_id=user_id,
            schema_name=ctx.schema_name,
            auth_source="mcp_gateway",
            recipients=recipients,
            sender_sector_id=sender_sector_id
        )

        logger.info(f"[MCP] create_document - documento creado: {result.get('document_id')}")

        response = {
            "success": True,
            "document_id": result.get("document_id"),
            "status": result.get("status", "draft"),
            "message": result.get("message", "Documento creado exitosamente")
        }

        if case_id:
            try:
                from services.cases.documents import propose_document_to_case
                await propose_document_to_case(
                    case_id=case_id,
                    document_draft_id=result.get("document_id"),
                    proposing_user_id=user_id,
                    schema_name=ctx.schema_name,
                )
                response["linked_to_case"] = case_id
                response["link_status"] = "linked"
                logger.info(f"[MCP] create_document - documento propuesto a expediente {case_id}")
            except ValidationError as ve:
                logger.warning(f"[MCP] create_document - vinculacion RECHAZADA por REGLA 1: {ve}")
                response["linked_to_case"] = None
                response["link_status"] = "rejected"
                response["link_error"] = str(ve)
            except Exception as link_error:
                logger.warning(f"[MCP] create_document - error proponiendo a expediente: {link_error}")
                response["linked_to_case"] = None
                response["link_status"] = "error"
                response["link_error"] = str(link_error)

        return response

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] create_document - error: {e}")
        raise RuntimeError("Error creando documento")


async def save_document(
    ctx: MCPContext,
    document_id: str,
    user_id: str,
    content: Optional[str] = None,
    reference: Optional[str] = None,
    signers: Optional[List[Dict[str, Any]]] = None,
    recipients: Optional[Dict[str, Any]] = None,
    proposed_case_ids: Optional[List[str]] = None
) -> Dict[str, Any]:
    logger.info(f"[MCP] save_document - document_id={document_id}, user={user_id}, schema={ctx.schema_name}")

    if not document_id:
        raise ValueError("document_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    if all(x is None for x in [content, reference, signers, recipients, proposed_case_ids]):
        raise ValueError("Debe proporcionar al menos un campo a actualizar (content, reference, signers, recipients o proposed_case_ids)")

    sender_sector_id = None
    if recipients is not None:
        user_sector = await _resolve_sender_sector_id(user_id, schema_name=ctx.schema_name)
        if user_sector:
            sender_sector_id = str(user_sector['sector_id'])

    try:
        result = await save_document_changes(
            document_id=document_id,
            reference=reference,
            content=content,
            signers=signers,
            recipients=recipients,
            sender_sector_id=sender_sector_id,
            proposed_case_ids=proposed_case_ids,
            user_id=user_id,
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
    except GDIBaseException:
        raise
    except Exception as e:
        logger.error(f"[MCP] save_document - error inesperado: {e}")
        raise


async def start_signing(
    ctx: MCPContext,
    document_id: str,
    user_id: str
) -> Dict[str, Any]:
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

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] start_signing - error: {e}")
        raise RuntimeError("Error iniciando proceso de firma")


async def sign_document(
    ctx: MCPContext,
    document_id: str,
    user_id: str,
) -> Dict[str, Any]:
    logger.info(f"[REST API] sign_document - document_id={document_id}, user={user_id}, schema={ctx.schema_name}")

    if not document_id:
        raise ValueError("document_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        result = await super_sign_document(
            document_id,
            user_id,
            schema_name=ctx.schema_name,
        )
        return result
    except (ValidationError, AuthorizationError, DocumentStateError,
            SpecialLaneBusyError, SignerTurnPendingError):
        raise
    except Exception as e:
        logger.error(f"[REST API] sign_document - error: {e}")
        raise


async def reject_document(
    ctx: MCPContext,
    document_id: str,
    user_id: str,
    reason: str
) -> Dict[str, Any]:
    logger.info(f"[MCP] reject_document - document_id={document_id}, user={user_id}, schema={ctx.schema_name}")

    if not document_id:
        raise ValueError("document_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")
    if not reason:
        raise ValueError("reason es requerido")

    try:
        result = await reject_doc_service(
            document_id=document_id,
            user_id=user_id,
            reason=reason,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] reject_document - documento {document_id} rechazado por {user_id}")

        return result

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] reject_document - error: {e}")
        raise RuntimeError("Error rechazando documento")


async def delete_document(
    ctx: MCPContext,
    document_id: str,
    user_id: str
) -> Dict[str, Any]:
    logger.info(f"[MCP] delete_document - document_id={document_id}, user={user_id}, schema={ctx.schema_name}")

    if not document_id:
        raise ValueError("document_id es requerido")
    if not user_id:
        raise ValueError("user_id es requerido")

    try:
        result = await delete_doc_service(
            document_id=document_id,
            user_id=user_id,
            schema_name=ctx.schema_name
        )

        logger.info(f"[MCP] delete_document - documento {document_id} eliminado por {user_id}")

        return result

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] delete_document - error: {e}")
        raise RuntimeError("Error eliminando documento")


async def search_document_by_number(
    ctx: MCPContext,
    doc_number: str,
    user_id: str
) -> Dict[str, Any]:
    logger.info(f"[MCP] search_document_by_number - doc_number={doc_number}, schema={ctx.schema_name}")

    if not doc_number:
        raise ValueError("doc_number es requerido")

    try:
        from services.documents.retrieval.official_search import search_official_document_by_number

        result = await search_official_document_by_number(
            doc_number,
            user_id=user_id,
            schema_name=ctx.schema_name
        )

        if result.get("found") and result.get("document"):
            doc_id = result["document"].get("id") or result["document"].get("document_id")
            if doc_id and not await can_user_view_document(doc_id, user_id, schema_name=ctx.schema_name):
                logger.info(f"[MCP] search_document_by_number - usuario sin permisos, ocultando resultado")
                return {"found": False, "document": None, "search_term": doc_number}

        found = result.get("found", False)
        logger.info(f"[MCP] search_document_by_number - encontrado: {found}")

        return result

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] search_document_by_number - error: {e}")
        raise RuntimeError("Error buscando documento por numero")


async def get_signature_details(
    ctx: MCPContext,
    document_id: str,
    user_id: str
) -> Dict[str, Any]:
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

        return strip_storage_urls(result)

    except (ValueError, GDIBaseException):
        raise
    except Exception as e:
        logger.error(f"[MCP] get_signature_details - error: {e}")
        raise RuntimeError("Error obteniendo detalles de firma")
