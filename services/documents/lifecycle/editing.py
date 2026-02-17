"""
Servicio de edicion de documentos - REFACTORIZADO
Aplica principios Clean Code y SOLID para mantener codigo legible y mantenible.

Ubicacion real: services/documents/lifecycle/editing.py
"""

import json
import uuid
from typing import Dict, Any, List, Optional, Tuple
from shared.logging import get_logger
from database import get_db_connection, get_db_cursor, execute_transaction
from shared.exceptions import DocumentNotFoundError, ValidationError, DocumentStateError
from shared.validation import validate_document_id, validate_required_string, validate_document_signers
from config.constants import EDITABLE_DOCUMENT_STATES, SAVE_SUCCESS_MESSAGE, SAVE_NO_CHANGES_ERROR
from ..core.queries import (
    get_document_details_for_editing_query,
    get_document_signers_query,
    get_document_rejection_info_query,
    get_document_status_query,
    delete_document_signers_query,
    insert_document_signer_ordered_query,
    update_document_reference_query,
    update_document_content_query,
    update_document_reference_and_content_query,
    get_proposed_cases_for_document_query,
    delete_proposed_cases_for_document_query,
    insert_proposed_case_query,
    validate_case_exists_query
)

logger = get_logger(__name__)


def _validate_document_can_be_edited(document_id: str, *, schema_name: str) -> None:
    """Valida que un documento pueda ser editado."""
    validation_error = validate_document_id(document_id, schema_name=schema_name)
    if validation_error:
        raise ValidationError(validation_error)

    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            cursor.execute(get_document_status_query(), (document_id,))
            document = cursor.fetchone()

            if not document:
                raise DocumentNotFoundError(document_id)

            if document['status'] not in EDITABLE_DOCUMENT_STATES:
                raise DocumentStateError(
                    f"Documento en estado '{document['status']}' no puede editarse",
                    current_state=document['status'],
                    required_state=" o ".join(EDITABLE_DOCUMENT_STATES)
                )


def _validate_document_update_data(reference: Optional[str], content: Optional[str], signers: Optional[List], *, schema_name: str) -> None:
    """Valida los datos de actualizacion del documento."""
    if reference is not None:
        ref_error = validate_required_string(reference, "reference", min_length=1, max_length=250)
        if ref_error:
            raise ValidationError(ref_error)

    # Solo validar content si tiene valor (string vacio = documento importado sin HTML)
    if content:
        content_error = validate_required_string(content, "content", min_length=1)
        if content_error:
            raise ValidationError(content_error)

    if signers is not None:
        signers_error = validate_document_signers(signers, schema_name=schema_name)
        if signers_error:
            raise ValidationError(signers_error)


def _fetch_document_basic_details(document_id: str, *, schema_name: str) -> Dict[str, Any]:
    """Obtiene los datos basicos del documento desde la base de datos."""
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            cursor.execute(get_document_details_for_editing_query(), (document_id,))
            document = cursor.fetchone()

            if not document:
                raise DocumentNotFoundError(document_id)

            return document


def _fetch_document_signers(document_id: str, *, schema_name: str) -> List[Dict[str, Any]]:
    """Obtiene la lista de firmantes del documento."""
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            cursor.execute(get_document_signers_query(), (document_id,))
            signers_data = cursor.fetchall()

            result = []
            for signer in signers_data:
                dept_acronym = signer.get('department_acronym')
                sector_acronym = signer.get('sector_acronym')
                department_sector = None
                if dept_acronym or sector_acronym:
                    department_sector = f"{dept_acronym or ''}#{sector_acronym or ''}"

                result.append({
                    "user_id": signer['user_id'],
                    "user_name": signer['user_name'] or "",
                    "email": signer['email'],
                    "signing_order": signer['signing_order'],
                    "is_numerator": signer['is_numerator'],
                    "profile_picture_url": signer['profile_picture_url'],
                    "department_sector": department_sector
                })

            return result


def _fetch_document_rejection_info(document_id: str, *, schema_name: str) -> Optional[Dict[str, Any]]:
    """Obtiene informacion del ultimo rechazo del documento (si existe)."""
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            cursor.execute(get_document_rejection_info_query(), (document_id,))
            rejection = cursor.fetchone()

            if rejection:
                return {
                    "reason": rejection['reason'],
                    "rejected_at": rejection['created_at'].isoformat() if rejection['created_at'] else None,
                    "rejected_by": str(rejection['rejected_by']),
                    "rejected_by_name": rejection['rejected_by_name']
                }

            return None


def _extract_html_content_from_document_json(content_json: Optional[Dict]) -> str:
    """Extrae el contenido HTML del JSON (soporta formatos 'html' y 'detalle')."""
    if not content_json:
        return ""

    return content_json.get('html') or content_json.get('detalle', '')


def _fetch_document_recipients(document_id: str, *, schema_name: str) -> Optional[Dict[str, List]]:
    """Obtiene los recipients de un documento NOTA para edición.

    El creador siempre ve todos los recipients (TO, CC, BCC) en modo edición.
    """
    from services.notes.queries import get_recipients_by_document_query

    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            cursor.execute(get_recipients_by_document_query(), (document_id,))
            all_recipients = cursor.fetchall()

            if not all_recipients:
                return None

            result = {'to': [], 'cc': [], 'bcc': []}

            for r in all_recipients:
                recipient_data = {
                    'sector_id': str(r['sector_id']),
                    'acronym': r['sector_acronym'],
                    'department_name': r['department_name']
                }

                if r['recipient_type'] == 'TO':
                    result['to'].append(recipient_data)
                elif r['recipient_type'] == 'CC':
                    result['cc'].append(recipient_data)
                elif r['recipient_type'] == 'BCC':
                    result['bcc'].append(recipient_data)

            return result


def _fetch_proposed_cases(document_id: str, *, schema_name: str) -> List[Dict]:
    """Obtiene los expedientes propuestos para un documento.

    Args:
        document_id: UUID del documento
        schema_name: Schema del tenant

    Returns:
        Lista de diccionarios con case_id, case_number, reference, proposing_date
    """
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            cursor.execute(get_proposed_cases_for_document_query(), (document_id,))
            proposed_cases = cursor.fetchall()

            result = []
            for case in proposed_cases:
                result.append({
                    "case_id": str(case['case_id']),
                    "case_number": case['case_number'],
                    "reference": case.get('reference'),
                    "proposing_date": case['proposing_date'].isoformat() if case.get('proposing_date') else None
                })

            return result


def _validate_case_ids(case_ids: List[str], *, schema_name: str) -> None:
    """Valida que todos los expedientes existan y no estén archivados.

    Args:
        case_ids: Lista de UUIDs de expedientes
        schema_name: Schema del tenant

    Raises:
        ValidationError: Si algún expediente no existe o está archivado
    """
    if not case_ids:
        return

    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            for case_id in case_ids:
                cursor.execute(validate_case_exists_query(), (case_id,))
                result = cursor.fetchone()

                if not result:
                    raise ValidationError(f"El expediente '{case_id}' no existe o está archivado")


def _build_proposed_cases_update_operations(
    document_id: str,
    proposed_case_ids: List[str],
    user_id: str
) -> List[Tuple[str, Tuple]]:
    """Construye operaciones para actualizar expedientes propuestos.

    Implementa deduplicación para prevenir que un documento esté propuesto
    múltiples veces al mismo expediente.

    Args:
        document_id: UUID del documento
        proposed_case_ids: Lista de UUIDs de expedientes (puede tener duplicados)
        user_id: UUID del usuario que propone

    Returns:
        Lista de tuplas (query, params) para execute_transaction
    """
    operations = []

    # 1. DELETE existentes
    operations.append((
        delete_proposed_cases_for_document_query(),
        (document_id,)
    ))

    # 2. Deduplicar case_ids (CRÍTICO para prevenir propuestas duplicadas)
    unique_case_ids = list(set(proposed_case_ids))

    if unique_case_ids:
        logger.info(f"Deduplicados {len(proposed_case_ids)} -> {len(unique_case_ids)} expedientes únicos")

    # 3. INSERT nuevos (uno por cada expediente único)
    for case_id in unique_case_ids:
        new_id = str(uuid.uuid4())
        operations.append((
            insert_proposed_case_query(),
            (new_id, case_id, document_id, user_id)
        ))

    return operations


def _build_document_type_info(document: Dict[str, Any]) -> Dict[str, str]:
    """Construye la informacion del tipo de documento."""
    doc_type_source = document.get('document_type_source')
    logger.debug(f"document_type_source from DB: {doc_type_source}")
    return {
        "name": document['document_type_name'] or "Sin tipo",
        "acronym": document['document_type_acronym'] or "",
        "type": doc_type_source or "HTML"
    }


def _build_department_sector(dept_acronym: Optional[str], sector_acronym: Optional[str]) -> Optional[str]:
    """Construye el string department_sector."""
    if dept_acronym or sector_acronym:
        return f"{dept_acronym or ''}#{sector_acronym or ''}"
    return None


def _build_complete_document_response(
    document: Dict[str, Any],
    signers: List[Dict[str, Any]],
    rejection_info: Optional[Dict[str, Any]],
    pdf_url: Optional[str] = None,
    recipients: Optional[Dict[str, List]] = None,
    proposed_cases: Optional[List[Dict]] = None
) -> Dict[str, Any]:
    """Construye la respuesta completa con todos los datos del documento."""
    department_sector = _build_department_sector(
        document.get('creator_department_acronym'),
        document.get('creator_sector_acronym')
    )

    is_imported = document.get('document_type_source') == 'Importado'

    response = {
        "document_id": document['id'],
        "reference": document['reference'],
        "content": _extract_html_content_from_document_json(document['content']),
        "status": document['status'],
        "document_type": _build_document_type_info(document),
        "created_by": document['creator_id'],
        "creator_name": document['creator_name'],
        "creator_profile_picture_url": document.get('creator_profile_picture_url'),
        "creator_department_sector": department_sector,
        "signers": signers,
        "rejection_info": rejection_info,
        "created_at": None,
        "updated_at": document['last_modified_at'].isoformat() if document['last_modified_at'] else None,
        "is_imported": is_imported,
        "pdf_url": pdf_url,
        "resume": document.get('resume')
    }

    # Incluir recipients solo si es tipo NOTA
    if recipients is not None:
        response["recipients"] = recipients

    # Incluir proposed_cases si existen
    if proposed_cases is not None:
        response["proposed_cases"] = proposed_cases

    return response


def _build_document_update_operations(
    document_id: str,
    reference: Optional[str],
    content: Optional[str]
) -> List[tuple]:
    """Construye operaciones para actualizar el documento."""
    # Tratar string vacio como None (documento importado sin contenido HTML)
    effective_content = content if content else None

    if reference is None and effective_content is None:
        return []

    # Seleccionar query apropiada segun que campos se actualizan
    if reference is not None and effective_content is not None:
        content_json = json.dumps({"html": effective_content})
        return [(update_document_reference_and_content_query(), [reference, content_json, document_id])]
    elif reference is not None:
        return [(update_document_reference_query(), [reference, document_id])]
    else:  # effective_content is not None
        content_json = json.dumps({"html": effective_content})
        return [(update_document_content_query(), [content_json, document_id])]


def _build_signers_update_operations(
    document_id: str,
    signers: List[Dict]
) -> List[tuple]:
    """Construye operaciones para actualizar firmantes."""
    operations = []

    operations.append((
        delete_document_signers_query(),
        [document_id]
    ))

    for order, signer in enumerate(signers, 1):
        operations.append((
            insert_document_signer_ordered_query(),
            [document_id, signer.get('user_id'), order, signer.get('is_numerator', False)]
        ))

    return operations


def _process_recipients_update(
    cursor,
    document_id: str,
    recipients: Dict,
    sender_sector_id: Optional[str],
    *,
    schema_name: str
) -> None:
    """
    Procesa la actualización de recipients para documentos NOTA.

    Solo procesa si el documento es tipo NOTA. Para otros tipos, ignora silenciosamente.

    Args:
        cursor: Cursor de la transacción padre
        document_id: UUID del documento
        recipients: Dict con {to: [], cc: [], bcc: []}
        sender_sector_id: UUID del sector emisor
        schema_name: Schema del tenant
    """
    from services.notes.validation import (
        is_nota_document_type_by_id,
        validate_recipients_input,
        validate_recipients_exist
    )
    from services.notes.save_recipients import save_recipients, delete_recipients

    # Verificar que es NOTA
    if not is_nota_document_type_by_id(document_id, cursor, schema_name=schema_name):
        logger.debug(f"Documento {document_id} no es NOTA, ignorando recipients")
        return

    # Validar y normalizar formato (incluye deduplicación)
    normalized = validate_recipients_input(recipients)

    # Validar que sectores existan (si hay recipients)
    has_recipients = normalized.get('to') or normalized.get('cc') or normalized.get('bcc')
    if has_recipients:
        if not sender_sector_id:
            raise ValidationError("Se requiere sender_sector_id para guardar recipients en NOTA")
        validate_recipients_exist(cursor, normalized, sender_sector_id, schema_name=schema_name)

    # Borrar existentes y guardar nuevos
    deleted_count = delete_recipients(cursor, document_id)
    if has_recipients:
        saved_count = save_recipients(cursor, document_id, sender_sector_id, normalized, schema_name=schema_name)
        logger.info(f"Recipients actualizados: {deleted_count} eliminados, {saved_count} guardados")
    else:
        logger.info(f"Recipients eliminados: {deleted_count} (lista vacía)")


def get_document_details_for_editing(document_id: str, *, schema_name: str) -> Dict[str, Any]:
    """Obtiene los detalles completos de un documento para edicion."""
    logger.info(f"Obteniendo detalles de documento {document_id} para edicion")

    _validate_document_can_be_edited(document_id, schema_name=schema_name)

    document = _fetch_document_basic_details(document_id, schema_name=schema_name)
    signers = _fetch_document_signers(document_id, schema_name=schema_name)

    rejection_info = None
    if document['status'] == 'rejected':
        rejection_info = _fetch_document_rejection_info(document_id, schema_name=schema_name)

    # Obtener pdf_url si es documento importado Y el PDF existe
    pdf_url = None
    if document.get('document_type_source') == 'Importado':
        try:
            from services.storage.cloudflare import get_tenant_r2_client
            r2_client = get_tenant_r2_client(schema_name=schema_name)
            document_id_no_hyphens = document_id.replace('-', '')
            r2_filename = f"{document_id_no_hyphens}.pdf"
            # Verificar si el PDF existe antes de generar URL
            if r2_client.exists_tosign(r2_filename):
                pdf_url = r2_client.get_tosign_url(r2_filename)
                logger.debug(f"PDF URL generada para documento importado: {pdf_url[:50]}...")
            else:
                logger.debug(f"PDF no existe aun para documento importado {document_id}")
        except Exception as e:
            logger.warning(f"No se pudo obtener PDF URL para documento {document_id}: {e}")

    # Obtener recipients si es tipo NOTA
    recipients = None
    if document.get('document_type_source') == 'NOTA':
        logger.info(f"Documento {document_id} es NOTA, obteniendo recipients...")
        recipients = _fetch_document_recipients(document_id, schema_name=schema_name)
        if recipients:
            logger.info(f"Recipients cargados: TO={len(recipients.get('to', []))}, CC={len(recipients.get('cc', []))}, BCC={len(recipients.get('bcc', []))}")
        else:
            logger.info(f"No se encontraron recipients para documento {document_id}")

    # Obtener expedientes propuestos
    proposed_cases = _fetch_proposed_cases(document_id, schema_name=schema_name)
    if proposed_cases:
        logger.info(f"Expedientes propuestos cargados: {len(proposed_cases)} expedientes")

    return _build_complete_document_response(document, signers, rejection_info, pdf_url, recipients, proposed_cases)




def save_document_changes(
    document_id: str,
    reference: Optional[str] = None,
    content: Optional[str] = None,
    signers: Optional[List[Dict]] = None,
    recipients: Optional[Dict] = None,
    sender_sector_id: Optional[str] = None,
    proposed_case_ids: Optional[List[str]] = None,
    user_id: Optional[str] = None,
    *,
    schema_name: str
) -> Dict[str, Any]:
    """Guarda cambios en un documento existente.

    Args:
        document_id: UUID del documento
        reference: Nueva referencia (opcional)
        content: Nuevo contenido HTML (opcional)
        signers: Lista de firmantes (opcional)
        recipients: Destinatarios para NOTA {to: [], cc: [], bcc: []} (opcional)
        sender_sector_id: UUID del sector emisor para NOTA (opcional)
        proposed_case_ids: Lista de UUIDs de expedientes propuestos (opcional)
        user_id: UUID del usuario que propone (requerido si hay proposed_case_ids)
        schema_name: Schema del tenant

    Returns:
        Dict con success, message, document_id y last_modified_at
    """
    logger.info(f"Guardando cambios en documento {document_id} en schema {schema_name}")

    _validate_document_can_be_edited(document_id, schema_name=schema_name)
    _validate_document_update_data(reference, content, signers, schema_name=schema_name)

    # Tratar string vacio como None para la validacion de cambios
    effective_content = content if content else None
    if reference is None and effective_content is None and signers is None and recipients is None and proposed_case_ids is None:
        raise ValidationError(SAVE_NO_CHANGES_ERROR)

    # Validar expedientes propuestos si existen
    if proposed_case_ids is not None:
        if not user_id:
            raise ValidationError("Se requiere user_id para proponer vinculación a expedientes")
        _validate_case_ids(proposed_case_ids, schema_name=schema_name)

    operations = []
    operations.extend(_build_document_update_operations(document_id, reference, content))

    if signers is not None:
        operations.extend(_build_signers_update_operations(document_id, signers))

    # Detectar propuestas NUEVAS antes de construir operaciones
    new_case_ids = set()
    if proposed_case_ids is not None:
        from database import execute_query
        existing_proposals = execute_query(
            "SELECT case_id::text FROM case_proposed_documents WHERE document_draft_id = %s AND is_active = true",
            (document_id,), schema_name=schema_name
        )
        existing_case_ids = {row['case_id'] for row in existing_proposals}
        new_case_ids = set(proposed_case_ids) - existing_case_ids

        operations.extend(_build_proposed_cases_update_operations(
            document_id, proposed_case_ids, user_id
        ))

    with execute_transaction(schema_name=schema_name) as (conn, cursor):
        for query, params in operations:
            cursor.execute(query, params)

        # Procesar recipients si se enviaron (solo para NOTA)
        if recipients is not None:
            _process_recipients_update(
                cursor, document_id, recipients, sender_sector_id,
                schema_name=schema_name
            )

    # Registrar historial para propuestas NUEVAS (después de la transacción)
    if proposed_case_ids is not None and new_case_ids:
        _register_proposal_history(
            document_id, list(new_case_ids), user_id, sender_sector_id,
            schema_name=schema_name
        )

    updated_document = get_document_details_for_editing(document_id, schema_name=schema_name)

    logger.info(f"Documento {document_id} actualizado exitosamente")

    return {
        "success": True,
        "message": SAVE_SUCCESS_MESSAGE,
        "document_id": document_id,
        "last_modified_at": updated_document.get("updated_at")
    }


def _register_proposal_history(
    document_id: str,
    new_case_ids: List[str],
    user_id: str,
    sender_sector_id: Optional[str],
    *,
    schema_name: str
):
    """Registrar en case_history cuando se propone vincular documento a expedientes nuevos."""
    from services.cases.history import create_movement
    from config.constants import MOVEMENT_TYPE_DOCUMENT_PROPOSAL
    from database import execute_query

    # Obtener referencia y numero del documento
    doc_info = execute_query(
        "SELECT reference, document_number FROM document_draft WHERE id = %s",
        (document_id,), schema_name=schema_name
    )
    reference = doc_info[0]['reference'] if doc_info else 'Sin referencia'
    doc_number = doc_info[0].get('document_number') if doc_info else None

    # Si no tenemos sector del usuario, obtenerlo
    if not sender_sector_id:
        user_result = execute_query(
            "SELECT sector_id FROM users WHERE id = %s",
            (user_id,), schema_name=schema_name
        )
        sender_sector_id = str(user_result[0]['sector_id']) if user_result else None

    # Construir mensaje
    reason = f"Propuso vincular documento: {reference}"
    if doc_number:
        reason += f" ({doc_number})"

    for case_id in new_case_ids:
        try:
            # Obtener admin_sector_id del expediente
            admin_result = execute_query(
                """SELECT admin_sector_id FROM case_movements
                   WHERE case_id = %s AND type IN ('creation', 'transfer')
                   ORDER BY created_at DESC LIMIT 1""",
                (case_id,), schema_name=schema_name
            )
            admin_sector_id = str(admin_result[0]['admin_sector_id']) if admin_result else sender_sector_id

            create_movement(
                case_id=case_id,
                movement_type=MOVEMENT_TYPE_DOCUMENT_PROPOSAL,
                user_id=user_id,
                creator_sector_id=sender_sector_id,
                admin_sector_id=admin_sector_id,
                reason=reason,
                schema_name=schema_name
            )
        except Exception as e:
            logger.warning(f"Error registrando propuesta en historial case {case_id}: {e}")


def check_document_can_be_edited(document_id: str, *, schema_name: str) -> Dict[str, Any]:
    """Verifica si un documento puede ser editado sin lanzar excepciones."""
    try:
        _validate_document_can_be_edited(document_id, schema_name=schema_name)

        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                cursor.execute(get_document_status_query(), (document_id,))
                document = cursor.fetchone()

                return {
                    "can_edit": True,
                    "document_status": document['status'] if document else None
                }

    except ValidationError as e:
        return {
            "can_edit": False,
            "reason": str(e)
        }
    except DocumentNotFoundError:
        return {
            "can_edit": False,
            "reason": f"Documento '{document_id}' no encontrado"
        }
    except DocumentStateError as e:
        return {
            "can_edit": False,
            "reason": str(e),
            "current_status": e.current_state,
            "allowed_statuses": EDITABLE_DOCUMENT_STATES
        }
    except Exception as e:
        return {
            "can_edit": False,
            "reason": f"Error inesperado: {str(e)}"
        }
