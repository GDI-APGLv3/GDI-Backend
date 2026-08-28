
from typing import Dict, Any
from database import fetch_all, fetch_one
from shared.exceptions import ValidationError, UserNotFoundError
from shared.validation import validate_user_id

async def get_user_statistics(user_id: str, *, schema_name: str) -> Dict[str, Any]:
    user_error = await validate_user_id(user_id, schema_name=schema_name)
    if user_error:
        raise ValidationError(user_error)

    user = await fetch_one(
        "SELECT id, first_name, last_name FROM users WHERE id = $1",
        user_id,
        schema_name=schema_name
    )

    if not user:
        raise UserNotFoundError(user_id)

    created_stats = await fetch_one(
        """
        SELECT
            COUNT(*) as total_created,
            COUNT(CASE WHEN status = 'draft' THEN 1 END) as drafts,
            COUNT(CASE WHEN status = 'sent_to_sign' THEN 1 END) as pending,
            COUNT(CASE WHEN status = 'signed' THEN 1 END) as signed,
            COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed,
            COUNT(CASE WHEN status = 'rejected' THEN 1 END) as rejected
        FROM documents WHERE creator_id = $1
        """,
        user_id,
        schema_name=schema_name
    )

    signing_stats = await fetch_one(
        """
        SELECT
            COUNT(ds.*) as assigned_to_sign,
            COUNT(dsig.*) as already_signed,
            COUNT(ds.*) - COUNT(dsig.*) as pending_signature
        FROM document_signers ds
        LEFT JOIN document_signatures dsig ON ds.document_id = dsig.document_id
                                          AND ds.user_id = dsig.user_id
        WHERE ds.user_id = $1
        """,
        user_id,
        schema_name=schema_name
    )

    numeration_stats = await fetch_one(
        """
        SELECT
            COUNT(ds.*) as assigned_to_numerate,
            COUNT(dn.*) as numerations_completed
        FROM document_signers ds
        LEFT JOIN document_numerations dn ON ds.document_id = dn.document_id
                                          AND ds.user_id = dn.numerator_id
        WHERE ds.user_id = $1 AND ds.is_numerator = true
        """,
        user_id,
        schema_name=schema_name
    )

    recent_activity = await fetch_one(
        """
        SELECT
            COUNT(CASE WHEN d.created_at >= CURRENT_DATE - INTERVAL '30 days' THEN 1 END) as documents_created_30d,
            COUNT(CASE WHEN dsig.signed_at >= CURRENT_DATE - INTERVAL '30 days' THEN 1 END) as documents_signed_30d
        FROM documents d
        FULL OUTER JOIN document_signatures dsig ON dsig.user_id = $1
        WHERE d.creator_id = $2 OR dsig.user_id = $3
        """,
        user_id, user_id, user_id,
        schema_name=schema_name
    )

    return {
        "user_info": {
            "user_id": user_id,
            "full_name": f"{user['first_name']} {user['last_name']}"
        },
        "documents_created": {
            "total": created_stats['total_created'],
            "by_status": {
                "draft": created_stats['drafts'],
                "pending_signature": created_stats['pending'],
                "signed": created_stats['signed'],
                "completed": created_stats['completed'],
                "rejected": created_stats['rejected']
            }
        },
        "documents_signing": {
            "assigned_to_sign": signing_stats['assigned_to_sign'],
            "already_signed": signing_stats['already_signed'],
            "pending_signature": signing_stats['pending_signature']
        },
        "numeration": {
            "assigned_to_numerate": numeration_stats['assigned_to_numerate'],
            "numerations_completed": numeration_stats['numerations_completed']
        },
        "recent_activity": {
            "documents_created_last_30d": recent_activity['documents_created_30d'],
            "documents_signed_last_30d": recent_activity['documents_signed_30d']
        }
    }

async def get_user_document_activity(user_id: str, schema_name: str, days: int = 30) -> Dict[str, Any]:
    user_error = await validate_user_id(user_id, schema_name=schema_name)
    if user_error:
        raise ValidationError(user_error)

    created_docs = await fetch_all(
        """
        SELECT d.id, d.reference, d.status, d.created_at,
               dt.name as document_type_name
        FROM documents d
        JOIN document_types dt ON d.document_type_id = dt.id
        WHERE d.creator_id = $1
          AND d.created_at >= CURRENT_DATE - ($2 * INTERVAL '1 day')
        ORDER BY d.created_at DESC
        LIMIT 10
        """,
        user_id, days,
        schema_name=schema_name
    )

    signed_docs = await fetch_all(
        """
        SELECT d.id, d.reference, d.status, dsig.signed_at,
               dt.name as document_type_name,
               ds.is_numerator
        FROM document_signatures dsig
        JOIN documents d ON dsig.document_id = d.id
        JOIN document_types dt ON d.document_type_id = dt.id
        JOIN document_signers ds ON d.id = ds.document_id AND ds.user_id = dsig.user_id
        WHERE dsig.user_id = $1
          AND dsig.signed_at >= CURRENT_DATE - ($2 * INTERVAL '1 day')
        ORDER BY dsig.signed_at DESC
        LIMIT 10
        """,
        user_id, days,
        schema_name=schema_name
    )

    pending_docs = await fetch_all(
        """
        SELECT d.id, d.reference, d.status, d.updated_at,
               dt.name as document_type_name,
               ds.signing_order, ds.is_numerator
        FROM document_signers ds
        JOIN documents d ON ds.document_id = d.id
        JOIN document_types dt ON d.document_type_id = dt.id
        LEFT JOIN document_signatures dsig ON d.id = dsig.document_id AND ds.user_id = dsig.user_id
        WHERE ds.user_id = $1
          AND dsig.id IS NULL
          AND d.status IN ('sent_to_sign', 'signed')
        ORDER BY d.updated_at DESC
        LIMIT 10
        """,
        user_id,
        schema_name=schema_name
    )

    created_formatted = []
    for doc in created_docs:
        created_formatted.append({
            "document_id": str(doc['id']),
            "reference": doc['reference'],
            "document_type": doc['document_type_name'],
            "status": doc['status'],
            "created_at": doc['created_at'].isoformat() if doc['created_at'] else None
        })

    signed_formatted = []
    for doc in signed_docs:
        signed_formatted.append({
            "document_id": str(doc['id']),
            "reference": doc['reference'],
            "document_type": doc['document_type_name'],
            "status": doc['status'],
            "signed_as": "numerator" if doc['is_numerator'] else "signer",
            "signed_at": doc['signed_at'].isoformat() if doc['signed_at'] else None
        })

    pending_formatted = []
    for doc in pending_docs:
        pending_formatted.append({
            "document_id": str(doc['id']),
            "reference": doc['reference'],
            "document_type": doc['document_type_name'],
            "status": doc['status'],
            "role": "numerator" if doc['is_numerator'] else "signer",
            "signing_order": doc['signing_order'],
            "updated_at": doc['updated_at'].isoformat() if doc['updated_at'] else None
        })

    return {
        "user_id": user_id,
        "period_days": days,
        "activity": {
            "documents_created": created_formatted,
            "documents_signed": signed_formatted,
            "documents_pending": pending_formatted
        },
        "summary": {
            "created_count": len(created_formatted),
            "signed_count": len(signed_formatted),
            "pending_count": len(pending_formatted)
        }
    }

async def get_users_with_roles(schema_name: str) -> Dict[str, Any]:
    users_data = await fetch_all(
        """
        SELECT DISTINCT
            u.id,
            u.first_name,
            u.last_name,
            u.email,
            u.is_active,
            u.position,
            d.name as department_name,
            d.code as department_code,
            -- Verificar si puede numerar documentos
            (SELECT COUNT(*) > 0 FROM document_signers ds
             WHERE ds.user_id = u.id AND ds.is_numerator = true) as can_numerate,
            -- Contar documentos creados
            (SELECT COUNT(*) FROM documents doc WHERE doc.creator_id = u.id) as documents_created,
            -- Contar documentos firmados
            (SELECT COUNT(*) FROM document_signatures dsig WHERE dsig.user_id = u.id) as documents_signed
        FROM users u
        LEFT JOIN departments d ON u.department_id = d.id
        WHERE u.is_active = true
        ORDER BY u.first_name, u.last_name
        """,
        schema_name=schema_name
    )

    creators = []
    signers = []
    numerators = []
    inactive_users = []

    for user in users_data:
        user_info = {
            "user_id": str(user['id']),
            "first_name": user['first_name'],
            "last_name": user['last_name'],
            "full_name": f"{user['first_name']} {user['last_name']}",
            "email": user['email'],
            "position": user['position'],
            "department": {
                "name": user['department_name'],
                "code": user['department_code']
            } if user['department_name'] else None,
            "activity": {
                "documents_created": user['documents_created'],
                "documents_signed": user['documents_signed']
            }
        }

        if not user['is_active']:
            inactive_users.append(user_info)
            continue

        if user['documents_created'] > 0:
            creators.append(user_info)

        if user['documents_signed'] > 0:
            signers.append(user_info)

        if user['can_numerate']:
            numerators.append({
                **user_info,
                "can_numerate": True
            })

    return {
        "users_by_role": {
            "creators": creators,
            "signers": signers,
            "numerators": numerators,
            "inactive": inactive_users
        },
        "summary": {
            "total_active_users": len([u for u in users_data if u['is_active']]),
            "creators_count": len(creators),
            "signers_count": len(signers),
            "numerators_count": len(numerators),
            "inactive_count": len(inactive_users)
        }
    }

async def get_department_users_summary(schema_name: str) -> Dict[str, Any]:
    departments_data = await fetch_all(
        """
        SELECT
            d.id as department_id,
            d.name as department_name,
            d.code as department_code,
            d.description as department_description,
            COUNT(u.id) as total_users,
            COUNT(CASE WHEN u.is_active = true THEN 1 END) as active_users,
            -- Usuarios que han creado documentos
            COUNT(CASE WHEN doc_count.created_count > 0 THEN 1 END) as users_with_documents,
            -- Usuarios que pueden numerar
            COUNT(CASE WHEN num_count.numerator_count > 0 THEN 1 END) as numerator_users
        FROM departments d
        LEFT JOIN users u ON d.id = u.department_id
        LEFT JOIN (
            SELECT creator_id, COUNT(*) as created_count
            FROM documents
            GROUP BY creator_id
        ) doc_count ON u.id = doc_count.creator_id
        LEFT JOIN (
            SELECT user_id, COUNT(*) as numerator_count
            FROM document_signers
            WHERE is_numerator = true
            GROUP BY user_id
        ) num_count ON u.id = num_count.user_id
        GROUP BY d.id, d.name, d.code, d.description
        HAVING COUNT(u.id) > 0
        ORDER BY d.name
        """,
        schema_name=schema_name
    )

    departments = []
    total_users = 0
    total_active = 0

    for dept in departments_data:
        departments.append({
            "department_id": str(dept['department_id']),
            "name": dept['department_name'],
            "code": dept['department_code'],
            "description": dept['department_description'],
            "user_stats": {
                "total_users": dept['total_users'],
                "active_users": dept['active_users'],
                "inactive_users": dept['total_users'] - dept['active_users'],
                "users_with_documents": dept['users_with_documents'],
                "numerator_users": dept['numerator_users']
            }
        })

        total_users += dept['total_users']
        total_active += dept['active_users']

    return {
        "departments": departments,
        "summary": {
            "total_departments": len(departments),
            "total_users": total_users,
            "total_active_users": total_active,
            "total_inactive_users": total_users - total_active
        }
    }

async def validate_user_permissions(user_id: str, schema_name: str, permission_type: str, resource_id: str = None) -> Dict[str, Any]:
    user_error = await validate_user_id(user_id, schema_name=schema_name)
    if user_error:
        return {"has_permission": False, "reason": user_error}

    user_result = await fetch_all(
        "SELECT id, is_active FROM users WHERE id = $1",
        user_id,
        schema_name=schema_name
    )

    if not user_result:
        return {"has_permission": False, "reason": "Usuario no encontrado"}

    if not user_result[0]['is_active']:
        return {"has_permission": False, "reason": "Usuario inactivo"}

    if permission_type == "create":
        return {"has_permission": True, "reason": "Usuario activo puede crear documentos"}

    elif permission_type == "sign":
        if not resource_id:
            return {"has_permission": False, "reason": "Se requiere ID de documento para validar firma"}

        signer_result = await fetch_all(
            "SELECT user_id FROM document_signers WHERE document_id = $1 AND user_id = $2",
            resource_id, user_id,
            schema_name=schema_name
        )

        if signer_result:
            return {"has_permission": True, "reason": "Usuario es firmante del documento"}
        else:
            return {"has_permission": False, "reason": "Usuario no es firmante del documento"}

    elif permission_type == "numerate":
        if not resource_id:
            numerator_result = await fetch_one(
                "SELECT COUNT(*) as count FROM document_signers WHERE user_id = $1 AND is_numerator = true",
                user_id,
                schema_name=schema_name
            )

            if numerator_result['count'] > 0:
                return {"has_permission": True, "reason": "Usuario tiene capacidad de numeración"}
            else:
                return {"has_permission": False, "reason": "Usuario no tiene capacidad de numeración"}
        else:
            doc_numerator_result = await fetch_all(
                "SELECT user_id FROM document_signers WHERE document_id = $1 AND user_id = $2 AND is_numerator = true",
                resource_id, user_id,
                schema_name=schema_name
            )

            if doc_numerator_result:
                return {"has_permission": True, "reason": "Usuario es numerador del documento"}
            else:
                return {"has_permission": False, "reason": "Usuario no es numerador del documento"}

    elif permission_type == "view":
        if not resource_id:
            return {"has_permission": True, "reason": "Usuario puede ver documentos generales"}

        access_result = await fetch_all(
            """
            SELECT
                (d.creator_id = $1) as is_creator,
                (ds.user_id IS NOT NULL) as is_signer
            FROM documents d
            LEFT JOIN document_signers ds ON d.id = ds.document_id AND ds.user_id = $2
            WHERE d.id = $3
            """,
            user_id, user_id, resource_id,
            schema_name=schema_name
        )

        if not access_result:
            return {"has_permission": False, "reason": "Documento no encontrado"}

        access = access_result[0]
        if access['is_creator'] or access['is_signer']:
            return {"has_permission": True, "reason": "Usuario tiene acceso al documento"}
        else:
            return {"has_permission": False, "reason": "Usuario no tiene acceso al documento"}

    else:
        return {"has_permission": False, "reason": f"Tipo de permiso '{permission_type}' no reconocido"}
