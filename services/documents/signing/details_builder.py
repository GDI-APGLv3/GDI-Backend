
import asyncio
from shared.logging import get_logger
from typing import Dict, Any, List, Optional
from database import fetch_all
from shared.exceptions import DocumentNotFoundError, AuthorizationError, reraise_if_transient
from services.shared.user_data import (
    get_user_complete_data,
    get_document_signers_complete_data
)
from services.shared.external_api import get_document_pdf_url
from services.documents.core.queries import (
    get_document_basic_info_for_signature_query,
    check_user_exists_query,
    check_user_is_document_signer_query,
    check_document_has_embeddings_query,
    get_linked_cases_for_official_document_query
)
from config.constants import (
    SIGNATURE_DOCUMENT_FINALIZED_MESSAGE,
    SIGNATURE_IN_PROCESS_MESSAGE,
    SIGNATURE_ALREADY_SIGNED_MESSAGE,
    SIGNATURE_NUMERATOR_WAITING_MESSAGE,
    SIGNATURE_USER_NOT_AUTHORIZED_ERROR
)
from services.notes.recipients import get_visible_recipients
from services.documents.lifecycle.editing import _fetch_proposed_cases

logger = get_logger(__name__)


async def build_signature_details_response(document_id: str, user_id: str, *, schema_name: str) -> Dict[str, Any]:
    logger.info(f"Construyendo detalles de firma - Doc: {document_id[:8]}, Usuario: {user_id[:8]}")

    document_info = await _get_document_basic_info(document_id, schema_name=schema_name)
    if not document_info:
        raise DocumentNotFoundError(document_id)

    has_official_number = document_info.get('official_number') is not None

    if has_official_number:
        await _validate_user_exists(user_id, schema_name=schema_name)
    else:
        await _validate_user_has_access(user_id, document_info, document_id, schema_name=schema_name)

    async def _fetch_recipients() -> Optional[Dict[str, Any]]:
        _doc_base_type = (document_info.get('document_base_type') or '').upper()
        if _doc_base_type == 'NOTA':
            user_data = await get_user_complete_data(user_id, schema_name=schema_name)
            requesting_sector_id = user_data.get('sector_id') if user_data else None

            if requesting_sector_id:
                try:
                    return await get_visible_recipients(
                        document_id=document_id,
                        requesting_sector_id=str(requesting_sector_id),
                        schema_name=schema_name
                    )
                except Exception as e:
                    reraise_if_transient(e, context=f"destinatarios de la NOTA {document_id[:8]}")
                    logger.warning(f"No se pudieron obtener recipients para NOTA {document_id[:8]}: {e}")
            return None
        elif _doc_base_type == 'MEMO':
            try:
                from services.memos.recipients import get_visible_memo_recipients
                return await get_visible_memo_recipients(
                    document_id=document_id,
                    requesting_user_id=user_id,
                    schema_name=schema_name
                )
            except Exception as e:
                reraise_if_transient(e, context=f"destinatarios del MEMO {document_id[:8]}")
                logger.warning(f"No se pudieron obtener recipients para MEMO {document_id[:8]}: {e}")
                return None
        return None

    async def _fetch_linked_cases_if_official() -> List[Dict[str, Any]]:
        if has_official_number:
            return await _fetch_linked_cases(document_id, user_id=user_id, schema_name=schema_name)
        return []

    creator_info, signers_data, recipients, proposed_cases, linked_cases = await asyncio.gather(
        get_user_complete_data(document_info['created_by'], schema_name=schema_name),
        get_document_signers_complete_data(document_id, schema_name=schema_name),
        _fetch_recipients(),
        _fetch_proposed_cases(document_id, schema_name=schema_name),
        _fetch_linked_cases_if_official(),
    )

    if proposed_cases:
        logger.info(f"Expedientes propuestos cargados: {len(proposed_cases)} expedientes")
    if linked_cases:
        logger.info(f"Expedientes vinculados (reales): {len(linked_cases)}")

    signatures_grouped = _group_signers_by_status(signers_data, user_id)

    result = await _build_final_response(
        document_info=document_info,
        creator_info=creator_info,
        signatures_grouped=signatures_grouped,
        user_id=user_id,
        document_id=document_id,
        has_official_number=has_official_number,
        recipients=recipients,
        proposed_cases=proposed_cases,
        linked_cases=linked_cases,
        schema_name=schema_name
    )

    logger.info("Detalles de firma construidos exitosamente")
    return result


async def _get_document_basic_info(document_id: str, *, schema_name: str) -> Optional[Dict[str, Any]]:
    rows = await fetch_all(
        get_document_basic_info_for_signature_query(),
        document_id,
        schema_name=schema_name,
    )
    return dict(rows[0]) if rows else None


async def _check_has_embeddings(document_id: str, *, schema_name: str) -> bool:
    rows = await fetch_all(
        check_document_has_embeddings_query(),
        document_id,
        schema_name=schema_name,
    )
    return rows[0]['has_embeddings'] if rows else False


async def _validate_user_exists(user_id: str, *, schema_name: str) -> None:
    rows = await fetch_all(
        check_user_exists_query(),
        user_id,
        schema_name=schema_name,
    )
    user_exists = rows[0]['user_exists'] if rows else False

    if not user_exists:
        raise AuthorizationError(f"Usuario '{user_id}' no existe en el sistema")


async def _validate_user_has_access(user_id: str, document_info: Dict[str, Any], document_id: str, *, schema_name: str) -> None:
    if user_id == document_info.get('created_by'):
        return

    if user_id == document_info.get('sent_by'):
        return

    rows = await fetch_all(
        check_user_is_document_signer_query(),
        document_id,
        user_id,
        schema_name=schema_name,
    )
    is_signer = rows[0]['is_signer'] if rows else False

    if is_signer:
        return

    from services.case_service import CaseService
    user_viewable_sectors = await CaseService.get_user_viewable_sector_ids(user_id, schema_name=schema_name)

    creator_result = await fetch_all(
        "SELECT u.sector_id FROM users u WHERE u.id = $1",
        document_info.get('created_by'),
        schema_name=schema_name,
    )
    creator_sector_id = str(creator_result[0]['sector_id']) if creator_result and creator_result[0].get('sector_id') else None

    if creator_sector_id and creator_sector_id in user_viewable_sectors:
        logger.info(f"Usuario {user_id[:8]} tiene acceso por permisos de sector")
        return

    raise AuthorizationError(SIGNATURE_USER_NOT_AUTHORIZED_ERROR)


def _group_signers_by_status(signers_data: List[Dict[str, Any]], current_user_id: str) -> Dict[str, Any]:
    pending_signatures = []
    completed_signatures = []

    for signer in signers_data:
        clean_signer = {k: v for k, v in signer.items() if k != 'is_current_user'}

        if signer['has_signed']:
            completed_signatures.append(clean_signer)
        else:
            pending_signatures.append(clean_signer)

    return {
        "pending": pending_signatures,
        "completed": completed_signatures,
        "pending_count": len(pending_signatures),
        "completed_count": len(completed_signatures)
    }


def _get_user_situation_message(
    user_id: str,
    document_info: Dict[str, Any],
    current_signer_info: Dict[str, Any],
    signatures_grouped: Dict[str, Any],
    has_official_number: bool
) -> Optional[str]:
    if has_official_number:
        return SIGNATURE_DOCUMENT_FINALIZED_MESSAGE

    if not current_signer_info:
        return SIGNATURE_IN_PROCESS_MESSAGE

    is_numerator = current_signer_info.get('is_numerator', False)
    has_signed = current_signer_info.get('has_signed', False)

    if has_signed and not is_numerator:
        return SIGNATURE_ALREADY_SIGNED_MESSAGE

    if is_numerator and not has_signed:
        other_pending = [s for s in signatures_grouped['pending'] if not s.get('is_numerator', False)]

        if other_pending:
            return SIGNATURE_NUMERATOR_WAITING_MESSAGE

    return None


async def _build_final_response(
    document_info: Dict[str, Any],
    creator_info: Optional[Dict[str, Any]],
    signatures_grouped: Dict[str, Any],
    user_id: str,
    document_id: str,
    has_official_number: bool,
    recipients: Optional[Dict[str, Any]] = None,
    proposed_cases: Optional[List[Dict[str, Any]]] = None,
    linked_cases: Optional[List[Dict[str, Any]]] = None,
    *,
    schema_name: str
) -> Dict[str, Any]:
    current_signer_info = _find_current_signer(signatures_grouped, user_id)

    can_sign = _can_user_sign(current_signer_info, signatures_grouped)


    has_embeddings = False
    if has_official_number:
        has_embeddings = await _check_has_embeddings(document_id, schema_name=schema_name)

    user_message = _get_user_situation_message(
        user_id=user_id,
        document_info=document_info,
        current_signer_info=current_signer_info,
        signatures_grouped=signatures_grouped,
        has_official_number=has_official_number
    )

    dept_acronym = creator_info.get('department_acronym') if creator_info else None
    sector_acronym = creator_info.get('sector_acronym') if creator_info else None
    department_sector = None

    if dept_acronym or sector_acronym:
        dept_part = dept_acronym if dept_acronym else ""
        sector_part = sector_acronym if sector_acronym else ""
        department_sector = f"{dept_part}#{sector_part}" if (dept_part or sector_part) else None

    document_section = {
        "document_id": document_info['document_id'],
        "reference": document_info.get('reference', ''),
        "status": document_info['status'],
        "document_type": {
            "name": document_info['document_type_name'],
            "acronym": document_info['document_type_acronym'],
            "is_public": document_info.get('document_type_visibility') == 'publico',
        },
        "created_by": document_info.get('created_by'),
        "creator_name": creator_info.get('full_name', '') if creator_info else '',
        "creator_profile_picture_url": creator_info.get('profile_picture_url') if creator_info else None,
        "creator_department_sector": department_sector,
        "creator_seal_name": creator_info.get('seal_name') if creator_info else None,
        "created_at": document_info['last_modified_at'].isoformat() if hasattr(document_info['last_modified_at'], 'isoformat') and document_info['last_modified_at'] else None,
        "resume": document_info.get('resume'),
        "short_resume": document_info.get('short_resume'),
        "has_embeddings": has_embeddings,
        "official_number": document_info.get('official_number'),
        "signature_policy": document_info.get('signature_policy')
    }

    pdf_url = None
    if document_info.get('document_generate_id'):
        document_section["document_generate_id"] = document_info['document_generate_id']
        try:
            pdf_url = await get_document_pdf_url(
                document_id=document_id,
                document_generate_id=document_info['document_generate_id'],
                document_status=document_info.get('status', 'sent_to_sign'),
                schema_name=schema_name
            )
        except Exception as e:
            logger.error(f"Error obteniendo PDF URL para documento {document_id[:8]}: {str(e)}")
            pdf_url = None

        if pdf_url:
            document_section["pdf_url"] = pdf_url

    is_creator = user_id == document_info.get('created_by')
    is_sent_by = user_id == document_info.get('sent_by')
    is_signer = current_signer_info and current_signer_info.get('user_id')
    is_sector_viewer = not is_creator and not is_sent_by and not is_signer

    response = {
        "document": document_section,
        "signature_policy": document_info.get('signature_policy'),
        "current_signer": {
            "user_id": current_signer_info.get('user_id', user_id),
            "user_name": current_signer_info.get('full_name', ''),
            "email": current_signer_info.get('email', ''),
            "profile_picture_url": current_signer_info.get('profile_picture_url', None),
            "signing_order": current_signer_info.get('signing_order', 1),
            "is_numerator": current_signer_info.get('is_numerator', False),
            "already_signed": current_signer_info.get('has_signed', False),
            "seal_name": current_signer_info.get('seal_name', None)
        } if current_signer_info else None,
        "signature_progress": {
            "completed": signatures_grouped['completed_count'],
            "total": signatures_grouped['pending_count'] + signatures_grouped['completed_count'],
            "signatures": _format_signatures_for_progress(signatures_grouped)
        },
        "can_sign": can_sign,
        "is_sector_viewer": is_sector_viewer
    }

    if pdf_url:
        response["pdf_url"] = pdf_url

    if user_message:
        response["message"] = user_message

    if recipients:
        response["recipients"] = recipients

    if proposed_cases:
        response["proposed_cases"] = proposed_cases

    response["auto_link_on_sign"] = any(
        pc.get("auto_link_on_sign", False) for pc in (proposed_cases or [])
    )

    if linked_cases:
        response["linked_cases"] = linked_cases

    return response


async def _fetch_linked_cases(
    document_id: str, *, user_id: str, schema_name: str
) -> List[Dict[str, Any]]:
    from services.cases.permissions import can_user_view_case

    rows = await fetch_all(
        get_linked_cases_for_official_document_query(),
        document_id,
        schema_name=schema_name,
    )
    result = []
    for r in rows:
        case_id_str = str(r["case_id"])
        raw_reserved = r["is_reserved"] if "is_reserved" in r.keys() else None
        if raw_reserved is None:
            raise ValueError(
                f"_fetch_linked_cases: fila sin columna is_reserved para case "
                f"{case_id_str} — la query debe garantizar la columna (ver "
                "get_linked_cases_for_official_document_query)."
            )
        is_reserved = bool(raw_reserved)

        if is_reserved and not await can_user_view_case(
            case_id_str, user_id, schema_name=schema_name
        ):
            continue

        result.append({
            "case_id": case_id_str,
            "case_number": r["case_number"],
            "reference": r.get("reference"),
            "is_reserved": is_reserved,
            "order_number": r.get("order_number"),
            "linking_date": r["linking_date"].isoformat() if r.get("linking_date") else None,
        })
    return result


def _find_current_signer(signatures_grouped: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    for signer in signatures_grouped['pending']:
        if signer['user_id'] == user_id:
            return signer

    for signer in signatures_grouped['completed']:
        if signer['user_id'] == user_id:
            return signer

    return {}


def _can_user_sign(current_signer_info: Dict[str, Any], signatures_grouped: Dict[str, Any]) -> bool:
    if not current_signer_info:
        return False

    if current_signer_info.get('has_signed', False):
        return False

    is_numerator = current_signer_info.get('is_numerator', False)

    if is_numerator:
        other_pending = [s for s in signatures_grouped['pending'] if not s.get('is_numerator', False)]

        if other_pending:
            return False
        else:
            return True
    else:
        return current_signer_info in signatures_grouped['pending']


def _format_signatures_for_progress(signatures_grouped: Dict[str, Any]) -> List[Dict[str, Any]]:
    signatures = []

    for signer in signatures_grouped['pending']:
        signatures.append({
            "user_id": signer['user_id'],
            "citizen_id": signer.get('citizen_id'),
            "country_id": signer.get('country_id'),
            "user_name": signer['full_name'],
            "email": "",
            "profile_picture_url": signer.get('profile_picture_url', None),
            "signing_order": 1,
            "is_numerator": signer.get('is_numerator', False),
            "has_signed": False,
            "signed_at": None,
            "is_current_user": False,
            "seal_name": signer.get('seal_name', None),
            "department_acronym": signer.get('department_acronym'),
            "sector_acronym": signer.get('sector_acronym'),
            "sector_color": signer.get('sector_color')
        })

    for signer in signatures_grouped['completed']:
        raw_signed_at = signer.get('signed_at')
        signatures.append({
            "user_id": signer['user_id'],
            "citizen_id": signer.get('citizen_id'),
            "country_id": signer.get('country_id'),
            "user_name": signer['full_name'],
            "email": "",
            "profile_picture_url": signer.get('profile_picture_url', None),
            "signing_order": 1,
            "is_numerator": signer.get('is_numerator', False),
            "has_signed": True,
            "signed_at": raw_signed_at.isoformat() if raw_signed_at else None,
            "is_current_user": False,
            "seal_name": signer.get('seal_name', None),
            "department_acronym": signer.get('department_acronym'),
            "sector_acronym": signer.get('sector_acronym'),
            "sector_color": signer.get('sector_color')
        })

    return signatures
