
from typing import Dict, Any, Optional
import uuid
from shared.logging import get_logger
from database import get_conn
from shared.exceptions import ValidationError, DatabaseError
from shared.validation import validate_document_type_acronym, validate_required_string, validate_uuid
from ..core.queries import (
    get_document_type_query,
    insert_document_draft_query,
    insert_document_signer_query,
    insert_document_draft_citizen_query,
    insert_document_signer_citizen_query,
)
from services.notes.validation import (
    validate_recipients_exist,
    validate_recipients_input
)
from services.notes.save_recipients import save_recipients
from services.memos.validation import (
    validate_memo_recipients_exist,
    validate_memo_recipients_input
)
from services.memos.save_recipients import save_memo_recipients

logger = get_logger(__name__)


def _validate_creation_inputs(
    document_type_acronym: str, reference: str, creator_id: Optional[str], citizen_id: Optional[str] = None
) -> None:
    errors = []

    type_error = validate_document_type_acronym(document_type_acronym)
    if type_error:
        errors.append(type_error)

    reference_error = validate_required_string(reference, "reference", min_length=1, max_length=250)
    if reference_error:
        errors.append(reference_error)

    if bool(creator_id) == bool(citizen_id):
        errors.append("Se requiere exactamente uno de creator_id o citizen_id")
    elif creator_id is not None and not validate_uuid(creator_id):
        errors.append("creator_id debe ser un UUID valido")
    elif citizen_id is not None and not validate_uuid(citizen_id):
        errors.append("citizen_id debe ser un UUID valido")

    if errors:
        raise ValidationError("; ".join(errors))


async def create_document(
    document_type_acronym: str,
    reference: str,
    creator_id: Optional[str] = None,
    *,
    schema_name: str,
    recipients: Optional[Dict[str, Any]] = None,
    sender_sector_id: Optional[str] = None,
    auth_source: Optional[str] = None,
    citizen_id: Optional[str] = None,
) -> Dict[str, Any]:
    actor_id = creator_id or citizen_id
    logger.info(f"Creando documento tipo '{document_type_acronym}' para actor {actor_id} en schema '{schema_name}'")

    _validate_creation_inputs(document_type_acronym, reference, creator_id, citizen_id)

    normalized_recipients = None

    try:
        async with get_conn(schema_name=schema_name, user_id=actor_id, auth_source=auth_source) as conn:
            async with conn.transaction():
                doc_type_row = await conn.fetchrow(get_document_type_query(), document_type_acronym)
                if not doc_type_row:
                    raise ValidationError(f"Tipo de documento '{document_type_acronym}' no encontrado")

                is_nota = (doc_type_row.get('base_type') or '').upper() == 'NOTA'
                is_memo = (doc_type_row.get('base_type') or '').upper() == 'MEMO'

                if is_nota and recipients:
                    if not sender_sector_id:
                        raise ValidationError("Se requiere sender_sector_id para guardar recipients en NOTA")
                    normalized_recipients = validate_recipients_input(recipients)
                elif is_nota:
                    logger.info(f"Creando NOTA sin recipients iniciales (se pueden agregar al guardar)")
                elif is_memo and recipients:
                    normalized_recipients = validate_memo_recipients_input(recipients)
                elif is_memo:
                    logger.info(f"Creando MEMO sin recipients iniciales (se pueden agregar al guardar)")
                elif recipients:
                    logger.warning(f"Se enviaron recipients para documento tipo '{document_type_acronym}' que no es NOTA ni MEMO - ignorados")

                document_id = str(uuid.uuid4())

                if citizen_id:
                    result_row = await conn.fetchrow(
                        insert_document_draft_citizen_query(),
                        document_id, doc_type_row['document_type_id'], reference, citizen_id
                    )
                    await conn.execute(
                        insert_document_signer_citizen_query(),
                        document_id, citizen_id, 1, True
                    )
                else:
                    result_row = await conn.fetchrow(
                        insert_document_draft_query(),
                        document_id, doc_type_row['document_type_id'], reference, creator_id
                    )
                    await conn.execute(
                        insert_document_signer_query(),
                        document_id, creator_id, 1, True
                    )

                recipients_count = 0
                if is_nota and normalized_recipients:
                    await validate_recipients_exist(
                        conn, normalized_recipients, sender_sector_id,
                        schema_name=schema_name
                    )
                    recipients_count = await save_recipients(
                        conn, document_id, sender_sector_id, normalized_recipients,
                        schema_name=schema_name
                    )

                if is_memo and normalized_recipients:
                    await validate_memo_recipients_exist(
                        conn, normalized_recipients, creator_id,
                        schema_name=schema_name
                    )
                    recipients_count = await save_memo_recipients(
                        conn, document_id, creator_id, normalized_recipients,
                        schema_name=schema_name
                    )

        logger.info(f"Documento {document_id} creado exitosamente" + (f" con {recipients_count} recipients" if (is_nota or is_memo) else ""))

        response = {
            "success": True,
            "document_id": result_row['document_id'],
            "document_type_id": doc_type_row['document_type_id'],
            "document_type_name": doc_type_row['name'],
            "document_type_acronym": document_type_acronym,
            "reference": reference,
            "creator_id": creator_id,
            "citizen_id": citizen_id,
            "status": "draft",
            "created_at": None,
            "updated_at": result_row['last_modified_at'].isoformat() if result_row['last_modified_at'] else None,
            "message": "Documento creado exitosamente"
        }

        if is_nota:
            response["is_nota"] = True
            response["recipients_count"] = recipients_count

        if is_memo:
            response["is_memo"] = True
            response["recipients_count"] = recipients_count

        return response

    except ValidationError:
        raise
    except Exception as e:
        logger.error(f"Error al crear documento: {str(e)}")
        raise DatabaseError(f"Error al crear documento: {str(e)}")
