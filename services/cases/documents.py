"""
Módulo de documentos para expedientes.
Funciones para gestionar documentos dentro de expedientes.
"""

from typing import List, Dict, Any
from shared.logging import get_logger
import uuid

from fastapi.concurrency import run_in_threadpool
from database import fetch_all, execute, transaction
from shared.exceptions import (
    NotFoundError,
    AuthorizationError,
    ValidationError,
    DatabaseError,
    BusinessLogicError
)

logger = get_logger(__name__)


async def get_case_documents(case_id: str, *, schema_name: str) -> Dict[str, Any]:
    """
    Obtener documentos vinculados al expediente (oficiales y propuestos).
    """
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
                "reference": doc['reference'],
                "linked_date": doc['linking_date'].isoformat() if doc['linking_date'] else None,
                "is_active": doc['is_active'],
                "pdf_url": None,
                "short_resume": doc.get('short_resume'),
                "linked_by": doc.get('linked_by'),
                "linked_sector": doc.get('linked_sector'),
            })

        if official_list:
            from services.storage.cloudflare import get_tenant_r2_client
            r2_client = await get_tenant_r2_client(schema_name=schema_name)
            for doc in official_list:
                official_number = doc.get("official_number")
                if official_number:
                    doc["pdf_url"] = await run_in_threadpool(r2_client.get_oficial_url, official_number)

        proposed_list = [
            {
                "id": str(doc['id']),
                "document_draft_id": str(doc['document_draft_id']),
                "reference": doc['reference'],
                "status": doc['status'],
                "document_number": doc.get('document_number'),
                "document_type_name": doc.get('document_type_name'),
                "document_type_acronym": doc.get('document_type_acronym'),
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


async def link_official_document(
    case_id: str,
    official_document_id: str,
    linking_user_id: str,
    user_sector_id: str,
    *,
    schema_name: str,
    reason_override: str | None = None,
    auth_source: str = "jwt"
) -> Dict[str, Any]:
    """
    Vincular documento oficial a expediente.
    """
    from services.case_service import CaseService

    try:
        # 1. Verificar que el expediente existe
        case_result = await fetch_all(
            "SELECT c.id, c.case_number FROM cases c WHERE c.id = $1",
            case_id,
            schema_name=schema_name
        )

        if not case_result:
            raise NotFoundError(f"Expediente no encontrado: {case_id}")

        case_number = case_result[0]['case_number']

        # 2. Verificar permisos del usuario
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

        # 3. Verificar que el documento oficial existe y está firmado
        doc_result = await fetch_all(
            "SELECT id, official_number, reference FROM official_documents WHERE id = $1 AND signed_at IS NOT NULL",
            official_document_id,
            schema_name=schema_name
        )

        if not doc_result:
            raise NotFoundError(f"Documento oficial no encontrado: {official_document_id}")

        official_number = doc_result[0]['official_number']
        doc_reference = doc_result[0]['reference']

        # 4. Verificar que no esté duplicado
        duplicate_result = await fetch_all(
            "SELECT 1 FROM case_official_documents WHERE case_id = $1 AND official_document_id = $2 AND is_active = true",
            case_id, official_document_id,
            schema_name=schema_name
        )

        if duplicate_result:
            raise ValidationError(f"El documento {official_number} ya está vinculado a este expediente")

        # 5. Transacción atómica
        try:
            async with transaction(schema_name=schema_name, user_id=linking_user_id, auth_source=auth_source) as conn:
                # Bloquear la fila del expediente para evitar race conditions
                await conn.execute("SELECT 1 FROM cases WHERE id = $1 FOR UPDATE", case_id)

                # Calcular siguiente order_number
                max_order_row = await conn.fetchrow(
                    "SELECT COALESCE(MAX(order_number), 0) as max_order FROM case_official_documents WHERE case_id = $1",
                    case_id
                )
                next_order = (max_order_row['max_order'] + 1) if max_order_row else 1

                # Insertar en case_official_documents
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

                # Obtener admin_sector_id del expediente para el movimiento
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

                # Registrar acción en historial
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

        except (AuthorizationError, NotFoundError, ValidationError):
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

    except (AuthorizationError, NotFoundError, ValidationError, DatabaseError):
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
    """
    Aceptar documento propuesto: vincular el documento oficial al expediente y desactivar la propuesta.
    """
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

    except (AuthorizationError, NotFoundError, ValidationError, DatabaseError):
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
    """
    Rechazar documento propuesto: desactivar la propuesta sin vincular.
    """
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

        # Registrar en historial del expediente
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
    proposing_user_id: str,
    *,
    schema_name: str,
) -> Dict[str, Any]:
    """
    Proponer un documento borrador para vincularlo a un expediente.

    Inserta un registro en case_proposed_documents. El documento queda como
    propuesta activa hasta que un responsable del expediente la acepte o rechace.

    Args:
        case_id: UUID del expediente
        document_draft_id: UUID del documento borrador a proponer
        proposing_user_id: UUID del usuario que propone
        schema_name: Schema de la municipalidad (keyword-only)

    Returns:
        Dict con case_id, document_draft_id y mensaje de confirmación

    Raises:
        ValidationError: Si faltan parámetros requeridos
        NotFoundError: Si el borrador no existe en el tenant
        BusinessLogicError: Si hay error de BD
    """
    if not case_id:
        raise ValidationError("case_id es requerido")
    if not document_draft_id:
        raise ValidationError("document_draft_id es requerido")
    if not proposing_user_id:
        raise ValidationError("proposing_user_id es requerido")

    try:
        logger.info(
            f"Proposing document {document_draft_id} to case {case_id} "
            f"by user {proposing_user_id}"
        )

        # Validar que el borrador existe en el tenant antes del INSERT para
        # evitar una violación de FK que llegaría al cliente como 500.
        from database import check_document_exists
        if not await check_document_exists(document_draft_id, schema_name=schema_name):
            raise NotFoundError(
                f"Documento borrador no encontrado: {document_draft_id}"
            )

        await execute(
            """
            INSERT INTO case_proposed_documents
                (case_id, document_draft_id, proposing_user_id, proposing_date, is_active)
            VALUES ($1, $2, $3, NOW(), true)
            """,
            case_id, document_draft_id, proposing_user_id,
            schema_name=schema_name,
        )

        logger.info(f"Document {document_draft_id} proposed to case {case_id}")

        return {
            "case_id": case_id,
            "document_draft_id": document_draft_id,
            "message": "Documento propuesto para vincular al expediente",
        }

    except (ValidationError, NotFoundError):
        raise
    except Exception as e:
        logger.error(f"Error proposing document to case: {str(e)}")
        raise BusinessLogicError(f"Error proponiendo documento: {str(e)}")
