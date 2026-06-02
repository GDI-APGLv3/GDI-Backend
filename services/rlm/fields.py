"""
Servicio para campos enriquecidos del modulo RLM.
Maneja actualizacion y verificacion de campos individuales.

Usa jsonb_set atomico + SELECT FOR UPDATE para evitar race conditions
cuando dos usuarios editan campos distintos del mismo legajo.
"""

from datetime import datetime
from typing import Optional, Any
from shared.logging import get_logger
from database import fetch_one, execute, transaction, check_document_exists
from shared.exceptions import ValidationError, NotFoundError, AuthorizationError
from services.rlm.queries import (
    get_record_detail_query,
    get_record_detail_for_update_query,
    update_record_field_atomic_query,
    get_user_sector_info_query,
    insert_history_query,
)
from services.rlm.validation import validate_field_update, calculate_next_expiration
from services.rlm.permissions import check_permission

logger = get_logger(__name__)


async def update_field(
    record_id: str,
    field_name: str,
    user_id: str,
    value: Any = None,
    expiration_date: Optional[str] = None,
    document_id: Optional[str] = None,
    notes: Optional[str] = None,
    document_reference: Optional[str] = None,
    document_resume: Optional[str] = None,
    *,
    schema_name: str
) -> dict:
    """
    Actualiza un campo enriquecido especifico de un legajo.

    Usa SELECT FOR UPDATE + jsonb_set para atomicidad. Dos usuarios
    pueden editar campos distintos del mismo legajo sin pisarse.

    Args:
        record_id: UUID del legajo
        field_name: Nombre del campo a actualizar
        user_id: UUID del usuario
        value: Nuevo valor del campo
        expiration_date: Fecha de vencimiento (ISO 8601)
        document_id: ID del documento vinculado
        notes: Notas
        schema_name: Schema del tenant

    Returns:
        Dict con el campo actualizado

    Raises:
        NotFoundError, AuthorizationError, ValidationError
    """
    # 1. Lectura sin lock para validaciones rapidas (permisos, schema)
    record = await fetch_one(
        get_record_detail_query(),
        record_id,
        schema_name=schema_name,
    )

    if not record:
        raise NotFoundError(f"Legajo con ID '{record_id}' no encontrado")

    # 2. Verificar permiso can_edit
    if not await check_permission(record["registry_family_id"], user_id, "can_edit", schema_name=schema_name):
        raise AuthorizationError("No tiene permisos para editar este legajo")

    # 3. Validar campo contra schema
    data_schema = record.get("data_schema") or {}
    validate_field_update(field_name, {}, data_schema)

    # 4. Obtener info del usuario para historial (antes de la transaccion)
    user_info = await fetch_one(
        get_user_sector_info_query(),
        user_id,
        schema_name=schema_name,
    )

    # 5. Transaccion atomica: SELECT FOR UPDATE + jsonb_set + historial
    async with transaction(schema_name=schema_name, user_id=user_id) as conn:
        # 5a. Lockear la fila y obtener datos frescos
        locked_record = await conn.fetchrow(
            get_record_detail_for_update_query(),
            record_id
        )

        if not locked_record:
            raise NotFoundError(f"Legajo con ID '{record_id}' no encontrado")

        # 5b. Leer campo actual desde datos lockeados (frescos)
        current_data = locked_record.get("data") or {}
        old_field = current_data.get(field_name, {})

        # 5c. Construir nuevo campo
        new_field = dict(old_field) if isinstance(old_field, dict) else {}
        if value is not None:
            new_field["value"] = value
        if expiration_date is not None:
            new_field["expiration_date"] = expiration_date
        if document_id is not None:
            new_field["document_id"] = document_id
        if notes is not None:
            new_field["notes"] = notes
        if document_reference is not None:
            new_field["document_reference"] = document_reference
        if document_resume is not None:
            new_field["document_resume"] = document_resume

        # 5d. Calcular next_expiration con datos frescos + nuevo campo
        temp_data = dict(current_data)
        temp_data[field_name] = new_field
        next_exp = calculate_next_expiration(temp_data, data_schema)

        # 5e. UPDATE atomico con jsonb_set (solo toca el campo modificado)
        jsonb_path = '{' + field_name + '}'
        result = await conn.fetchrow(
            update_record_field_atomic_query(),
            jsonb_path, new_field, next_exp, record_id
        )

        # 5f. Registrar en historial (misma transaccion)
        await conn.execute(
            insert_history_query(),
            record_id,
            "field_updated",
            field_name,
            old_field if old_field else None,
            new_field,
            user_id,
            user_info.get("sector_id") if user_info else None,
        )

    logger.info(f"Field '{field_name}' updated on record {record_id[:8]}")

    return {
        "field_name": field_name,
        "field_data": new_field,
        "next_expiration": str(result["next_expiration"]) if result["next_expiration"] else None,
        "updated_at": str(result["updated_at"]),
    }


async def verify_field(
    record_id: str,
    field_name: str,
    user_id: str,
    document_id: str,
    notes: Optional[str] = None,
    *,
    schema_name: str
) -> dict:
    """
    Marca un campo como verificado.

    Usa SELECT FOR UPDATE + jsonb_set para atomicidad.

    Args:
        record_id: UUID del legajo
        field_name: Nombre del campo a verificar
        user_id: UUID del usuario verificador
        document_id: UUID del documento oficial que respalda la verificacion
        notes: Notas de verificacion
        schema_name: Schema del tenant

    Returns:
        Dict con el campo verificado

    Raises:
        NotFoundError, AuthorizationError, ValidationError
    """
    # 1. Validar que el documento de respaldo existe
    if not await check_document_exists(document_id, schema_name=schema_name):
        raise NotFoundError(f"Documento de respaldo con ID '{document_id}' no encontrado")

    # 2. Lectura sin lock para validaciones rapidas
    record = await fetch_one(
        get_record_detail_query(),
        record_id,
        schema_name=schema_name,
    )

    if not record:
        raise NotFoundError(f"Legajo con ID '{record_id}' no encontrado")

    # 3. Verificar permiso can_verify
    if not await check_permission(record["registry_family_id"], user_id, "can_verify", schema_name=schema_name):
        raise AuthorizationError("No tiene permisos para verificar campos en este legajo")

    # 4. Validar que el campo existe y tiene has_verification
    data_schema = record.get("data_schema") or {}
    if field_name not in data_schema:
        raise ValidationError(f"El campo '{field_name}' no existe")

    if not data_schema[field_name].get("has_verification"):
        raise ValidationError(f"El campo '{field_name}' no admite verificacion")

    # 5. Obtener info del usuario (antes de la transaccion)
    user_info = await fetch_one(
        get_user_sector_info_query(),
        user_id,
        schema_name=schema_name,
    )

    # 6. Transaccion atomica: SELECT FOR UPDATE + jsonb_set + historial
    async with transaction(schema_name=schema_name, user_id=user_id) as conn:
        # 6a. Lockear la fila y obtener datos frescos
        locked_record = await conn.fetchrow(
            get_record_detail_for_update_query(),
            record_id
        )

        if not locked_record:
            raise NotFoundError(f"Legajo con ID '{record_id}' no encontrado")

        # 6b. Leer campo actual desde datos lockeados (frescos)
        current_data = locked_record.get("data") or {}
        old_field = current_data.get(field_name, {})

        # 6c. Construir campo con verificacion
        new_field = dict(old_field) if isinstance(old_field, dict) else {}
        new_field["verified"] = True
        new_field["verified_at"] = datetime.now().isoformat()
        new_field["verified_by"] = user_id
        new_field["verified_by_name"] = user_info.get("full_name", "") if user_info else ""
        new_field["verified_document_id"] = document_id

        # Fetch document info for display (dentro de la transaccion usando conn)
        doc_info = await conn.fetchrow(
            """SELECT COALESCE(official_number, '') as official_number,
                      COALESCE(resume, '') as resume
               FROM official_documents WHERE id = $1 AND signed_at IS NOT NULL""",
            document_id
        )
        if doc_info:
            new_field["verified_document_number"] = doc_info.get("official_number", "")
            new_field["verified_document_resume"] = doc_info.get("resume", "")
        if notes:
            new_field["verification_notes"] = notes

        # 6d. Calcular next_expiration con datos frescos + campo verificado
        temp_data = dict(current_data)
        temp_data[field_name] = new_field
        next_exp = calculate_next_expiration(temp_data, data_schema)

        # 6e. UPDATE atomico con jsonb_set
        jsonb_path = '{' + field_name + '}'
        result = await conn.fetchrow(
            update_record_field_atomic_query(),
            jsonb_path, new_field, next_exp, record_id
        )

        # 6f. Registrar en historial (misma transaccion)
        await conn.execute(
            insert_history_query(),
            record_id,
            "field_verified",
            field_name,
            old_field if old_field else None,
            new_field,
            user_id,
            user_info.get("sector_id") if user_info else None,
        )

    logger.info(f"Field '{field_name}' verified on record {record_id[:8]}")

    return {
        "field_name": field_name,
        "field_data": new_field,
        "updated_at": str(result["updated_at"]),
    }
