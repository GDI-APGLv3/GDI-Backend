"""
Servicio específico para signature_details endpoint.
Implementa Clean Architecture reutilizando componentes compartidos.

Responsabilidades:
- Obtener detalles de documento para firma
- Agrupar firmantes por estado (pendientes/completados)
- Validar permisos y estados
- Mantener consistencia con otros endpoints
"""

from shared.logging import get_logger
from typing import Dict, Any, List, Optional
from database import get_db_connection, execute_query
from shared.exceptions import DocumentNotFoundError, ValidationError, AuthorizationError
from services.shared.user_data import (
    get_user_complete_data,
    get_document_signers_complete_data
)
from services.shared.external_api import get_document_pdf_url
from services.documents.core.queries import (
    get_document_basic_info_for_signature_query,
    check_document_has_official_number_query,
    check_user_exists_query,
    check_user_is_document_signer_query,
    check_document_has_embeddings_query
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
    """Construye la respuesta completa para signature_details endpoint.

    Args:
        document_id: UUID del documento
        user_id: UUID del usuario solicitante
        schema_name: Nombre del esquema de base de datos (multi-tenant)

    Returns:
        Dict con estructura completa del endpoint

    Raises:
        DocumentNotFoundError: Si el documento no existe
        AuthorizationError: Si el usuario no tiene permisos
    """
    logger.info(f"Construyendo detalles de firma - Doc: {document_id[:8]}, Usuario: {user_id[:8]}")

    # 1. Obtener datos básicos del documento
    document_info = _get_document_basic_info(document_id, schema_name=schema_name)
    if not document_info:
        raise DocumentNotFoundError(document_id)

    # 2. Verificar si el documento tiene official_number
    has_official_number = _has_official_number(document_id, schema_name=schema_name)

    # 3. Validar permisos según estado del documento
    if has_official_number:
        _validate_user_exists(user_id, schema_name=schema_name)
    else:
        _validate_user_has_access(user_id, document_info, document_id, schema_name=schema_name)

    # 4. Obtener datos completos del creador
    creator_info = get_user_complete_data(document_info['created_by'], schema_name=schema_name)

    # 5. Obtener firmantes
    signers_data = get_document_signers_complete_data(document_id, schema_name=schema_name)

    # 5.1 Obtener recipients si es documento tipo NOTA
    recipients = None
    if document_info.get('document_type_acronym') == 'NOTA':
        # Obtener sector_id del usuario actual
        user_data = get_user_complete_data(user_id, schema_name=schema_name)
        requesting_sector_id = user_data.get('sector_id') if user_data else None

        if requesting_sector_id:
            try:
                recipients = get_visible_recipients(
                    document_id=document_id,
                    requesting_sector_id=str(requesting_sector_id),
                    schema_name=schema_name
                )
            except Exception as e:
                logger.warning(f"No se pudieron obtener recipients para NOTA {document_id[:8]}: {e}")
                recipients = None

    # 5.2 Obtener expedientes propuestos
    proposed_cases = _fetch_proposed_cases(document_id, schema_name=schema_name)
    if proposed_cases:
        logger.info(f"Expedientes propuestos cargados: {len(proposed_cases)} expedientes")

    # 6. Agrupar firmantes por estado
    signatures_grouped = _group_signers_by_status(signers_data, user_id)

    # 7. Construir respuesta final
    result = await _build_final_response(
        document_info=document_info,
        creator_info=creator_info,
        signatures_grouped=signatures_grouped,
        user_id=user_id,
        document_id=document_id,
        recipients=recipients,
        proposed_cases=proposed_cases,
        schema_name=schema_name
    )

    logger.info("Detalles de firma construidos exitosamente")
    return result


# ============================================================================
# OBTENCIÓN DE DATOS - Data Access Layer
# ============================================================================

def _get_document_basic_info(document_id: str, *, schema_name: str) -> Optional[Dict[str, Any]]:
    """Obtiene información básica del documento para signature_details.

    Args:
        document_id: UUID del documento
        schema_name: Nombre del esquema de base de datos (multi-tenant)

    Returns:
        Dict con datos básicos o None si no existe
    """
    result = execute_query(get_document_basic_info_for_signature_query(), (document_id,), schema_name=schema_name)
    return result[0] if result else None


# ============================================================================
# VALIDACIONES - Business Logic Layer
# ============================================================================

def _has_official_number(document_id: str, *, schema_name: str) -> bool:
    """Verifica si el documento tiene número oficial asignado.

    Args:
        document_id: UUID del documento
        schema_name: Nombre del esquema de base de datos (multi-tenant)

    Returns:
        bool: True si el documento está en official_documents, False si no
    """
    result = execute_query(check_document_has_official_number_query(), (document_id,), schema_name=schema_name)
    return result[0]['has_official_number'] if result else False


def _check_has_embeddings(document_id: str, *, schema_name: str) -> bool:
    """Verifica si el documento tiene embeddings indexados (con embedding NOT NULL).

    Args:
        document_id: UUID del documento oficial
        schema_name: Nombre del esquema de base de datos (multi-tenant)

    Returns:
        bool: True si tiene al menos un chunk con embedding, False si no
    """
    result = execute_query(check_document_has_embeddings_query(), (document_id,), schema_name=schema_name)
    return result[0]['has_embeddings'] if result else False


def _validate_user_exists(user_id: str, *, schema_name: str) -> None:
    """Valida que el usuario exista en la base de datos.

    Args:
        user_id: UUID del usuario
        schema_name: Nombre del esquema de base de datos (multi-tenant)

    Raises:
        AuthorizationError: Si el usuario no existe
    """
    result = execute_query(check_user_exists_query(), (user_id,), schema_name=schema_name)
    user_exists = result[0]['user_exists'] if result else False

    if not user_exists:
        raise AuthorizationError(f"Usuario '{user_id}' no existe en el sistema")


def _validate_user_has_access(user_id: str, document_info: Dict[str, Any], document_id: str, *, schema_name: str) -> None:
    """Valida que el usuario tenga acceso al documento en proceso de firma.

    Args:
        user_id: UUID del usuario
        document_info: Información del documento (incluye created_by y sent_by)
        document_id: UUID del documento
        schema_name: Nombre del esquema de base de datos (multi-tenant)

    Raises:
        AuthorizationError: Si el usuario no tiene permisos
    """
    # Verificar si es creador, quien envió a firma, o firmante
    if user_id == document_info.get('created_by'):
        return

    if user_id == document_info.get('sent_by'):
        return

    # Verificar si es firmante
    result = execute_query(check_user_is_document_signer_query(), (document_id, user_id), schema_name=schema_name)
    is_signer = result[0]['is_signer'] if result else False

    if is_signer:
        return

    # Verificar si tiene permisos de sector sobre el sector del creador
    from services.case_service import CaseService
    user_viewable_sectors = CaseService.get_user_viewable_sector_ids(user_id, schema_name=schema_name)

    # Obtener sector del creador del documento
    creator_sector_query = """
        SELECT u.sector_id
        FROM users u
        WHERE u.id = %s
    """
    creator_result = execute_query(creator_sector_query, (document_info.get('created_by'),), schema_name=schema_name)
    creator_sector_id = str(creator_result[0]['sector_id']) if creator_result and creator_result[0].get('sector_id') else None

    if creator_sector_id and creator_sector_id in user_viewable_sectors:
        logger.info(f"Usuario {user_id[:8]} tiene acceso por permisos de sector")
        return

    # Si no es ninguno, denegar acceso
    raise AuthorizationError(SIGNATURE_USER_NOT_AUTHORIZED_ERROR)


# ============================================================================
# PROCESAMIENTO DE DATOS - Business Logic Layer
# ============================================================================

def _group_signers_by_status(signers_data: List[Dict[str, Any]], current_user_id: str) -> Dict[str, Any]:
    """
    Agrupa firmantes por estado de firma y calcula estadísticas.

    Args:
        signers_data: Lista completa de firmantes
        current_user_id: ID del usuario actual (para validación interna)

    Returns:
        Dict con firmantes agrupados y conteos
    """
    pending_signatures = []
    completed_signatures = []

    for signer in signers_data:
        # Crear copia sin el campo is_current_user
        clean_signer = {k: v for k, v in signer.items() if k != 'is_current_user'}

        # Agrupar por estado
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


# ============================================================================
# CONSTRUCCIÓN DE RESPUESTA - Presentation Layer
# ============================================================================

def _get_user_situation_message(
    user_id: str,
    document_info: Dict[str, Any],
    current_signer_info: Dict[str, Any],
    signatures_grouped: Dict[str, Any],
    has_official_number: bool
) -> Optional[str]:
    """Genera un mensaje específico según la situación del usuario respecto al documento.

    Args:
        user_id: ID del usuario actual
        document_info: Información del documento
        current_signer_info: Información del usuario como firmante
        signatures_grouped: Firmantes agrupados por estado
        has_official_number: True si el documento ya está oficializado

    Returns:
        str con el mensaje específico o None si no aplica mensaje especial
    """
    # Caso 1: Documento ya finalizado
    if has_official_number:
        return SIGNATURE_DOCUMENT_FINALIZED_MESSAGE

    # Caso 2: Usuario no es firmante (es creador/sent_by observando)
    if not current_signer_info:
        return SIGNATURE_IN_PROCESS_MESSAGE

    # De aquí en adelante el usuario ES firmante
    is_numerator = current_signer_info.get('is_numerator', False)
    has_signed = current_signer_info.get('has_signed', False)

    # Caso 3: Firmante común que ya firmó
    if has_signed and not is_numerator:
        return SIGNATURE_ALREADY_SIGNED_MESSAGE

    # Caso 4: Numerador esperando su turno
    if is_numerator and not has_signed:
        # Verificar si hay otros firmantes pendientes (además del numerador)
        other_pending = [s for s in signatures_grouped['pending'] if not s.get('is_numerator', False)]

        if other_pending:  # Hay firmantes comunes pendientes
            return SIGNATURE_NUMERATOR_WAITING_MESSAGE

    # Caso 5: Usuario puede firmar ahora (firmante común o numerador en su turno)
    return None


async def _build_final_response(
    document_info: Dict[str, Any],
    creator_info: Optional[Dict[str, Any]],
    signatures_grouped: Dict[str, Any],
    user_id: str,
    document_id: str,
    recipients: Optional[Dict[str, Any]] = None,
    proposed_cases: Optional[List[Dict[str, Any]]] = None,
    *,
    schema_name: str
) -> Dict[str, Any]:
    """Construye la respuesta final del endpoint signature_details.

    Args:
        document_info: Información básica del documento
        creator_info: Datos completos del creador
        signatures_grouped: Firmantes agrupados por estado
        user_id: ID del usuario actual
        document_id: UUID del documento
        schema_name: Nombre del esquema de base de datos (multi-tenant)

    Returns:
        Dict con estructura completa del endpoint
    """
    # Buscar información del usuario actual en los firmantes
    current_signer_info = _find_current_signer(signatures_grouped, user_id)

    # Determinar si el usuario puede firmar
    can_sign = _can_user_sign(current_signer_info, signatures_grouped)

    # Verificar si el documento tiene número oficial
    has_official_number = _has_official_number(document_id, schema_name=schema_name)

    # Verificar estado IA (solo para docs oficiales)
    has_embeddings = False
    if has_official_number:
        has_embeddings = _check_has_embeddings(document_id, schema_name=schema_name)

    # Generar mensaje específico según la situación del usuario
    user_message = _get_user_situation_message(
        user_id=user_id,
        document_info=document_info,
        current_signer_info=current_signer_info,
        signatures_grouped=signatures_grouped,
        has_official_number=has_official_number
    )

    # Construir department_sector si al menos uno existe
    dept_acronym = creator_info.get('department_acronym') if creator_info else None
    sector_acronym = creator_info.get('sector_acronym') if creator_info else None
    department_sector = None

    if dept_acronym or sector_acronym:
        dept_part = dept_acronym if dept_acronym else ""
        sector_part = sector_acronym if sector_acronym else ""
        department_sector = f"{dept_part}#{sector_part}" if (dept_part or sector_part) else None

    # Construir la información del documento
    document_section = {
        "document_id": document_info['document_id'],
        "reference": document_info.get('reference', ''),
        "status": document_info['status'],
        "document_type": {
            "name": document_info['document_type_name'],
            "acronym": document_info['document_type_acronym']
        },
        "created_by": document_info.get('created_by'),  # Para que frontend sepa quién es el creador
        "creator_name": creator_info.get('full_name', '') if creator_info else '',
        "creator_profile_picture_url": creator_info.get('profile_picture_url') if creator_info else None,
        "creator_department_sector": department_sector,
        "creator_seal_name": creator_info.get('seal_name') if creator_info else None,
        "created_at": document_info['last_modified_at'].isoformat() if hasattr(document_info['last_modified_at'], 'isoformat') and document_info['last_modified_at'] else None,
        "resume": document_info.get('resume'),
        "has_embeddings": has_embeddings
    }

    pdf_url = None
    if document_info.get('document_generate_id'):
        document_section["document_generate_id"] = document_info['document_generate_id']
        # Obtener la URL del PDF desde Cloudflare R2
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

    # Determinar si el usuario es solo sector_viewer (solo lectura)
    is_creator = user_id == document_info.get('created_by')
    is_sent_by = user_id == document_info.get('sent_by')
    is_signer = current_signer_info and current_signer_info.get('user_id')
    is_sector_viewer = not is_creator and not is_sent_by and not is_signer

    response = {
        "document": document_section,
        "current_signer": {
            "user_id": current_signer_info.get('user_id', user_id),
            "user_name": current_signer_info.get('full_name', ''),
            "email": current_signer_info.get('email', ''),
            "profile_picture_url": current_signer_info.get('profile_picture_url', None),
            "signing_order": current_signer_info.get('signing_order', 1),
            "is_numerator": current_signer_info.get('is_numerator', False),
            "already_signed": current_signer_info.get('has_signed', False),
            "seal_name": current_signer_info.get('seal_name', None)
        },
        "signature_progress": {
            "completed": signatures_grouped['completed_count'],
            "total": signatures_grouped['pending_count'] + signatures_grouped['completed_count'],
            "signatures": _format_signatures_for_progress(signatures_grouped)
        },
        "can_sign": can_sign,
        "is_sector_viewer": is_sector_viewer
    }

    # Agregar pdf_url al nivel raíz si existe
    if pdf_url:
        response["pdf_url"] = pdf_url

    # Agregar mensaje solo si existe
    if user_message:
        response["message"] = user_message

    # Agregar recipients si existen (para docs NOTA)
    if recipients:
        response["recipients"] = recipients

    # Agregar proposed_cases si existen
    if proposed_cases:
        response["proposed_cases"] = proposed_cases

    return response


# ============================================================================
# FUNCIONES AUXILIARES - Helper Functions
# ============================================================================

def _find_current_signer(signatures_grouped: Dict[str, Any], user_id: str) -> Dict[str, Any]:
    """
    Encuentra la información del firmante actual en los grupos de firmantes.

    Args:
        signatures_grouped: Firmantes agrupados por estado
        user_id: ID del usuario actual

    Returns:
        Dict con información del firmante actual o dict vacío si no se encuentra
    """
    # Buscar en firmantes pendientes
    for signer in signatures_grouped['pending']:
        if signer['user_id'] == user_id:
            return signer

    # Buscar en firmantes completados
    for signer in signatures_grouped['completed']:
        if signer['user_id'] == user_id:
            return signer

    return {}


def _can_user_sign(current_signer_info: Dict[str, Any], signatures_grouped: Dict[str, Any]) -> bool:
    """Determina si el usuario actual puede firmar el documento.

    Args:
        current_signer_info: Información del firmante actual
        signatures_grouped: Firmantes agrupados por estado

    Returns:
        bool: True si puede firmar, False si no
    """
    # Si no es firmante, no puede firmar
    if not current_signer_info:
        return False

    # Si ya firmó, no puede firmar de nuevo
    if current_signer_info.get('has_signed', False):
        return False

    # Verificar si es numerador
    is_numerator = current_signer_info.get('is_numerator', False)

    if is_numerator:
        # Si es numerador, verificar si hay otros firmantes pendientes
        other_pending = [s for s in signatures_grouped['pending'] if not s.get('is_numerator', False)]

        if other_pending:
            return False  # Debe esperar
        else:
            return True  # Es su turno
    else:
        # Si es firmante común, verificar si está en pendientes
        return current_signer_info in signatures_grouped['pending']


def _format_signatures_for_progress(signatures_grouped: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Formatea las firmas para el modelo de progreso.

    Args:
        signatures_grouped: Firmantes agrupados por estado

    Returns:
        Lista de firmantes formateada para el modelo SignerInfo
    """
    signatures = []

    # Añadir firmantes pendientes
    for signer in signatures_grouped['pending']:
        signatures.append({
            "user_id": signer['user_id'],
            "user_name": signer['full_name'],
            "email": "",
            "profile_picture_url": signer.get('profile_picture_url', None),
            "signing_order": 1,
            "is_numerator": signer.get('is_numerator', False),
            "has_signed": False,
            "signed_at": None,
            "is_current_user": False,
            "seal_name": signer.get('seal_name', None)
        })

    # Añadir firmantes completados
    for signer in signatures_grouped['completed']:
        signatures.append({
            "user_id": signer['user_id'],
            "user_name": signer['full_name'],
            "email": "",
            "profile_picture_url": signer.get('profile_picture_url', None),
            "signing_order": 1,
            "is_numerator": signer.get('is_numerator', False),
            "has_signed": True,
            "signed_at": None,
            "is_current_user": False,
            "seal_name": signer.get('seal_name', None)
        })

    return signatures
