
from typing import Dict, Any, Optional
from shared.logging import get_logger
import uuid

from fastapi.concurrency import run_in_threadpool
from database import fetch_all, execute, transaction
from shared.exceptions import (
    NotFoundError,
    AuthorizationError,
    ValidationError,
    DatabaseError,
    BusinessLogicError,
    TransientLookupError,
)

logger = get_logger(__name__)


async def get_case_documents(case_id: str, *, schema_name: str) -> Dict[str, Any]:
    from services.case_queries import get_official_documents_query, get_proposed_documents_query
    from config.constants import DOCUMENTS_ERROR

    try:
        logger.info(f"Fetching documents for case: {case_id}")

        official_docs = await fetch_all(get_official_documents_query(), case_id, schema_name=schema_name)
        proposed_docs = await fetch_all(get_proposed_documents_query(), case_id, schema_name=schema_name)

        official_list = []
        for doc in (official_docs or []):
            official_list.append({
                "id": str(doc['id']),
                "document_id": str(doc['document_id']),
                "order": doc['order_number'],
                "official_number": doc['official_number'],
                "_pdf_location": doc.get('pdf_location') or "oficial",
                "reference": doc['reference'],
                "linked_date": doc['linking_date'].isoformat() if doc['linking_date'] else None,
                "is_active": doc['is_active'],
                "pdf_url": None,
                "short_resume": doc.get('short_resume'),
                "linked_by": doc.get('linked_by'),
                "linked_sector": doc.get('linked_sector'),
                "is_reserved": bool(doc.get('is_reserved', False)),
                "is_public": doc.get('document_type_visibility') == 'publico',
            })

        if official_list:
            from services.storage.cloudflare import get_tenant_r2_client
            r2_client = await get_tenant_r2_client(schema_name=schema_name)
            for doc in official_list:
                official_number = doc.get("official_number")
                _loc = doc.pop("_pdf_location", "oficial")
                if official_number:
                    doc["pdf_url"] = await run_in_threadpool(
                        r2_client.get_oficial_url, official_number, _loc
                    )

        proposed_list = [
            {
                "id": str(doc['id']),
                "document_draft_id": str(doc['document_draft_id']),
                "reference": doc['reference'],
                "status": doc['status'],
                "document_number": doc.get('document_number'),
                "document_type_name": doc.get('document_type_name'),
                "document_type_acronym": doc.get('document_type_acronym'),
                "is_reserved": bool(doc.get('is_reserved', False)),
                "is_public": doc.get('document_type_visibility') == 'publico',
                "can_link": bool(doc.get('can_link', False)),
                "proposed_date": doc['proposing_date'].isoformat() if doc['proposing_date'] else None,
                "proposed_by": doc['proposed_by'],
                "short_resume": doc.get('short_resume'),
            }
            for doc in (proposed_docs or [])
        ]

        logger.info(f"Found {len(official_list)} official and {len(proposed_list)} proposed documents for case {case_id}")

        return {
            "official": official_list,
            "proposed": proposed_list,
            "total_official": len(official_list),
            "total_proposed": len(proposed_list)
        }

    except Exception as e:
        logger.error(f"Error fetching case documents: {str(e)}")
        raise BusinessLogicError(DOCUMENTS_ERROR)


async def _assert_can_link_documents(
    case_id: str,
    linking_user_id: str,
    *,
    schema_name: str
) -> None:
    from services.case_service import CaseService

    user_sector_ids = await CaseService.get_user_editable_sector_ids(linking_user_id, schema_name=schema_name)
    logger.debug(f"User {linking_user_id} editable sectors for linking: {user_sector_ids}")

    if not user_sector_ids:
        raise AuthorizationError("Usuario sin permisos de edicion en ningun sector")

    permission_check = """
        SELECT 1
        FROM cases c
        WHERE c.id = $1
        AND (
            EXISTS (
                SELECT 1 FROM case_movements cm
                WHERE cm.case_id = c.id
                AND cm.assigned_sector_id = ANY($2::uuid[])
                AND cm.is_active = true
            )
            OR
            EXISTS (
                SELECT 1 FROM case_movements cm
                WHERE cm.case_id = c.id
                AND cm.type = 'transfer'
                AND cm.is_active = false
                AND cm.admin_sector_id = ANY($3::uuid[])
                AND cm.closed_at = (
                    SELECT MAX(cm2.closed_at)
                    FROM case_movements cm2
                    WHERE cm2.case_id = c.id
                    AND cm2.type = 'transfer'
                    AND cm2.is_active = false
                )
            )
            OR
            (
                EXISTS (
                    SELECT 1 FROM case_movements cm
                    WHERE cm.case_id = c.id
                    AND cm.type = 'creation'
                    AND cm.admin_sector_id = ANY($4::uuid[])
                )
                AND NOT EXISTS (
                    SELECT 1 FROM case_movements cm
                    WHERE cm.case_id = c.id
                    AND cm.type = 'transfer'
                )
            )
        )
    """

    permission_result = await fetch_all(
        permission_check,
        case_id, user_sector_ids, user_sector_ids, user_sector_ids,
        schema_name=schema_name
    )

    if not permission_result:
        raise AuthorizationError("No tiene permisos para vincular documentos a este expediente")


async def link_official_document(
    case_id: str,
    official_document_id: str,
    linking_user_id: str,
    user_sector_id: str,
    *,
    schema_name: str,
    reason_override: str | None = None,
    auth_source: str = "jwt",
    system_generated: bool = False
) -> Dict[str, Any]:
    try:
        case_result = await fetch_all(
            "SELECT c.id, c.case_number FROM cases c WHERE c.id = $1",
            case_id,
            schema_name=schema_name
        )

        if not case_result:
            raise NotFoundError(f"Expediente no encontrado: {case_id}")

        case_number = case_result[0]['case_number']

        if system_generated:
            logger.info(
                f"Linking system-generated document {official_document_id} to case {case_id} "
                f"(permission check skipped)"
            )
        else:
            await _assert_can_link_documents(case_id, linking_user_id, schema_name=schema_name)

        doc_result = await fetch_all(
            "SELECT id, official_number, reference FROM official_documents WHERE id = $1 AND signed_at IS NOT NULL",
            official_document_id,
            schema_name=schema_name
        )

        if not doc_result:
            raise NotFoundError(f"Documento oficial no encontrado: {official_document_id}")

        official_number = doc_result[0]['official_number']
        doc_reference = doc_result[0]['reference']

        duplicate_result = await fetch_all(
            "SELECT 1 FROM case_official_documents WHERE case_id = $1 AND official_document_id = $2 AND is_active = true",
            case_id, official_document_id,
            schema_name=schema_name
        )

        if duplicate_result:
            raise ValidationError(f"El documento {official_number} ya está vinculado a este expediente")

        try:
            async with transaction(schema_name=schema_name, user_id=linking_user_id, auth_source=auth_source) as conn:
                await conn.execute("SELECT 1 FROM cases WHERE id = $1 FOR UPDATE", case_id)

                reserved_check = await conn.fetchrow(
                    """
                    SELECT dt.is_reserved AS doc_reserved, ct.is_reserved AS case_reserved
                    FROM official_documents od
                    JOIN document_types dt ON od.document_type_id = dt.id
                    CROSS JOIN cases c
                    JOIN case_templates ct ON ct.id = c.case_template_id
                    WHERE od.id = $1 AND c.id = $2
                    """,
                    official_document_id, case_id
                )
                if reserved_check is None:
                    logger.warning(
                        "gdi287.reserved_check_phantom doc=%s case=%s — reserved_check "
                        "volvió vacío al vincular un oficial; se responde 503 en vez de "
                        "asumir 'no reservado'",
                        official_document_id[:8], case_id[:8],
                    )
                    raise TransientLookupError(
                        "No se pudo verificar la política de reserva del documento "
                        "en este momento. Reintentá en unos segundos."
                    )
                if reserved_check['doc_reserved'] and not reserved_check['case_reserved']:
                    raise ValidationError(
                        "Un documento reservado solo puede vincularse a un expediente reservado"
                    )

                max_order_row = await conn.fetchrow(
                    "SELECT COALESCE(MAX(order_number), 0) as max_order FROM case_official_documents WHERE case_id = $1",
                    case_id
                )
                next_order = (max_order_row['max_order'] + 1) if max_order_row else 1

                link_id = str(uuid.uuid4())

                linking_row = await conn.fetchrow(
                    """
                    INSERT INTO case_official_documents (
                        id, case_id, official_document_id,
                        linking_user_id, order_number,
                        linking_date, is_active
                    ) VALUES (
                        $1, $2, $3, $4, $5, NOW(), true
                    )
                    RETURNING linking_date
                    """,
                    link_id, case_id, official_document_id, linking_user_id, next_order
                )
                linking_date = linking_row['linking_date']

                admin_result = await conn.fetchrow(
                    """
                    SELECT s.id as admin_sector_id
                    FROM case_movements cm
                    JOIN sectors s ON cm.admin_sector_id = s.id
                    WHERE cm.case_id = $1
                      AND cm.is_active = false
                      AND cm.type IN ('creation', 'transfer')
                    ORDER BY cm.closed_at DESC
                    LIMIT 1
                    """,
                    case_id
                )
                admin_sector_id = admin_result['admin_sector_id'] if admin_result else user_sector_id

                movement_id = str(uuid.uuid4())
                await conn.execute(
                    """
                    INSERT INTO case_movements (
                        id, case_id, type, user_id,
                        creator_sector_id, admin_sector_id,
                        supporting_document_id,
                        reason, is_active, closed_at, closing_reason
                    ) VALUES (
                        $1, $2, 'document_link', $3, $4, $5, $6, $7, false, NOW(), 'Acción completada'
                    )
                    """,
                    movement_id, case_id, linking_user_id, user_sector_id, admin_sector_id,
                    official_document_id,
                    reason_override or f"Vinculó documento: {official_number} ({doc_reference})"
                )

        except (AuthorizationError, NotFoundError, ValidationError, TransientLookupError):
            raise
        except Exception as e:
            raise DatabaseError(f"Error en transacción de vinculación: {str(e)}")

        return {
            "link_id": link_id,
            "case_id": case_id,
            "case_number": case_number,
            "official_document_id": official_document_id,
            "official_number": official_number,
            "document_reference": doc_reference,
            "order_number": next_order,
            "linking_date": linking_date
        }

    except (AuthorizationError, NotFoundError, ValidationError, DatabaseError, TransientLookupError):
        raise
    except Exception as e:
        raise BusinessLogicError(f"Error vinculando documento: {str(e)}")


async def accept_proposed_document(
    case_id: str,
    proposed_id: str,
    user_id: str,
    user_sector_id: str,
    *,
    schema_name: str,
    auth_source: str = "jwt"
) -> Dict[str, Any]:
    from services.case_queries import get_proposed_document_by_id_query, deactivate_proposed_document_query
    from config.constants import (
        PROPOSED_DOCUMENT_NOT_FOUND,
        PROPOSED_DOCUMENT_ALREADY_PROCESSED,
        PROPOSED_DOCUMENT_NOT_SIGNED,
    )

    try:
        proposed = await fetch_all(
            get_proposed_document_by_id_query(), proposed_id, case_id, schema_name=schema_name
        )

        if not proposed:
            raise NotFoundError(PROPOSED_DOCUMENT_NOT_FOUND)

        proposed_doc = proposed[0]

        if not proposed_doc['is_active']:
            raise ValidationError(PROPOSED_DOCUMENT_ALREADY_PROCESSED)

        if proposed_doc['status'] != 'signed':
            raise ValidationError(
                PROPOSED_DOCUMENT_NOT_SIGNED.format(status=proposed_doc['status'])
            )

        reference = proposed_doc.get('reference', '')
        doc_number = proposed_doc.get('document_number', '')
        reason_msg = f"Aceptó la incorporación de {reference}"
        if doc_number:
            reason_msg += f" ({doc_number})"

        link_result = await link_official_document(
            case_id=case_id,
            official_document_id=str(proposed_doc['document_draft_id']),
            linking_user_id=user_id,
            user_sector_id=user_sector_id,
            schema_name=schema_name,
            reason_override=reason_msg,
            auth_source=auth_source
        )

        await execute(
            deactivate_proposed_document_query(), proposed_id,
            schema_name=schema_name, user_id=user_id, auth_source=auth_source
        )

        logger.info(f"Proposed document {proposed_id} accepted and linked to case {case_id}")

        return link_result

    except (AuthorizationError, NotFoundError, ValidationError, DatabaseError,
            TransientLookupError):
        raise
    except Exception as e:
        logger.error(f"Error accepting proposed document: {str(e)}")
        raise BusinessLogicError(f"Error aceptando documento propuesto: {str(e)}")


async def reject_proposed_document(
    case_id: str,
    proposed_id: str,
    user_id: str,
    user_sector_id: str = None,
    *,
    schema_name: str,
    auth_source: str = "jwt"
) -> Dict[str, Any]:
    from services.case_queries import get_proposed_document_by_id_query, deactivate_proposed_document_query
    from services.case_service import CaseService
    from config.constants import (
        PROPOSED_DOCUMENT_NOT_FOUND,
        PROPOSED_DOCUMENT_ALREADY_PROCESSED,
        PROPOSED_DOCUMENT_REJECT_NO_PERMISSION,
    )

    try:
        proposed = await fetch_all(
            get_proposed_document_by_id_query(), proposed_id, case_id, schema_name=schema_name
        )

        if not proposed:
            raise NotFoundError(PROPOSED_DOCUMENT_NOT_FOUND)

        proposed_doc = proposed[0]

        if not proposed_doc['is_active']:
            raise ValidationError(PROPOSED_DOCUMENT_ALREADY_PROCESSED)

        user_sector_ids = await CaseService.get_user_editable_sector_ids(user_id, schema_name=schema_name)

        if not user_sector_ids:
            raise AuthorizationError(PROPOSED_DOCUMENT_REJECT_NO_PERMISSION)

        permission_check = """
            SELECT 1
            FROM cases c
            WHERE c.id = $1
            AND (
                EXISTS (
                    SELECT 1 FROM case_movements cm
                    WHERE cm.case_id = c.id
                    AND cm.assigned_sector_id = ANY($2::uuid[])
                    AND cm.is_active = true
                )
                OR
                EXISTS (
                    SELECT 1 FROM case_movements cm
                    WHERE cm.case_id = c.id
                    AND cm.type = 'transfer'
                    AND cm.is_active = false
                    AND cm.admin_sector_id = ANY($3::uuid[])
                    AND cm.closed_at = (
                        SELECT MAX(cm2.closed_at)
                        FROM case_movements cm2
                        WHERE cm2.case_id = c.id
                        AND cm2.type = 'transfer'
                        AND cm2.is_active = false
                    )
                )
                OR
                (
                    EXISTS (
                        SELECT 1 FROM case_movements cm
                        WHERE cm.case_id = c.id
                        AND cm.type = 'creation'
                        AND cm.admin_sector_id = ANY($4::uuid[])
                    )
                    AND NOT EXISTS (
                        SELECT 1 FROM case_movements cm
                        WHERE cm.case_id = c.id
                        AND cm.type = 'transfer'
                    )
                )
            )
        """

        permission_result = await fetch_all(
            permission_check,
            case_id, user_sector_ids, user_sector_ids, user_sector_ids,
            schema_name=schema_name
        )

        if not permission_result:
            raise AuthorizationError(PROPOSED_DOCUMENT_REJECT_NO_PERMISSION)

        await execute(
            deactivate_proposed_document_query(), proposed_id,
            schema_name=schema_name, user_id=user_id, auth_source=auth_source
        )

        try:
            from services.cases.history import create_movement
            from config.constants import MOVEMENT_TYPE_DOCUMENT_PROPOSAL_REJECT

            admin_result = await fetch_all(
                """SELECT admin_sector_id FROM case_movements
                   WHERE case_id = $1 AND type IN ('creation', 'transfer')
                   ORDER BY created_at DESC LIMIT 1""",
                case_id, schema_name=schema_name
            )
            admin_sector_id = str(admin_result[0]['admin_sector_id']) if admin_result else user_sector_id

            reference = proposed_doc.get('reference', 'Sin referencia')
            doc_number = proposed_doc.get('document_number')
            reason = f"Rechazó la incorporación de {reference}"
            if doc_number:
                reason += f" ({doc_number})"

            effective_sector_id = user_sector_id
            if not effective_sector_id:
                user_sector_result = await fetch_all(
                    "SELECT sector_id FROM users WHERE id = $1",
                    user_id, schema_name=schema_name
                )
                effective_sector_id = str(user_sector_result[0]['sector_id']) if user_sector_result else admin_sector_id

            await create_movement(
                case_id=case_id,
                movement_type=MOVEMENT_TYPE_DOCUMENT_PROPOSAL_REJECT,
                user_id=user_id,
                creator_sector_id=effective_sector_id,
                admin_sector_id=admin_sector_id,
                reason=reason,
                supporting_document_id=str(proposed_doc['document_draft_id']) if proposed_doc.get('status') == 'signed' else None,
                schema_name=schema_name,
                auth_source=auth_source
            )
            logger.info(f"Rejection history recorded for case {case_id}")
        except Exception as hist_error:
            logger.warning(f"Error recording rejection history for case {case_id}: {hist_error}")

        logger.info(f"Proposed document {proposed_id} rejected for case {case_id}")

        return {
            "proposed_id": proposed_id,
            "document_reference": proposed_doc['reference'],
            "action": "rejected"
        }

    except (AuthorizationError, NotFoundError, ValidationError):
        raise
    except Exception as e:
        logger.error(f"Error rejecting proposed document: {str(e)}")
        raise BusinessLogicError(f"Error rechazando documento propuesto: {str(e)}")


async def propose_document_to_case(
    case_id: str,
    document_draft_id: str,
    proposing_user_id: Optional[str] = None,
    *,
    schema_name: str,
    auth_source: str = "jwt",
    proposing_citizen_id: Optional[str] = None,
) -> Dict[str, Any]:
    if not case_id:
        raise ValidationError("case_id es requerido")
    if not document_draft_id:
        raise ValidationError("document_draft_id es requerido")
    if bool(proposing_user_id) == bool(proposing_citizen_id):
        raise ValidationError("Se requiere exactamente uno de proposing_user_id o proposing_citizen_id")
    is_citizen_actor = proposing_citizen_id is not None
    proposer_id = proposing_citizen_id if is_citizen_actor else proposing_user_id

    try:
        logger.info(
            f"Proposing document {document_draft_id} to case {case_id} "
            f"by {'citizen' if is_citizen_actor else 'user'} {proposer_id}"
        )

        from database import check_document_exists
        if not await check_document_exists(document_draft_id, schema_name=schema_name):
            raise NotFoundError(
                f"Documento borrador no encontrado: {document_draft_id}"
            )

        async with transaction(schema_name=schema_name, user_id=proposer_id, auth_source=auth_source) as conn:
            reserved_check = await conn.fetchrow(
                """
                SELECT COALESCE(dt.is_reserved, false) AS doc_reserved, ct.is_reserved AS case_reserved
                FROM document_draft dd
                LEFT JOIN document_types dt ON dd.document_type_id = dt.id
                CROSS JOIN cases c
                JOIN case_templates ct ON ct.id = c.case_template_id
                WHERE dd.id = $1 AND c.id = $2
                """,
                document_draft_id, case_id
            )
            if reserved_check is None:
                logger.warning(
                    "gdi276.reserved_check_phantom doc=%s case=%s — reserved_check "
                    "volvió vacío; se responde 503 en vez de asumir 'no reservado'",
                    document_draft_id[:8], case_id[:8],
                )
                raise TransientLookupError(
                    "No se pudo verificar la política de reserva del documento "
                    "en este momento. Reintentá en unos segundos."
                )
            if reserved_check['doc_reserved'] and not reserved_check['case_reserved']:
                raise ValidationError(
                    "Un documento reservado solo puede proponerse a un expediente reservado"
                )

            if is_citizen_actor:
                await conn.execute(
                    """
                    INSERT INTO case_proposed_documents
                        (case_id, document_draft_id, proposing_citizen_id, proposing_date, is_active)
                    VALUES ($1, $2, $3, NOW(), true)
                    """,
                    case_id, document_draft_id, proposing_citizen_id,
                )
            else:
                await conn.execute(
                    """
                    INSERT INTO case_proposed_documents
                        (case_id, document_draft_id, proposing_user_id, proposing_date, is_active)
                    VALUES ($1, $2, $3, NOW(), true)
                    """,
                    case_id, document_draft_id, proposing_user_id,
                )

        logger.info(f"Document {document_draft_id} proposed to case {case_id}")

        try:
            from services.cases.history import create_movement
            from config.constants import MOVEMENT_TYPE_DOCUMENT_PROPOSAL

            admin_result = await fetch_all(
                """SELECT admin_sector_id FROM case_movements
                   WHERE case_id = $1 AND type IN ('creation', 'transfer')
                   ORDER BY created_at DESC LIMIT 1""",
                case_id, schema_name=schema_name
            )
            admin_sector_id = str(admin_result[0]['admin_sector_id']) if admin_result else None

            if is_citizen_actor:
                creator_sector_id = admin_sector_id
            else:
                user_sector_result = await fetch_all(
                    "SELECT sector_id FROM users WHERE id = $1",
                    proposing_user_id, schema_name=schema_name
                )
                creator_sector_id = (
                    str(user_sector_result[0]['sector_id'])
                    if user_sector_result and user_sector_result[0]['sector_id']
                    else admin_sector_id
                )

            if admin_sector_id and creator_sector_id:
                doc_row = await fetch_all(
                    "SELECT reference, document_number FROM document_draft WHERE id = $1",
                    document_draft_id, schema_name=schema_name
                )
                reference = doc_row[0]['reference'] if doc_row else 'Sin referencia'
                doc_number = doc_row[0]['document_number'] if doc_row else None
                reason = f"Propuso vincular {reference}"
                if doc_number:
                    reason += f" ({doc_number})"

                await create_movement(
                    case_id=case_id,
                    movement_type=MOVEMENT_TYPE_DOCUMENT_PROPOSAL,
                    user_id=None if is_citizen_actor else proposing_user_id,
                    citizen_id=proposing_citizen_id if is_citizen_actor else None,
                    creator_sector_id=creator_sector_id,
                    admin_sector_id=admin_sector_id,
                    reason=reason,
                    supporting_document_id=document_draft_id,
                    schema_name=schema_name,
                    auth_source=auth_source,
                )
                logger.info(f"Proposal history recorded for case {case_id}")
            else:
                logger.warning(
                    f"No se pudo registrar movement de propose para case {case_id}: "
                    f"faltan sectores (admin={admin_sector_id}, creator={creator_sector_id})"
                )
        except Exception as hist_error:
            logger.warning(f"Error recording proposal history for case {case_id}: {hist_error}")

        return {
            "case_id": case_id,
            "document_draft_id": document_draft_id,
            "message": "Documento propuesto para vincular al expediente",
        }

    except (ValidationError, NotFoundError, DatabaseError, TransientLookupError):
        raise
    except Exception as e:
        logger.error(f"Error proposing document to case: {str(e)}")
        raise BusinessLogicError(f"Error proponiendo documento: {str(e)}")
