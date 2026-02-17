"""
Módulo de documentos para expedientes.
Funciones para gestionar documentos dentro de expedientes.
"""

from typing import List, Dict, Any
from shared.logging import get_logger
import uuid

from database import execute_query, execute_update, get_db_connection
from shared.exceptions import (
    NotFoundError,
    AuthorizationError,
    ValidationError,
    DatabaseError,
    BusinessLogicError
)

logger = get_logger(__name__)


def get_case_documents(case_id: str, *, schema_name: str) -> Dict[str, Any]:
    """
    Obtener documentos vinculados al expediente (oficiales y propuestos).

    Args:
        case_id: ID del expediente
        schema_name: Nombre del schema (opcional, para multi-tenant)

    Returns:
        Dict con listas de documentos oficiales y propuestos, más URLs de PDFs
    """
    from services.case_queries import get_official_documents_query, get_proposed_documents_query
    from config.constants import DOCUMENTS_ERROR

    try:
        logger.info(f"Fetching documents for case: {case_id}")

        # Obtener documentos oficiales
        official_docs = execute_query(get_official_documents_query(), (case_id,), schema_name=schema_name)

        # Obtener documentos propuestos
        proposed_docs = execute_query(get_proposed_documents_query(), (case_id,), schema_name=schema_name)

        # Formatear documentos oficiales con conversión de fechas
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
                "pdf_url": None  # Se llenará después
            })

        # Obtener URLs de PDFs para documentos oficiales
        if official_list:
            from services.storage.cloudflare import get_tenant_r2_client
            r2_client = get_tenant_r2_client(schema_name=schema_name)
            for doc in official_list:
                official_number = doc.get("official_number")
                if official_number:
                    doc["pdf_url"] = r2_client.get_oficial_url(official_number)

        # Formatear documentos propuestos con conversión de fechas
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
                "proposed_by": doc['proposed_by']
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


def link_official_document(
    case_id: str,
    official_document_id: str,
    linking_user_id: str,
    user_sector_id: str,
    *,
    schema_name: str,
    reason_override: str | None = None
) -> Dict[str, Any]:
    """
    Vincular documento oficial a expediente

    Args:
        case_id: UUID del expediente
        official_document_id: UUID del documento oficial
        linking_user_id: UUID del usuario que vincula
        user_sector_id: UUID del sector del usuario
        schema_name: Nombre del schema (opcional, para multi-tenant)

    Returns:
        Dict con información del documento vinculado

    Raises:
        Exception: Si hay algún error en la vinculación
    """
    # Import CaseService dinámicamente para evitar circular import
    from services.case_service import CaseService

    try:
        # 1. Verificar que el expediente existe
        case_query = """
            SELECT c.id, c.case_number
            FROM cases c
            WHERE c.id = %s
        """
        case_result = execute_query(case_query, (case_id,), schema_name=schema_name)

        if not case_result:
            raise NotFoundError(f"Expediente no encontrado: {case_id}")

        case_number = case_result[0]['case_number']

        # 2. Verificar permisos del usuario (solo sectores donde puede EDITAR)
        # Usa helper que respeta can_edit de user_sector_permissions
        user_sector_ids = CaseService.get_user_editable_sector_ids(linking_user_id, schema_name=schema_name)
        logger.debug(f"User {linking_user_id} editable sectors for linking: {user_sector_ids}")

        if not user_sector_ids:
            raise AuthorizationError("Usuario sin permisos de edicion en ningun sector")

        # Verificar permisos: ADMIN o ASIGNADO
        sector_placeholders = ",".join(["%s"] * len(user_sector_ids))

        permission_check = f"""
            SELECT 1
            FROM cases c
            WHERE c.id = %s
            AND (
                -- Condición 1: ASIGNADO (asignación activa)
                EXISTS (
                    SELECT 1 FROM case_movements cm
                    WHERE cm.case_id = c.id
                    AND cm.assigned_sector_id IN ({sector_placeholders})
                    AND cm.is_active = true
                )
                OR
                -- Condición 2: ADMIN (última transferencia cerrada)
                EXISTS (
                    SELECT 1 FROM case_movements cm
                    WHERE cm.case_id = c.id
                    AND cm.type = 'transfer'
                    AND cm.is_active = false
                    AND cm.admin_sector_id IN ({sector_placeholders})
                    AND cm.closed_at = (
                        SELECT MAX(cm2.closed_at)
                        FROM case_movements cm2
                        WHERE cm2.case_id = c.id
                        AND cm2.type = 'transfer'
                        AND cm2.is_active = false
                    )
                )
                OR
                -- Condición 3: ADMIN (creador, solo si no hay transfers)
                (
                    EXISTS (
                        SELECT 1 FROM case_movements cm
                        WHERE cm.case_id = c.id
                        AND cm.type = 'creation'
                        AND cm.admin_sector_id IN ({sector_placeholders})
                    )
                    AND NOT EXISTS (
                        SELECT 1 FROM case_movements cm
                        WHERE cm.case_id = c.id
                        AND cm.type = 'transfer'
                    )
                )
            )
        """

        permission_params = [case_id] + (user_sector_ids * 3)
        permission_result = execute_query(permission_check, tuple(permission_params), schema_name=schema_name)

        if not permission_result:
            raise AuthorizationError("No tiene permisos para vincular documentos a este expediente")

        # 3. Verificar que el documento oficial existe
        doc_query = """
            SELECT id, official_number, reference
            FROM official_documents
            WHERE id = %s
        """
        doc_result = execute_query(doc_query, (official_document_id,), schema_name=schema_name)

        if not doc_result:
            raise NotFoundError(f"Documento oficial no encontrado: {official_document_id}")

        official_number = doc_result[0]['official_number']
        doc_reference = doc_result[0]['reference']

        # 4. Verificar que no esté duplicado (mismo documento en mismo expediente)
        duplicate_check = """
            SELECT 1
            FROM case_official_documents
            WHERE case_id = %s
            AND official_document_id = %s
            AND is_active = true
        """
        duplicate_result = execute_query(duplicate_check, (case_id, official_document_id), schema_name=schema_name)

        if duplicate_result:
            raise ValidationError(f"El documento {official_number} ya está vinculado a este expediente")

        # 5. Usar transacción con SELECT FOR UPDATE para obtener el siguiente order_number
        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                try:
                    # Bloquear la fila del expediente para evitar race conditions
                    cursor.execute("""
                        SELECT 1 FROM cases WHERE id = %s FOR UPDATE
                    """, (case_id,))

                    # Ahora calcular el máximo (el expediente está bloqueado)
                    cursor.execute("""
                        SELECT COALESCE(MAX(order_number), 0) as max_order
                        FROM case_official_documents
                        WHERE case_id = %s
                    """, (case_id,))

                    max_order_result = cursor.fetchone()
                    next_order = max_order_result['max_order'] + 1 if max_order_result else 1

                    # 6. Insertar en case_official_documents con RETURNING
                    link_id = str(uuid.uuid4())

                    cursor.execute("""
                        INSERT INTO case_official_documents (
                            id, case_id, official_document_id,
                            linking_user_id, order_number,
                            linking_date, is_active
                        ) VALUES (
                            %s, %s, %s, %s, %s, NOW(), true
                        )
                        RETURNING linking_date
                    """, (link_id, case_id, official_document_id, linking_user_id, next_order))

                    linking_result = cursor.fetchone()
                    linking_date = linking_result['linking_date']

                    # Obtener admin_sector_id del expediente para el movimiento
                    cursor.execute("""
                        SELECT s.id as admin_sector_id
                        FROM case_movements cm
                        JOIN sectors s ON cm.admin_sector_id = s.id
                        WHERE cm.case_id = %s
                          AND cm.is_active = false
                          AND cm.type IN ('creation', 'transfer')
                        ORDER BY cm.closed_at DESC
                        LIMIT 1
                    """, (case_id,))
                    admin_result = cursor.fetchone()
                    admin_sector_id = admin_result['admin_sector_id'] if admin_result else user_sector_id

                    # Registrar acción en historial (cerrado, solo registro)
                    movement_id = str(uuid.uuid4())
                    cursor.execute("""
                        INSERT INTO case_movements (
                            id, case_id, type, user_id,
                            creator_sector_id, admin_sector_id,
                            supporting_document_id,
                            reason, is_active, closed_at, closing_reason
                        ) VALUES (
                            %s, %s, 'document_link', %s, %s, %s, %s, %s, false, NOW(), 'Acción completada'
                        )
                    """, (movement_id, case_id, linking_user_id, user_sector_id, admin_sector_id,
                          official_document_id, reason_override or f"Vinculó documento: {official_number} ({doc_reference})"))

                    # Commit de la transacción
                    conn.commit()

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

                except Exception as e:
                    conn.rollback()
                    raise DatabaseError(f"Error en transacción de vinculación: {str(e)}")

    except (AuthorizationError, NotFoundError, ValidationError, DatabaseError):
        # Re-lanzar excepciones custom sin modificar
        raise
    except Exception as e:
        raise BusinessLogicError(f"Error vinculando documento: {str(e)}")


def accept_proposed_document(
    case_id: str,
    proposed_id: str,
    user_id: str,
    user_sector_id: str,
    *,
    schema_name: str
) -> Dict[str, Any]:
    """
    Aceptar documento propuesto: vincular el documento oficial al expediente y desactivar la propuesta.

    Args:
        case_id: UUID del expediente
        proposed_id: UUID de la propuesta
        user_id: UUID del usuario que acepta
        user_sector_id: UUID del sector del usuario
        schema_name: Nombre del schema (multi-tenant)

    Returns:
        Dict con información del documento vinculado
    """
    from services.case_queries import get_proposed_document_by_id_query, deactivate_proposed_document_query
    from config.constants import (
        PROPOSED_DOCUMENT_NOT_FOUND,
        PROPOSED_DOCUMENT_ALREADY_PROCESSED,
        PROPOSED_DOCUMENT_NOT_SIGNED,
    )

    try:
        # 1. Obtener la propuesta
        proposed = execute_query(
            get_proposed_document_by_id_query(), (proposed_id, case_id), schema_name=schema_name
        )

        if not proposed:
            raise NotFoundError(PROPOSED_DOCUMENT_NOT_FOUND)

        proposed_doc = proposed[0]

        # 2. Validar que está activa
        if not proposed_doc['is_active']:
            raise ValidationError(PROPOSED_DOCUMENT_ALREADY_PROCESSED)

        # 3. Validar que el documento está firmado (oficial)
        if proposed_doc['status'] != 'signed':
            raise ValidationError(
                PROPOSED_DOCUMENT_NOT_SIGNED.format(status=proposed_doc['status'])
            )

        # 4. Construir mensaje diferenciado para aceptación de propuesta
        reference = proposed_doc.get('reference', '')
        doc_number = proposed_doc.get('document_number', '')
        reason_msg = f"Aceptó la incorporación de {reference}"
        if doc_number:
            reason_msg += f" ({doc_number})"

        # 5. Vincular usando la función existente (maneja permisos, duplicados, transacción e historial)
        link_result = link_official_document(
            case_id=case_id,
            official_document_id=str(proposed_doc['document_draft_id']),
            linking_user_id=user_id,
            user_sector_id=user_sector_id,
            schema_name=schema_name,
            reason_override=reason_msg
        )

        # 6. Desactivar la propuesta
        execute_update(
            deactivate_proposed_document_query(), (proposed_id,), schema_name=schema_name
        )

        logger.info(f"Proposed document {proposed_id} accepted and linked to case {case_id}")

        return link_result

    except (AuthorizationError, NotFoundError, ValidationError, DatabaseError):
        raise
    except Exception as e:
        logger.error(f"Error accepting proposed document: {str(e)}")
        raise BusinessLogicError(f"Error aceptando documento propuesto: {str(e)}")


def reject_proposed_document(
    case_id: str,
    proposed_id: str,
    user_id: str,
    user_sector_id: str = None,
    *,
    schema_name: str
) -> Dict[str, Any]:
    """
    Rechazar documento propuesto: desactivar la propuesta sin vincular.

    Args:
        case_id: UUID del expediente
        proposed_id: UUID de la propuesta
        user_id: UUID del usuario que rechaza
        user_sector_id: UUID del sector del usuario (para historial)
        schema_name: Nombre del schema (multi-tenant)

    Returns:
        Dict con información de la propuesta rechazada
    """
    from services.case_queries import get_proposed_document_by_id_query, deactivate_proposed_document_query
    from services.case_service import CaseService
    from config.constants import (
        PROPOSED_DOCUMENT_NOT_FOUND,
        PROPOSED_DOCUMENT_ALREADY_PROCESSED,
        PROPOSED_DOCUMENT_REJECT_NO_PERMISSION,
    )

    try:
        # 1. Obtener la propuesta
        proposed = execute_query(
            get_proposed_document_by_id_query(), (proposed_id, case_id), schema_name=schema_name
        )

        if not proposed:
            raise NotFoundError(PROPOSED_DOCUMENT_NOT_FOUND)

        proposed_doc = proposed[0]

        # 2. Validar que está activa
        if not proposed_doc['is_active']:
            raise ValidationError(PROPOSED_DOCUMENT_ALREADY_PROCESSED)

        # 3. Verificar permisos (mismo patrón que link_official_document)
        user_sector_ids = CaseService.get_user_editable_sector_ids(user_id, schema_name=schema_name)

        if not user_sector_ids:
            raise AuthorizationError(PROPOSED_DOCUMENT_REJECT_NO_PERMISSION)

        sector_placeholders = ",".join(["%s"] * len(user_sector_ids))

        permission_check = f"""
            SELECT 1
            FROM cases c
            WHERE c.id = %s
            AND (
                EXISTS (
                    SELECT 1 FROM case_movements cm
                    WHERE cm.case_id = c.id
                    AND cm.assigned_sector_id IN ({sector_placeholders})
                    AND cm.is_active = true
                )
                OR
                EXISTS (
                    SELECT 1 FROM case_movements cm
                    WHERE cm.case_id = c.id
                    AND cm.type = 'transfer'
                    AND cm.is_active = false
                    AND cm.admin_sector_id IN ({sector_placeholders})
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
                        AND cm.admin_sector_id IN ({sector_placeholders})
                    )
                    AND NOT EXISTS (
                        SELECT 1 FROM case_movements cm
                        WHERE cm.case_id = c.id
                        AND cm.type = 'transfer'
                    )
                )
            )
        """

        permission_params = [case_id] + (user_sector_ids * 3)
        permission_result = execute_query(permission_check, tuple(permission_params), schema_name=schema_name)

        if not permission_result:
            raise AuthorizationError(PROPOSED_DOCUMENT_REJECT_NO_PERMISSION)

        # 4. Desactivar la propuesta
        execute_update(
            deactivate_proposed_document_query(), (proposed_id,), schema_name=schema_name
        )

        # 5. Registrar en historial del expediente
        try:
            from services.cases.history import create_movement
            from config.constants import MOVEMENT_TYPE_DOCUMENT_PROPOSAL_REJECT

            # Obtener admin_sector_id del último movimiento
            admin_result = execute_query(
                """SELECT admin_sector_id FROM case_movements
                   WHERE case_id = %s AND type IN ('creation', 'transfer')
                   ORDER BY created_at DESC LIMIT 1""",
                (case_id,), schema_name=schema_name
            )
            admin_sector_id = str(admin_result[0]['admin_sector_id']) if admin_result else user_sector_id

            # Construir mensaje
            reference = proposed_doc.get('reference', 'Sin referencia')
            doc_number = proposed_doc.get('document_number')
            reason = f"Rechazó la incorporación de {reference}"
            if doc_number:
                reason += f" ({doc_number})"

            # Determinar sector del usuario si no se proporcionó
            effective_sector_id = user_sector_id
            if not effective_sector_id:
                user_sector_result = execute_query(
                    "SELECT sector_id FROM users WHERE id = %s",
                    (user_id,), schema_name=schema_name
                )
                effective_sector_id = str(user_sector_result[0]['sector_id']) if user_sector_result else admin_sector_id

            create_movement(
                case_id=case_id,
                movement_type=MOVEMENT_TYPE_DOCUMENT_PROPOSAL_REJECT,
                user_id=user_id,
                creator_sector_id=effective_sector_id,
                admin_sector_id=admin_sector_id,
                reason=reason,
                supporting_document_id=str(proposed_doc['document_draft_id']) if proposed_doc.get('status') == 'signed' else None,
                schema_name=schema_name
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
