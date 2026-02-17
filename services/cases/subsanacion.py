"""
Servicio de lógica de negocio para subsanación de documentos oficiales en expedientes

Este módulo maneja la subsanación de documentos oficiales vinculados a expedientes:
1. Desactiva el documento oficial erróneo
2. Vincula el documento oficial que justifica la subsanación

Arquitectura: Clean Architecture (Patrón A)
- Lógica de negocio centralizada
- Validaciones robustas
- Transacciones atómicas
"""

import uuid
from typing import Dict, Any
from database import get_db_connection, execute_query
from shared.exceptions import ValidationError, NotFoundError, AuthorizationError
from services.cases.permissions import get_user_editable_sector_ids

async def subsanar_document_service(
    case_id: str,
    official_document_id_erroneo: str,
    official_document_id_justifica: str,
    user_id: str,
    *,
    schema_name: str
) -> Dict[str, Any]:
    """
    Subsanar documento oficial en expediente

    Solo usuarios ADMIN del expediente pueden ejecutar esta acción.

    Proceso:
    1. Valida que el expediente existe
    2. Valida que el usuario tiene permisos ADMIN sobre el expediente
    3. Valida que el documento erróneo existe, está vinculado y activo
    4. Valida que el documento que justifica existe
    5. Valida que no hay duplicados
    6. En transacción atómica:
       a. Desactiva el documento erróneo (is_active=false, deactivated_at, deactivated_by_user_id)
       b. Vincula el documento que justifica (reutiliza link_official_document)

    Args:
        case_id: UUID del expediente
        official_document_id_erroneo: UUID del documento oficial a desactivar
        official_document_id_justifica: UUID del documento oficial correcto
        user_id: UUID del usuario que ejecuta la subsanación
        schema_name: Schema del tenant (obligatorio para multi-tenant)

    Returns:
        Dict con información de ambos documentos (desactivado y vinculado)

    Raises:
        NotFoundError: Si no se encuentra expediente, documento o usuario
        AuthorizationError: Si el usuario no tiene permisos ADMIN
        ValidationError: Si las validaciones de negocio fallan
    """

    # =================================================================
    # PASO 1: VALIDAR EXPEDIENTE EXISTE
    # =================================================================
    case_query = "SELECT id FROM cases WHERE id = %s"
    case_result = execute_query(case_query, (case_id,), schema_name=schema_name)

    if not case_result:
        raise NotFoundError(f"Expediente no encontrado: {case_id}")

    # =================================================================
    # PASO 2: VALIDAR PERMISOS ADMIN
    # =================================================================
    # Obtener sectores donde el usuario puede editar (principal + permissions con can_edit=true)
    user_sector_ids = get_user_editable_sector_ids(user_id, schema_name=schema_name)

    if not user_sector_ids:
        raise AuthorizationError("Usuario sin sectores asignados")

    # Obtener admin_sector del expediente (último movimiento cerrado creation/transfer)
    admin_sector_query = """
        SELECT s.id as sector_id
        FROM case_movements cm
        JOIN sectors s ON cm.admin_sector_id = s.id
        WHERE cm.case_id = %s
          AND cm.is_active = false
          AND cm.type IN ('creation', 'transfer')
        ORDER BY cm.closed_at DESC
        LIMIT 1
    """
    admin_sector_result = execute_query(admin_sector_query, (case_id,), schema_name=schema_name)

    if not admin_sector_result:
        raise NotFoundError("Expediente sin sector administrador")

    admin_sector_id = str(admin_sector_result[0]['sector_id'])

    # Verificar que usuario es ADMIN (mismo permiso que transfer)
    if admin_sector_id not in user_sector_ids:
        raise AuthorizationError("Solo el sector administrador puede subsanar documentos en este expediente")

    # =================================================================
    # PASO 3: VALIDAR DOCUMENTO ERRÓNEO
    # =================================================================
    doc_erroneo_query = """
        SELECT
            cod.id,
            cod.official_document_id,
            cod.is_active,
            od.official_number,
            od.reference
        FROM case_official_documents cod
        JOIN official_documents od ON cod.official_document_id = od.id
        WHERE cod.case_id = %s
          AND cod.official_document_id = %s
    """
    doc_erroneo_result = execute_query(
        doc_erroneo_query,
        (case_id, official_document_id_erroneo),
        schema_name=schema_name
    )

    if not doc_erroneo_result:
        raise NotFoundError("El documento erróneo no está vinculado a este expediente")

    doc_erroneo_data = doc_erroneo_result[0]

    # Verificar que el documento está activo (no subsanado previamente)
    if not doc_erroneo_data['is_active']:
        raise ValidationError(
            f"El documento {doc_erroneo_data['official_number']} ya fue subsanado previamente"
        )

    # =================================================================
    # PASO 4: VALIDAR DOCUMENTO QUE JUSTIFICA EXISTE
    # =================================================================
    doc_justifica_query = """
        SELECT id, official_number, reference
        FROM official_documents
        WHERE id = %s
    """
    doc_justifica_result = execute_query(
        doc_justifica_query,
        (official_document_id_justifica,),
        schema_name=schema_name
    )

    if not doc_justifica_result:
        raise NotFoundError("El documento que justifica no existe en el sistema")

    doc_justifica_data = doc_justifica_result[0]

    # =================================================================
    # PASO 5: VALIDAR NO DUPLICADO
    # =================================================================
    # El documento que justifica no debe estar ya vinculado al expediente
    duplicate_check = """
        SELECT 1
        FROM case_official_documents
        WHERE case_id = %s
          AND official_document_id = %s
          AND is_active = true
    """
    duplicate_result = execute_query(
        duplicate_check,
        (case_id, official_document_id_justifica),
        schema_name=schema_name
    )

    if duplicate_result:
        raise ValidationError(
            f"El documento {doc_justifica_data['official_number']} ya está vinculado al expediente"
        )

    # =================================================================
    # PASO 6: TRANSACCIÓN ATÓMICA
    # =================================================================
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            try:
                # 6a. DESACTIVAR documento erróneo
                deactivate_query = """
                    UPDATE case_official_documents
                    SET
                        is_active = false,
                        deactivated_at = NOW(),
                        deactivated_by_user_id = %s
                    WHERE id = %s
                    RETURNING deactivated_at
                """
                cursor.execute(
                    deactivate_query,
                    (user_id, doc_erroneo_data['id'])
                )
                deactivate_result = cursor.fetchone()
                deactivated_at = deactivate_result['deactivated_at']

                # 6b. VINCULAR documento que justifica
                # Inline de CaseService.link_official_document() para usar la misma conexión

                # Bloquear la fila del expediente para evitar race conditions
                cursor.execute("""
                    SELECT 1 FROM cases WHERE id = %s FOR UPDATE
                """, (case_id,))

                # Calcular el siguiente order_number
                cursor.execute("""
                    SELECT COALESCE(MAX(order_number), 0) as max_order
                    FROM case_official_documents
                    WHERE case_id = %s
                """, (case_id,))

                max_order_result = cursor.fetchone()
                next_order = max_order_result['max_order'] + 1 if max_order_result else 1

                # Insertar en case_official_documents con RETURNING
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
                """, (link_id, case_id, official_document_id_justifica, user_id, next_order))

                linking_result = cursor.fetchone()
                linking_date = linking_result['linking_date']

                # 6c. Registrar movimiento de document_link (por el documento que justifica)
                movement_link_id = str(uuid.uuid4())
                cursor.execute("""
                    INSERT INTO case_movements (
                        id, case_id, type, user_id,
                        creator_sector_id, admin_sector_id,
                        supporting_document_id,
                        reason, is_active, closed_at, closing_reason
                    ) VALUES (
                        %s, %s, 'document_link', %s, %s, %s, %s, %s, false, NOW(), 'Acción completada'
                    )
                """, (movement_link_id, case_id, user_id, admin_sector_id, admin_sector_id,
                      official_document_id_justifica, f"Vinculó documento: {doc_justifica_data['official_number']} ({doc_justifica_data['reference']})"))

                # 6d. Registrar movimiento de SUBSANACIÓN en historial
                subsanacion_movement_id = str(uuid.uuid4())
                subsanacion_reason = f"subsanó el documento {doc_erroneo_data['official_number']}, vinculando el documento {doc_justifica_data['official_number']}"

                cursor.execute("""
                    INSERT INTO case_movements (
                        id, case_id, type, user_id,
                        creator_sector_id, admin_sector_id,
                        reason, is_active, closed_at, closing_reason
                    ) VALUES (
                        %s, %s, 'subsanacion', %s, %s, %s, %s, false, NOW(), 'Acción completada'
                    )
                """, (subsanacion_movement_id, case_id, user_id, admin_sector_id, admin_sector_id, subsanacion_reason))

                # UN SOLO COMMIT al final de todas las operaciones
                conn.commit()

                # =================================================================
                # PASO 7: RETORNAR RESULTADO
                # =================================================================
                return {
                    "deactivated_document": {
                        "id": str(doc_erroneo_data['id']),
                        "official_number": doc_erroneo_data['official_number'],
                        "reference": doc_erroneo_data['reference'],
                        "deactivated_at": deactivated_at.isoformat() if deactivated_at else None,
                        "deactivated_by_user_id": user_id
                    },
                    "linked_document": {
                        "link_id": link_id,
                        "official_number": doc_justifica_data['official_number'],
                        "document_reference": doc_justifica_data['reference'],
                        "order_number": next_order,
                        "linking_date": linking_date.isoformat() if hasattr(linking_date, 'isoformat') else str(linking_date)
                    }
                }

            except (ValidationError, NotFoundError, AuthorizationError):
                # Re-raise excepciones de negocio
                conn.rollback()
                raise
            except Exception as e:
                conn.rollback()
                raise Exception(f"Error en transacción de subsanación: {str(e)}")
