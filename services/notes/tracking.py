"""
Servicios de tracking para NOTAS.
Registro de apertura y detalle de notas.
"""

from typing import Dict, Any, Optional
from shared.logging import get_logger
from shared.exceptions import NotFoundError, AuthorizationError
from database import get_db_connection
from .queries import (
    insert_note_opening_query,
    get_opening_by_document_user_query,
    get_openings_by_document_query,
    get_note_detail_query,
    check_user_is_recipient_query,
    check_user_is_sender_query,
    get_note_recipient_info_query
)
from .recipients import get_visible_recipients, check_sector_access
from services.documents.lifecycle.editing import _fetch_proposed_cases

logger = get_logger(__name__)


def record_opening(
    document_id: str,
    user_id: str,
    sector_id: str,
    *, schema_name: str
) -> Dict[str, Any]:
    """
    Registra la apertura de una nota por un usuario.
    Solo se registra una vez por usuario (idempotente).

    Args:
        document_id: UUID del documento
        user_id: UUID del usuario que abre
        sector_id: UUID del sector del usuario
        schema_name: Schema del tenant

    Returns:
        Dict con {
            recorded: bool (True si es primera apertura),
            opened_at: datetime (fecha de apertura)
        }
    """
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            # Verificar que el sector sea recipient (no sender)
            cursor.execute(check_user_is_recipient_query(), (document_id, sector_id))
            recipient_result = cursor.fetchone()

            if not recipient_result:
                # No es recipient, verificar si es sender
                cursor.execute(check_user_is_sender_query(), (document_id, sector_id))
                is_sender_result = cursor.fetchone()
                if is_sender_result and is_sender_result['is_sender']:
                    # Es sender, no registrar apertura
                    logger.debug(f"[{schema_name}] Sender {sector_id} abrió nota {document_id}, no se registra")
                    return {'recorded': False, 'opened_at': None, 'reason': 'sender'}
                else:
                    raise AuthorizationError("No tienes acceso a esta nota")

            # Intentar insertar (ON CONFLICT ignora si ya existe)
            cursor.execute(insert_note_opening_query(), (document_id, sector_id, user_id))
            result = cursor.fetchone()

            if result:
                # Primera apertura
                conn.commit()
                logger.info(f"[{schema_name}] Usuario {user_id} abrió nota {document_id} por primera vez")
                # Obtener timestamp
                cursor.execute(get_opening_by_document_user_query(), (document_id, user_id))
                opening = cursor.fetchone()
                return {
                    'recorded': True,
                    'opened_at': opening['opened_at'].isoformat() if opening else None
                }
            else:
                # Ya había sido abierta
                conn.commit()
                cursor.execute(get_opening_by_document_user_query(), (document_id, user_id))
                opening = cursor.fetchone()
                logger.debug(f"[{schema_name}] Usuario {user_id} reabrió nota {document_id}")
                return {
                    'recorded': False,
                    'opened_at': opening['opened_at'].isoformat() if opening else None
                }


def get_note_detail(
    document_id: str,
    requesting_user_id: str,
    requesting_sector_id: str,
    *,
    register_opening: bool = True,
    schema_name: str
) -> Dict[str, Any]:
    """
    Obtiene el detalle completo de una nota.
    Registra la apertura si es recipient y no es sender (controlado por register_opening).

    Args:
        document_id: UUID del documento
        requesting_user_id: UUID del usuario que solicita
        requesting_sector_id: UUID del sector del usuario
        register_opening: Si True, registra la apertura (default True para backward compat)
        schema_name: Schema del tenant

    Returns:
        Dict con detalle completo de la nota

    Raises:
        NotFoundError: Si la nota no existe
        AuthorizationError: Si el usuario no tiene acceso
    """
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            # Obtener detalle de la nota
            cursor.execute(get_note_detail_query(), (document_id,))
            note = cursor.fetchone()

            if not note:
                raise NotFoundError(f"Nota {document_id} no encontrada")

    # Verificar acceso y obtener recipients visibles
    recipients = get_visible_recipients(document_id, requesting_sector_id, schema_name=schema_name)

    # Registrar apertura si corresponde y está habilitado
    if register_opening:
        opening_result = record_opening(
            document_id, requesting_user_id, requesting_sector_id,
            schema_name=schema_name
        )
    else:
        # Usuario con solo can_view - no registrar apertura
        opening_result = {'recorded': False, 'opened_at': None, 'reason': 'view_only'}

    # Obtener aperturas si es sender
    openings = None
    if recipients['is_sender']:
        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                cursor.execute(get_openings_by_document_query(), (document_id,))
                openings_raw = cursor.fetchall()
                openings = [
                    {
                        'sector_id': str(o['sector_id']),
                        'sector_acronym': o['sector_acronym'],
                        'user_id': str(o['user_id']),
                        'user_name': o['user_name'],
                        'opened_at': o['opened_at'].isoformat() if o['opened_at'] else None
                    }
                    for o in openings_raw
                ]

    # Obtener estado de archivado si es recipient
    is_archived = False
    archived_at = None
    if not recipients['is_sender']:
        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                cursor.execute(get_note_recipient_info_query(), (document_id, requesting_sector_id))
                recipient_info = cursor.fetchone()
                if recipient_info:
                    is_archived = recipient_info['is_archived']
                    archived_at = recipient_info['archived_at'].isoformat() if recipient_info['archived_at'] else None

    # Construir respuesta
    result = {
        'document_id': str(note['id']),
        'official_number': note['official_number'],
        'reference': note['reference'],
        'content': note['content'],
        'signed_at': note['signed_at'].isoformat() if note['signed_at'] else None,
        'ai_summary': note['ai_summary'],
        'signers': note['signers'],
        'document_type': {
            'name': note['document_type_name'],
            'acronym': note['document_type_acronym']
        },
        'department_name': note['department_name'],
        'recipients': recipients,
        'my_access': {
            'is_sender': recipients['is_sender'],
            'recipient_type': recipients['my_recipient_type'],
            'sector_id': requesting_sector_id,
            'first_open': opening_result['recorded'] if opening_result else False,
            'opened_at': opening_result['opened_at'] if opening_result else None,
            'is_archived': is_archived,
            'archived_at': archived_at
        }
    }

    # Incluir openings solo si es sender
    if openings is not None:
        result['openings'] = openings

    # Incluir expedientes propuestos
    try:
        proposed_cases = _fetch_proposed_cases(document_id, schema_name=schema_name)
        if proposed_cases:
            result['proposed_cases'] = proposed_cases
    except Exception as e:
        logger.warning(f"[{schema_name}] Error al obtener proposed_cases para nota {document_id}: {e}")

    logger.info(
        f"[{schema_name}] Nota {document_id} accedida por sector {requesting_sector_id} "
        f"(is_sender={recipients['is_sender']}, type={recipients['my_recipient_type']})"
    )

    return result


def get_note_detail_multi_sector(
    document_id: str,
    requesting_user_id: str,
    user_permissions: list[dict],
    *, schema_name: str
) -> Dict[str, Any]:
    """
    Obtiene el detalle de una nota verificando acceso en múltiples sectores.

    Busca el primer sector con can_view que tenga acceso (sender o recipient).
    Registra apertura si el usuario tiene can_view (es suficiente para leer).

    Args:
        document_id: UUID del documento
        requesting_user_id: UUID del usuario que solicita
        user_permissions: Lista de permisos del usuario [{sector_id, can_view, can_edit}, ...]
        schema_name: Schema del tenant

    Returns:
        Dict con detalle completo de la nota

    Raises:
        NotFoundError: Si la nota no existe
        AuthorizationError: Si ningún sector del usuario tiene acceso
    """
    # Filtrar solo sectores con can_view
    viewable_permissions = [p for p in user_permissions if p.get('can_view')]

    if not viewable_permissions:
        raise AuthorizationError(
            "No tienes sectores con permiso de visualización."
        )

    # Buscar el primer sector que tenga acceso a la nota
    access_sector_id = None
    can_register_opening = False

    for perm in viewable_permissions:
        sector_id = perm['sector_id']
        access = check_sector_access(document_id, sector_id, schema_name=schema_name)

        if access['has_access']:
            access_sector_id = sector_id
            # Registrar apertura si tiene can_view (suficiente para lectura)
            can_register_opening = perm.get('can_view', False)
            logger.debug(
                f"[{schema_name}] Acceso encontrado para nota {document_id} via sector {sector_id} "
                f"(can_view={can_register_opening})"
            )
            break

    if not access_sector_id:
        raise AuthorizationError(
            "No tienes acceso a esta nota. Solo el emisor y los destinatarios pueden verla."
        )

    # Obtener detalle usando el sector con acceso
    return get_note_detail(
        document_id=document_id,
        requesting_user_id=requesting_user_id,
        requesting_sector_id=access_sector_id,
        register_opening=can_register_opening,
        schema_name=schema_name
    )
