"""
Servicio de edicion de documentos - REFACTORIZADO
Aplica principios Clean Code y SOLID para mantener codigo legible y mantenible.

Ubicacion real: services/documents/lifecycle/editing.py
MIGRADO: Fase 6 asyncpg
"""

import uuid
from typing import Dict, Any, List, Optional, Tuple
from fastapi.concurrency import run_in_threadpool
from shared.logging import get_logger
from database import fetch_one, fetch_all, fetch_val, execute, transaction, get_conn
from shared.exceptions import DocumentNotFoundError, ValidationError, DocumentStateError
from shared.validation import validate_document_id, validate_required_string, validate_document_signers, sanitize_html
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


async def _validate_document_can_be_edited(document_id: str, *, schema_name: str) -> None:
    """Valida que un documento pueda ser editado."""
    validation_error = await validate_document_id(document_id, schema_name=schema_name)
    if validation_error:
        raise ValidationError(validation_error)

    document = await fetch_one(get_document_status_query(), document_id, schema_name=schema_name)

    if not document:
        raise DocumentNotFoundError(document_id)

    if document['status'] not in EDITABLE_DOCUMENT_STATES:
        raise DocumentStateError(
            f"Documento en estado '{document['status']}' no puede editarse",
            current_state=document['status'],
            required_state=" o ".join(EDITABLE_DOCUMENT_STATES)
        )


async def _validate_document_update_data(reference: Optional[str], content: Optional[str], signers: Optional[List], *, schema_name: str) -> None:
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
        signers_error = await validate_document_signers(signers, schema_name=schema_name)
        if signers_error:
            raise ValidationError(signers_error)


async def _fetch_document_basic_details(document_id: str, *, schema_name: str) -> Dict[str, Any]:
    """Obtiene los datos basicos del documento desde la base de datos."""
    document = await fetch_one(get_document_details_for_editing_query(), document_id, schema_name=schema_name)

    if not document:
        raise DocumentNotFoundError(document_id)

    return dict(document)


async def _fetch_document_signers(document_id: str, *, schema_name: str) -> List[Dict[str, Any]]:
    """Obtiene la lista de firmantes del documento."""
    signers_data = await fetch_all(get_document_signers_query(), document_id, schema_name=schema_name)

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


async def _fetch_document_rejection_info(document_id: str, *, schema_name: str) -> Optional[Dict[str, Any]]:
    """Obtiene informacion del ultimo rechazo del documento (si existe)."""
    rejection = await fetch_one(get_document_rejection_info_query(), document_id, schema_name=schema_name)

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


async def _fetch_document_recipients(document_id: str, document_type_source: str = None, *, schema_name: str) -> Optional[Dict[str, List]]:
    """Obtiene los recipients de un documento NOTA o MEMO para edición.

    El creador siempre ve todos los recipients (TO, CC, BCC) en modo edición.

    Args:
        document_id: UUID del documento
        document_type_source: Tipo de documento ('NOTA', 'MEMO', etc.)
        schema_name: Schema del tenant
    """
    if document_type_source == 'MEMO':
        from services.memos.queries import get_recipients_by_document_query as get_memo_recipients_query

        all_recipients = await fetch_all(get_memo_recipients_query(), document_id, schema_name=schema_name)

        if not all_recipients:
            return None

        result = {'to': [], 'cc': [], 'bcc': []}

        for r in all_recipients:
            recipient_data = {
                'user_id': str(r['recipient_user_id']),
                'name': r['recipient_name'],
                'sector_acronym': r['recipient_sector_acronym'] or ''
            }

            if r['recipient_type'] == 'TO':
                result['to'].append(recipient_data)
            elif r['recipient_type'] == 'CC':
                result['cc'].append(recipient_data)
            elif r['recipient_type'] == 'BCC':
                result['bcc'].append(recipient_data)

        return result
    else:
        # Default: NOTA recipients (sector-based)
        from services.notes.queries import get_recipients_by_document_query

        all_recipients = await fetch_all(get_recipients_by_document_query(), document_id, schema_name=schema_name)

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


async def _fetch_proposed_cases(document_id: str, *, schema_name: str) -> List[Dict]:
    """Obtiene los expedientes propuestos para un documento.

    Args:
        document_id: UUID del documento
        schema_name: Schema del tenant

    Returns:
        Lista de diccionarios con case_id, case_number, reference, proposing_date
    """
    proposed_cases = await fetch_all(get_proposed_cases_for_document_query(), document_id, schema_name=schema_name)

    result = []
    for case in proposed_cases:
        result.append({
            "case_id": str(case['case_id']),
            "case_number": case['case_number'],
            "reference": case.get('reference'),
            "proposing_date": case['proposing_date'].isoformat() if case.get('proposing_date') else None
        })

    return result


async def _validate_case_ids(case_ids: List[str], *, schema_name: str) -> None:
    """Valida que todos los expedientes existan y no estén archivados.

    Args:
        case_ids: Lista de UUIDs de expedientes
        schema_name: Schema del tenant

    Raises:
        ValidationError: Si algún expediente no existe o está archivado
    """
    if not case_ids:
        return

    for case_id in case_ids:
        result = await fetch_one(validate_case_exists_query(), case_id, schema_name=schema_name)

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
        Lista de tuplas (query, params) para ejecutar en transaccion
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
    logger.info(f"[DEBUG] document_type_source from DB: {doc_type_source}")
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
        "resume": document.get('resume'),
        "short_resume": document.get('short_resume')
    }

    # Incluir recipients si es tipo NOTA o MEMO
    if recipients is not None:
        if document.get('document_type_source') == 'MEMO':
            response["memo_recipients"] = recipients
        else:
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
        content_dict = {"html": effective_content}
        return [(update_document_reference_and_content_query(), (reference, content_dict, document_id))]
    elif reference is not None:
        return [(update_document_reference_query(), (reference, document_id))]
    else:  # effective_content is not None
        content_dict = {"html": effective_content}
        return [(update_document_content_query(), (content_dict, document_id))]


def _build_signers_update_operations(
    document_id: str,
    signers: List[Dict]
) -> List[tuple]:
    """Construye operaciones para actualizar firmantes."""
    operations = []

    operations.append((
        delete_document_signers_query(),
        (document_id,)
    ))

    for order, signer in enumerate(signers, 1):
        operations.append((
            insert_document_signer_ordered_query(),
            (document_id, signer.get('user_id'), order, signer.get('is_numerator', False))
        ))

    return operations


async def _process_recipients_update(
    conn,
    document_id: str,
    recipients: Dict,
    sender_sector_id: Optional[str],
    *,
    sender_user_id: Optional[str] = None,
    schema_name: str
) -> None:
    """
    Procesa la actualización de recipients para documentos NOTA o MEMO.

    Solo procesa si el documento es tipo NOTA o MEMO. Para otros tipos, ignora silenciosamente.

    Args:
        conn: Conexion asyncpg de la transacción padre
        document_id: UUID del documento
        recipients: Dict con {to: [], cc: [], bcc: []}
        sender_sector_id: UUID del sector emisor (para NOTA)
        sender_user_id: UUID del usuario emisor (para MEMO)
        schema_name: Schema del tenant
    """
    from services.notes.validation import (
        is_nota_document_type_by_id,
        validate_recipients_input,
        validate_recipients_exist
    )
    from services.notes.save_recipients import save_recipients, delete_recipients
    from services.memos.validation import (
        is_memo_document_type_by_id,
        validate_memo_recipients_input,
        validate_memo_recipients_exist
    )
    from services.memos.save_recipients import save_memo_recipients, delete_memo_recipients

    # Verificar si es NOTA
    if await is_nota_document_type_by_id(document_id, conn, schema_name=schema_name):
        # Validar y normalizar formato (incluye deduplicación)
        normalized = validate_recipients_input(recipients)

        # Validar que sectores existan (si hay recipients)
        has_recipients = normalized.get('to') or normalized.get('cc') or normalized.get('bcc')
        if has_recipients:
            if not sender_sector_id:
                raise ValidationError("Se requiere sender_sector_id para guardar recipients en NOTA")
            await validate_recipients_exist(conn, normalized, sender_sector_id, schema_name=schema_name)

        # Borrar existentes y guardar nuevos
        deleted_count = await delete_recipients(conn, document_id)
        if has_recipients:
            saved_count = await save_recipients(conn, document_id, sender_sector_id, normalized, schema_name=schema_name)
            logger.info(f"NOTA recipients actualizados: {deleted_count} eliminados, {saved_count} guardados")
        else:
            logger.info(f"NOTA recipients eliminados: {deleted_count} (lista vacía)")
        return

    # Verificar si es MEMO
    if await is_memo_document_type_by_id(document_id, conn, schema_name=schema_name):
        # Validar y normalizar formato (incluye deduplicación)
        normalized = validate_memo_recipients_input(recipients)

        # Validar que usuarios existan (si hay recipients)
        has_recipients = normalized.get('to') or normalized.get('cc') or normalized.get('bcc')
        if has_recipients:
            if not sender_user_id:
                raise ValidationError("Se requiere sender_user_id para guardar recipients en MEMO")
            await validate_memo_recipients_exist(conn, normalized, sender_user_id, schema_name=schema_name)

        # Borrar existentes y guardar nuevos
        deleted_count = await delete_memo_recipients(conn, document_id, schema_name=schema_name)
        if has_recipients:
            saved_count = await save_memo_recipients(conn, document_id, sender_user_id, normalized, schema_name=schema_name)
            logger.info(f"MEMO recipients actualizados: {deleted_count} eliminados, {saved_count} guardados")
        else:
            logger.info(f"MEMO recipients eliminados: {deleted_count} (lista vacía)")
        return

    logger.debug(f"Documento {document_id} no es NOTA ni MEMO, ignorando recipients")


async def get_document_details_for_editing(document_id: str, *, schema_name: str) -> Dict[str, Any]:
    """Obtiene los detalles completos de un documento para edicion."""
    logger.info(f"Obteniendo detalles de documento {document_id} para edicion")

    await _validate_document_can_be_edited(document_id, schema_name=schema_name)

    document = await _fetch_document_basic_details(document_id, schema_name=schema_name)
    signers = await _fetch_document_signers(document_id, schema_name=schema_name)

    rejection_info = None
    if document['status'] == 'rejected':
        rejection_info = await _fetch_document_rejection_info(document_id, schema_name=schema_name)

    # Obtener pdf_url si es documento importado Y el PDF existe
    pdf_url = None
    if document.get('document_type_source') == 'Importado':
        try:
            from services.storage.cloudflare import get_tenant_r2_client
            r2_client = await get_tenant_r2_client(schema_name=schema_name)
            document_id_no_hyphens = document_id.replace('-', '')
            r2_filename = f"{document_id_no_hyphens}.pdf"
            # Verificar si el PDF existe antes de generar URL
            if await run_in_threadpool(r2_client.exists_tosign, r2_filename):
                pdf_url = await run_in_threadpool(r2_client.get_tosign_url, r2_filename)
                logger.debug(f"PDF URL generada para documento importado: {pdf_url[:50]}...")
            else:
                logger.debug(f"PDF no existe aun para documento importado {document_id}")
        except Exception as e:
            logger.warning(f"No se pudo obtener PDF URL para documento {document_id}: {e}")

    # Obtener recipients si es tipo NOTA o MEMO
    recipients = None
    document_type_source = document.get('document_type_source')
    if document_type_source in ('NOTA', 'MEMO'):
        logger.info(f"Documento {document_id} es {document_type_source}, obteniendo recipients...")
        recipients = await _fetch_document_recipients(document_id, document_type_source=document_type_source, schema_name=schema_name)
        if recipients:
            logger.info(f"Recipients cargados: TO={len(recipients.get('to', []))}, CC={len(recipients.get('cc', []))}, BCC={len(recipients.get('bcc', []))}")
        else:
            logger.info(f"No se encontraron recipients para documento {document_id}")

    # Obtener expedientes propuestos
    proposed_cases = await _fetch_proposed_cases(document_id, schema_name=schema_name)
    if proposed_cases:
        logger.info(f"Expedientes propuestos cargados: {len(proposed_cases)} expedientes")

    return _build_complete_document_response(document, signers, rejection_info, pdf_url, recipients, proposed_cases)


async def save_document_changes(
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

    await _validate_document_can_be_edited(document_id, schema_name=schema_name)
    await _validate_document_update_data(reference, content, signers, schema_name=schema_name)

    # Sanitizar HTML para prevenir XSS stored
    if content:
        content = sanitize_html(content)

    # Tratar string vacio como None para la validacion de cambios
    effective_content = content if content else None
    if reference is None and effective_content is None and signers is None and recipients is None and proposed_case_ids is None:
        raise ValidationError(SAVE_NO_CHANGES_ERROR)

    # Validar expedientes propuestos si existen
    if proposed_case_ids is not None:
        if not user_id:
            raise ValidationError("Se requiere user_id para proponer vinculación a expedientes")
        await _validate_case_ids(proposed_case_ids, schema_name=schema_name)

    operations = []
    operations.extend(_build_document_update_operations(document_id, reference, content))

    if signers is not None:
        operations.extend(_build_signers_update_operations(document_id, signers))

    # Detectar propuestas NUEVAS antes de construir operaciones
    new_case_ids = set()
    if proposed_case_ids is not None:
        existing_rows = await fetch_all(
            "SELECT case_id::text FROM case_proposed_documents WHERE document_draft_id = $1 AND is_active = true",
            document_id,
            schema_name=schema_name
        )
        existing_case_ids = {row['case_id'] for row in existing_rows}
        new_case_ids = set(proposed_case_ids) - existing_case_ids

        operations.extend(_build_proposed_cases_update_operations(
            document_id, proposed_case_ids, user_id
        ))

    async with transaction(schema_name=schema_name) as conn:
        for query, params in operations:
            await conn.execute(query, *params)

        # Procesar recipients si se enviaron (para NOTA o MEMO)
        if recipients is not None:
            await _process_recipients_update(
                conn, document_id, recipients, sender_sector_id,
                sender_user_id=user_id,
                schema_name=schema_name
            )

    # Registrar historial para propuestas NUEVAS (después de la transacción)
    if proposed_case_ids is not None and new_case_ids:
        await _register_proposal_history(
            document_id, list(new_case_ids), user_id, sender_sector_id,
            schema_name=schema_name
        )

    updated_document = await get_document_details_for_editing(document_id, schema_name=schema_name)

    logger.info(f"Documento {document_id} actualizado exitosamente")

    return {
        "success": True,
        "message": SAVE_SUCCESS_MESSAGE,
        "document_id": document_id,
        "last_modified_at": updated_document.get("updated_at")
    }


async def _register_proposal_history(
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

    # Obtener referencia y numero del documento
    doc_row = await fetch_one(
        "SELECT reference, document_number FROM document_draft WHERE id = $1",
        document_id,
        schema_name=schema_name
    )
    reference = doc_row['reference'] if doc_row else 'Sin referencia'
    doc_number = doc_row.get('document_number') if doc_row else None

    # Si no tenemos sector del usuario, obtenerlo
    if not sender_sector_id:
        user_row = await fetch_one(
            "SELECT sector_id FROM users WHERE id = $1",
            user_id,
            schema_name=schema_name
        )
        sender_sector_id = str(user_row['sector_id']) if user_row else None

    # Construir mensaje
    reason = f"Propuso vincular documento: {reference}"
    if doc_number:
        reason += f" ({doc_number})"

    for case_id in new_case_ids:
        try:
            # Obtener admin_sector_id del expediente
            admin_row = await fetch_one(
                """SELECT admin_sector_id FROM case_movements
                   WHERE case_id = $1 AND type IN ('creation', 'transfer')
                   ORDER BY created_at DESC LIMIT 1""",
                case_id,
                schema_name=schema_name
            )
            admin_sector_id = str(admin_row['admin_sector_id']) if admin_row else sender_sector_id

            await create_movement(
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


async def check_document_can_be_edited(document_id: str, *, schema_name: str) -> Dict[str, Any]:
    """Verifica si un documento puede ser editado sin lanzar excepciones."""
    try:
        await _validate_document_can_be_edited(document_id, schema_name=schema_name)

        document = await fetch_one(get_document_status_query(), document_id, schema_name=schema_name)

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
