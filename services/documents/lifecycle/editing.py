
import uuid
from typing import Dict, Any, List, Optional, Tuple
from fastapi.concurrency import run_in_threadpool
from shared.logging import get_logger
from database import fetch_one, fetch_all, transaction
from shared.exceptions import DocumentNotFoundError, ValidationError, DocumentStateError, AuthorizationError
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
    if reference is not None:
        ref_error = validate_required_string(reference, "reference", min_length=1, max_length=250)
        if ref_error:
            raise ValidationError(ref_error)

    if content:
        content_error = validate_required_string(content, "content", min_length=1)
        if content_error:
            raise ValidationError(content_error)

    if signers is not None:
        signers_error = await validate_document_signers(signers, schema_name=schema_name)
        if signers_error:
            raise ValidationError(signers_error)


async def _fetch_document_basic_details(document_id: str, *, schema_name: str) -> Dict[str, Any]:
    document = await fetch_one(get_document_details_for_editing_query(), document_id, schema_name=schema_name)

    if not document:
        raise DocumentNotFoundError(document_id)

    return dict(document)


async def _fetch_document_signers(document_id: str, *, schema_name: str) -> List[Dict[str, Any]]:
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
            "department_sector": department_sector,
            "seal_name": signer.get('seal_name'),
            "department_acronym": dept_acronym,
            "sector_acronym": sector_acronym,
            "sector_color": signer.get('sector_color'),
        })

    return result


async def _fetch_document_rejection_info(document_id: str, *, schema_name: str) -> Optional[Dict[str, Any]]:
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
    if not content_json:
        return ""

    return content_json.get('html') or content_json.get('detalle', '')


async def _fetch_document_recipients(document_id: str, document_type_source: str = None, *, schema_name: str) -> Optional[Dict[str, List]]:
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
    proposed_cases = await fetch_all(get_proposed_cases_for_document_query(), document_id, schema_name=schema_name)

    result = []
    for case in proposed_cases:
        result.append({
            "case_id": str(case['case_id']),
            "case_number": case['case_number'],
            "reference": case.get('reference'),
            "is_reserved": bool(case.get('is_reserved', False)),
            "proposing_date": case['proposing_date'].isoformat() if case.get('proposing_date') else None,
            "auto_link_on_sign": bool(case.get('auto_link_on_sign', False))
        })

    return result


async def _validate_case_ids(case_ids: List[str], *, schema_name: str) -> None:
    if not case_ids:
        return

    for case_id in case_ids:
        result = await fetch_one(validate_case_exists_query(), case_id, schema_name=schema_name)

        if not result:
            raise ValidationError(f"El expediente '{case_id}' no existe o está archivado")


async def _validate_regla1_proposed_cases(
    document_id: str,
    case_ids: List[str],
    *,
    schema_name: str,
) -> None:
    if not case_ids:
        return

    doc_row = await fetch_one(
        """
        SELECT COALESCE(dt.is_reserved, false) AS doc_reserved
        FROM document_draft dd
        LEFT JOIN document_types dt ON dd.document_type_id = dt.id
        WHERE dd.id = $1
        """,
        document_id,
        schema_name=schema_name,
    )
    if not doc_row:
        from services.documents.signing.lookup_guard import (
            confirm_document_missing,
        )
        await confirm_document_missing(
            document_id, schema_name=schema_name,
            context="editing._validate_regla1_proposed_cases",
        )
        raise DocumentNotFoundError(document_id)

    if not doc_row['doc_reserved']:
        return

    non_reserved_cases = await fetch_all(
        """
        SELECT c.id
        FROM cases c
        JOIN case_templates ct ON ct.id = c.case_template_id
        WHERE c.id = ANY($1::uuid[]) AND ct.is_reserved = false
        """,
        case_ids,
        schema_name=schema_name,
    )
    if non_reserved_cases:
        raise ValidationError(
            "Un documento reservado solo puede vincularse a un expediente reservado"
        )


def _build_proposed_cases_update_operations(
    document_id: str,
    proposed_case_ids: List[str],
    user_id: str,
    auto_link_on_sign: bool = False
) -> List[Tuple[str, Tuple]]:
    operations = []

    operations.append((
        delete_proposed_cases_for_document_query(),
        (document_id,)
    ))

    unique_case_ids = list(set(proposed_case_ids))

    if unique_case_ids:
        logger.info(f"Deduplicados {len(proposed_case_ids)} -> {len(unique_case_ids)} expedientes únicos")

    for case_id in unique_case_ids:
        new_id = str(uuid.uuid4())
        operations.append((
            insert_proposed_case_query(),
            (new_id, case_id, document_id, user_id, auto_link_on_sign)
        ))

    return operations


def _build_document_type_info(document: Dict[str, Any]) -> Dict[str, Any]:
    doc_type_source = document.get('document_type_source')
    logger.info(f"[DEBUG] document_type_source from DB: {doc_type_source}")
    return {
        "name": document['document_type_name'] or "Sin tipo",
        "acronym": document['document_type_acronym'] or "",
        "type": doc_type_source or "HTML",
        "has_fields": bool(document.get('has_fields')),
        "is_public": document.get('document_type_visibility') == 'publico',
    }


def _build_department_sector(dept_acronym: Optional[str], sector_acronym: Optional[str]) -> Optional[str]:
    if dept_acronym or sector_acronym:
        return f"{dept_acronym or ''}#{sector_acronym or ''}"
    return None


def _build_complete_document_response(
    document: Dict[str, Any],
    signers: List[Dict[str, Any]],
    rejection_info: Optional[Dict[str, Any]],
    pdf_url: Optional[str] = None,
    recipients: Optional[Dict[str, List]] = None,
    proposed_cases: Optional[List[Dict]] = None,
    field_definitions: Optional[List] = None,
    embedded_files: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    department_sector = _build_department_sector(
        document.get('creator_department_acronym'),
        document.get('creator_sector_acronym')
    )

    doc_source = document.get('document_type_source')
    is_imported = doc_source == 'Importado'
    has_form_fields = bool(document.get('has_fields'))

    if has_form_fields:
        raw_content = document.get('content')
        content_value = raw_content if isinstance(raw_content, dict) else (raw_content or {})
    else:
        content_value = _extract_html_content_from_document_json(document['content'])

    response = {
        "document_id": document['id'],
        "reference": document['reference'],
        "content": content_value,
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

    if recipients is not None:
        if doc_source == 'MEMO':
            response["memo_recipients"] = recipients
        else:
            response["recipients"] = recipients

    if proposed_cases is not None:
        response["proposed_cases"] = proposed_cases

    response["auto_link_on_sign"] = any(
        case.get("auto_link_on_sign", False) for case in (proposed_cases or [])
    )

    if field_definitions is not None:
        response["field_definitions"] = field_definitions

    if embedded_files is not None:
        response["embedded_files"] = embedded_files

    return response


def _build_document_update_operations(
    document_id: str,
    reference: Optional[str],
    content: Optional[str]
) -> List[tuple]:
    effective_content = content if content else None

    if reference is None and effective_content is None:
        return []

    if reference is not None and effective_content is not None:
        content_dict = {"html": effective_content}
        return [(update_document_reference_and_content_query(), (reference, content_dict, document_id))]
    elif reference is not None:
        return [(update_document_reference_query(), (reference, document_id))]
    else:
        content_dict = {"html": effective_content}
        return [(update_document_content_query(), (content_dict, document_id))]


def _build_document_update_operations_with_dict(
    document_id: str,
    reference: Optional[str],
    content_dict: Dict,
) -> List[tuple]:
    if reference is None and not content_dict:
        return []

    if reference is not None and content_dict:
        return [(update_document_reference_and_content_query(), (reference, content_dict, document_id))]
    elif reference is not None:
        return [(update_document_reference_query(), (reference, document_id))]
    else:
        return [(update_document_content_query(), (content_dict, document_id))]


def _build_signers_update_operations(
    document_id: str,
    signers: List[Dict]
) -> List[tuple]:
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

    if await is_nota_document_type_by_id(document_id, conn, schema_name=schema_name):
        normalized = validate_recipients_input(recipients)

        has_recipients = normalized.get('to') or normalized.get('cc') or normalized.get('bcc')
        if has_recipients:
            if not sender_sector_id:
                raise ValidationError("Se requiere sender_sector_id para guardar recipients en NOTA")
            await validate_recipients_exist(conn, normalized, sender_sector_id, schema_name=schema_name)

        deleted_count = await delete_recipients(conn, document_id)
        if has_recipients:
            saved_count = await save_recipients(conn, document_id, sender_sector_id, normalized, schema_name=schema_name)
            logger.info(f"NOTA recipients actualizados: {deleted_count} eliminados, {saved_count} guardados")
        else:
            logger.info(f"NOTA recipients eliminados: {deleted_count} (lista vacía)")
        return

    if await is_memo_document_type_by_id(document_id, conn, schema_name=schema_name):
        normalized = validate_memo_recipients_input(recipients)

        has_recipients = normalized.get('to') or normalized.get('cc') or normalized.get('bcc')
        if has_recipients:
            if not sender_user_id:
                raise ValidationError("Se requiere sender_user_id para guardar recipients en MEMO")
            await validate_memo_recipients_exist(conn, normalized, sender_user_id, schema_name=schema_name)

        deleted_count = await delete_memo_recipients(conn, document_id, schema_name=schema_name)
        if has_recipients:
            saved_count = await save_memo_recipients(conn, document_id, sender_user_id, normalized, schema_name=schema_name)
            logger.info(f"MEMO recipients actualizados: {deleted_count} eliminados, {saved_count} guardados")
        else:
            logger.info(f"MEMO recipients eliminados: {deleted_count} (lista vacía)")
        return

    logger.debug(f"Documento {document_id} no es NOTA ni MEMO, ignorando recipients")


async def get_document_details_for_editing(document_id: str, user_id: Optional[str] = None, *, schema_name: str) -> Dict[str, Any]:
    logger.info(f"Obteniendo detalles de documento {document_id} para edicion")

    await _validate_document_can_be_edited(document_id, schema_name=schema_name)

    document = await _fetch_document_basic_details(document_id, schema_name=schema_name)
    signers = await _fetch_document_signers(document_id, schema_name=schema_name)

    rejection_info = None
    if document['status'] == 'rejected':
        rejection_info = await _fetch_document_rejection_info(document_id, schema_name=schema_name)

    pdf_url = None
    if document.get('document_type_source') == 'Importado':
        try:
            from services.storage.cloudflare import get_tenant_r2_client
            r2_client = await get_tenant_r2_client(schema_name=schema_name)
            document_id_no_hyphens = document_id.replace('-', '')
            r2_filename = f"{document_id_no_hyphens}.pdf"
            if await run_in_threadpool(r2_client.exists_tosign, r2_filename):
                pdf_url = await run_in_threadpool(r2_client.get_tosign_url, r2_filename)
                logger.debug(f"PDF URL generada para documento importado: {pdf_url[:50]}...")
            else:
                logger.debug(f"PDF no existe aun para documento importado {document_id}")
        except Exception as e:
            logger.warning(f"No se pudo obtener PDF URL para documento {document_id}: {e}")

    recipients = None
    document_type_source = document.get('document_type_source')
    if document_type_source in ('NOTA', 'MEMO'):
        logger.info(f"Documento {document_id} es {document_type_source}, obteniendo recipients...")
        recipients = await _fetch_document_recipients(document_id, document_type_source=document_type_source, schema_name=schema_name)
        if recipients:
            logger.info(f"Recipients cargados: TO={len(recipients.get('to', []))}, CC={len(recipients.get('cc', []))}, BCC={len(recipients.get('bcc', []))}")
        else:
            logger.info(f"No se encontraron recipients para documento {document_id}")

    proposed_cases = await _fetch_proposed_cases(document_id, schema_name=schema_name)
    if proposed_cases:
        logger.info(f"Expedientes propuestos cargados: {len(proposed_cases)} expedientes")

    field_definitions = None
    if bool(document.get('has_fields')):
        logger.info(f"Documento {document_id} tiene formulario controlado, obteniendo field_definitions...")
        fd_row = await fetch_one(
            """SELECT dtf.field_definitions
               FROM document_draft dd
               JOIN document_type_fields dtf ON dd.document_type_id = dtf.document_type_id
               WHERE dd.id = $1""",
            document_id,
            schema_name=schema_name,
        )
        field_definitions = fd_row['field_definitions'] if fd_row else []
        logger.info(f"field_definitions cargadas: {len(field_definitions) if field_definitions else 0} campos")

    embedded_files = None
    if bool(document.get('accepts_embedded_files')):
        if user_id:
            from services.documents.lifecycle.embedded_files import list_embedded_files
            embedded_files = await list_embedded_files(document_id, user_id, schema_name=schema_name)
        else:
            logger.warning(
                f"get_document_details_for_editing: documento {document_id} acepta embebidos "
                "pero no se recibió user_id — se omite embedded_files (fail-safe, sin caller conocido "
                "que llegue hasta acá sin haber verificado permisos antes)."
            )

    return _build_complete_document_response(
        document, signers, rejection_info, pdf_url, recipients, proposed_cases,
        field_definitions=field_definitions,
        embedded_files=embedded_files,
    )


async def save_document_changes(
    document_id: str,
    reference: Optional[str] = None,
    content: Optional[str] = None,
    signers: Optional[List[Dict]] = None,
    recipients: Optional[Dict] = None,
    sender_sector_id: Optional[str] = None,
    proposed_case_ids: Optional[List[str]] = None,
    user_id: Optional[str] = None,
    auto_link_on_sign: bool = False,
    *,
    schema_name: str
) -> Dict[str, Any]:
    logger.info(f"Guardando cambios en documento {document_id} en schema {schema_name}")

    await _validate_document_can_be_edited(document_id, schema_name=schema_name)

    if user_id is not None:
        owner_row = await fetch_one(
            "SELECT created_by FROM document_draft WHERE id = $1",
            document_id,
            schema_name=schema_name,
        )
        if owner_row and str(owner_row["created_by"]) != str(user_id):
            raise AuthorizationError("Solo el creador puede editar este documento")

    is_ffcc = False
    ffcc_data_dict: Optional[Dict] = None

    if content is not None:
        doc_type_row = await fetch_one(
            """SELECT dt.type AS source_type, dt.id AS document_type_id
               FROM document_draft dd
               JOIN document_types dt ON dd.document_type_id = dt.id
               WHERE dd.id = $1""",
            document_id,
            schema_name=schema_name,
        )
        if doc_type_row:
            field_defs_row = await fetch_one(
                "SELECT field_definitions FROM document_type_fields WHERE document_type_id = $1",
                doc_type_row['document_type_id'],
                schema_name=schema_name,
            )
            has_form_fields = field_defs_row is not None
        else:
            field_defs_row = None
            has_form_fields = False

        if has_form_fields:
            is_ffcc = True
            logger.info(f"Documento {document_id} tiene formulario controlado, aplicando validacion FFCC")
            import json as _json
            try:
                if isinstance(content, str):
                    ffcc_data_dict = _json.loads(content)
                elif isinstance(content, dict):
                    ffcc_data_dict = content
                else:
                    raise ValidationError("El contenido FFCC debe ser un objeto JSON")
            except (_json.JSONDecodeError, TypeError) as e:
                raise ValidationError(f"El contenido del formulario no es JSON valido: {e}")

            field_defs = field_defs_row['field_definitions'] if field_defs_row else []

            from services.documents.ffcc_validator import validate_ffcc_content
            validate_ffcc_content(
                ffcc_data_dict,
                field_defs,
                schema_name=schema_name,
                enforce_required=False,
            )
            logger.info(f"Validacion FFCC OK para documento {document_id} (draft, enforce_required=False)")

    if not is_ffcc:
        await _validate_document_update_data(reference, content, signers, schema_name=schema_name)
        if content:
            content = sanitize_html(content)
    else:
        if reference is not None:
            ref_error = validate_required_string(reference, "reference", min_length=1, max_length=250)
            if ref_error:
                raise ValidationError(ref_error)
        if signers is not None:
            signers_error = await validate_document_signers(signers, schema_name=schema_name)
            if signers_error:
                raise ValidationError(signers_error)

    effective_content = content if content else None
    if reference is None and effective_content is None and signers is None and recipients is None and proposed_case_ids is None:
        raise ValidationError(SAVE_NO_CHANGES_ERROR)

    if proposed_case_ids is not None:
        if not user_id:
            raise ValidationError("Se requiere user_id para proponer vinculación a expedientes")
        await _validate_case_ids(proposed_case_ids, schema_name=schema_name)
        await _validate_regla1_proposed_cases(document_id, proposed_case_ids, schema_name=schema_name)

    operations = []
    if is_ffcc and ffcc_data_dict is not None:
        operations.extend(_build_document_update_operations_with_dict(
            document_id, reference, ffcc_data_dict
        ))
    else:
        operations.extend(_build_document_update_operations(document_id, reference, content))

    if signers is not None:
        operations.extend(_build_signers_update_operations(document_id, signers))

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
            document_id, proposed_case_ids, user_id, auto_link_on_sign=auto_link_on_sign
        ))

    async with transaction(schema_name=schema_name) as conn:
        for query, params in operations:
            await conn.execute(query, *params)

        if recipients is not None:
            await _process_recipients_update(
                conn, document_id, recipients, sender_sector_id,
                sender_user_id=user_id,
                schema_name=schema_name
            )

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
    from services.cases.history import create_movement
    from config.constants import MOVEMENT_TYPE_DOCUMENT_PROPOSAL

    doc_row = await fetch_one(
        "SELECT reference, document_number FROM document_draft WHERE id = $1",
        document_id,
        schema_name=schema_name
    )
    reference = doc_row['reference'] if doc_row else 'Sin referencia'
    doc_number = doc_row.get('document_number') if doc_row else None

    if not sender_sector_id:
        user_row = await fetch_one(
            "SELECT sector_id FROM users WHERE id = $1",
            user_id,
            schema_name=schema_name
        )
        sender_sector_id = str(user_row['sector_id']) if user_row else None

    reason = f"Propuso vincular documento: {reference}"
    if doc_number:
        reason += f" ({doc_number})"

    for case_id in new_case_ids:
        try:
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
