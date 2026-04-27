"""
Servicios para la creacion de documentos.
Maneja la logica de negocio para crear nuevos documentos.

Ubicacion real: services/documents/lifecycle/creation.py
"""

from typing import Dict, Any, Optional
import uuid
from shared.logging import get_logger
from database import get_db_connection
from shared.exceptions import ValidationError, DatabaseError
from shared.audit_context import set_audit_context
from shared.validation import validate_document_type_acronym, validate_required_string, validate_uuid
from ..core.queries import (
    get_document_type_query,
    insert_document_draft_query,
    insert_document_signer_query
)
from services.notes.validation import (
    is_nota_document_type_by_acronym,
    validate_recipients_exist,
    validate_recipients_input
)
from services.notes.save_recipients import save_recipients
from services.memos.validation import (
    is_memo_document_type_by_acronym,
    validate_memo_recipients_exist,
    validate_memo_recipients_input
)
from services.memos.save_recipients import save_memo_recipients

logger = get_logger(__name__)


def _validate_creation_inputs(document_type_acronym: str, reference: str, creator_id: str) -> None:
    """Valida los inputs para creacion de documento."""
    errors = []

    type_error = validate_document_type_acronym(document_type_acronym)
    if type_error:
        errors.append(type_error)

    reference_error = validate_required_string(reference, "reference", min_length=1, max_length=250)
    if reference_error:
        errors.append(reference_error)

    # Solo validar formato UUID, NO existencia (ya validado por TenantMiddleware)
    if not validate_uuid(creator_id):
        errors.append("creator_id debe ser un UUID valido")

    if errors:
        raise ValidationError("; ".join(errors))


def _get_document_type(cursor, document_type_acronym: str) -> Dict[str, Any]:
    """Obtiene y valida el tipo de documento."""
    cursor.execute(get_document_type_query(), (document_type_acronym,))
    document_type = cursor.fetchone()

    if not document_type:
        raise ValidationError(f"Tipo de documento '{document_type_acronym}' no encontrado")

    return document_type


def _insert_document(cursor, document_id: str, document_type_id: str, reference: str, creator_id: str) -> Dict[str, Any]:
    """Inserta el documento en la base de datos."""
    cursor.execute(
        insert_document_draft_query(),
        (document_id, document_type_id, reference, creator_id)
    )
    return cursor.fetchone()


def _assign_creator_as_signer(cursor, document_id: str, creator_id: str) -> None:
    """Asigna al creador como firmante numerador."""
    cursor.execute(
        insert_document_signer_query(),
        (document_id, creator_id, 1, True)
    )


def create_document(
    document_type_acronym: str,
    reference: str,
    creator_id: str,
    *,
    schema_name: str,
    recipients: Optional[Dict[str, Any]] = None,
    sender_sector_id: Optional[str] = None,
    auth_source: Optional[str] = None
) -> Dict[str, Any]:
    """
    Crea un nuevo documento en estado draft.

    Args:
        document_type_acronym: Acronimo del tipo de documento
        reference: Referencia del documento
        creator_id: UUID del usuario creador
        schema_name: Schema del tenant (municipalidad)
        recipients: (Solo para NOTA) Dict con {to: [], cc: [], bcc: []}
        sender_sector_id: (Solo para NOTA) UUID del sector emisor
        auth_source: Origen de autenticación para trazabilidad (jwt, api_key, mcp_oauth, testing, system)

    Returns:
        Dict con document_id y datos del documento creado

    Raises:
        ValidationError: Si los datos son invalidos
        DatabaseError: Si falla la operacion en BD
    """
    logger.info(f"Creando documento tipo '{document_type_acronym}' para usuario {creator_id} en schema '{schema_name}'")

    _validate_creation_inputs(document_type_acronym, reference, creator_id)

    # Detectar si es NOTA o MEMO
    is_nota = is_nota_document_type_by_acronym(document_type_acronym, schema_name=schema_name)
    is_memo = is_memo_document_type_by_acronym(document_type_acronym, schema_name=schema_name)
    normalized_recipients = None

    # Validar recipients si es NOTA y se proporcionan
    if is_nota and recipients:
        # Si se proporcionan recipients al crear, validarlos
        if not sender_sector_id:
            raise ValidationError("Se requiere sender_sector_id para guardar recipients en NOTA")
        # Normalizar y validar estructura (incluye deduplicación)
        normalized_recipients = validate_recipients_input(recipients)
    elif is_nota:
        # NOTA sin recipients es válido - se pueden agregar después
        logger.info(f"Creando NOTA sin recipients iniciales (se pueden agregar al guardar)")
    elif is_memo and recipients:
        # MEMO: validar recipients (user_ids en vez de sector_ids)
        normalized_recipients = validate_memo_recipients_input(recipients)
    elif is_memo:
        # MEMO sin recipients es válido - se pueden agregar después
        logger.info(f"Creando MEMO sin recipients iniciales (se pueden agregar al guardar)")
    elif recipients:
        # Si no es NOTA ni MEMO pero se enviaron recipients, ignorar con warning
        logger.warning(f"Se enviaron recipients para documento tipo '{document_type_acronym}' que no es NOTA ni MEMO - ignorados")

    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            try:
                # Inyectar contexto de auditoría
                if creator_id or auth_source:
                    set_audit_context(cursor, user_id=creator_id, auth_source=auth_source)

                document_type = _get_document_type(cursor, document_type_acronym)
                document_id = str(uuid.uuid4())

                result = _insert_document(
                    cursor, document_id, document_type['document_type_id'],
                    reference, creator_id
                )

                _assign_creator_as_signer(cursor, document_id, creator_id)

                # Si es NOTA con recipients, guardarlos en la misma transacción
                recipients_count = 0
                if is_nota and normalized_recipients:
                    # Validar que los sectors existan (usa el mismo cursor)
                    validate_recipients_exist(
                        cursor, normalized_recipients, sender_sector_id,
                        schema_name=schema_name
                    )
                    # Guardar recipients
                    recipients_count = save_recipients(
                        cursor, document_id, sender_sector_id, normalized_recipients,
                        schema_name=schema_name
                    )

                # Si es MEMO con recipients, guardarlos en la misma transacción
                if is_memo and normalized_recipients:
                    # Validar que los users existan (usa el mismo cursor)
                    validate_memo_recipients_exist(
                        cursor, normalized_recipients, creator_id,
                        schema_name=schema_name
                    )
                    # Guardar recipients (sender_user_id = creator_id)
                    recipients_count = save_memo_recipients(
                        cursor, document_id, creator_id, normalized_recipients,
                        schema_name=schema_name
                    )

                conn.commit()

                logger.info(f"Documento {document_id} creado exitosamente" + (f" con {recipients_count} recipients" if (is_nota or is_memo) else ""))

                response = {
                    "success": True,
                    "document_id": result['document_id'],
                    "document_type_id": document_type['document_type_id'],
                    "document_type_name": document_type['name'],
                    "document_type_acronym": document_type_acronym,
                    "reference": reference,
                    "creator_id": creator_id,
                    "status": "draft",
                    "created_at": None,
                    "updated_at": result['last_modified_at'].isoformat() if result['last_modified_at'] else None,
                    "message": "Documento creado exitosamente"
                }

                # Agregar info de recipients si es NOTA
                if is_nota:
                    response["is_nota"] = True
                    response["recipients_count"] = recipients_count

                # Agregar info de recipients si es MEMO
                if is_memo:
                    response["is_memo"] = True
                    response["recipients_count"] = recipients_count

                return response

            except ValidationError:
                conn.rollback()
                raise
            except Exception as e:
                conn.rollback()
                logger.error(f"Error al crear documento: {str(e)}")
                raise DatabaseError(f"Error al crear documento: {str(e)}")
