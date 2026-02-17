"""
Endpoint para validar permisos de firmante numerador ANTES de firmar.
Verifica si un usuario tiene el rango y sector necesarios para firmar
un tipo de documento dado, sin necesidad de un draft existente.
"""
from shared.logging import get_logger
from fastapi import APIRouter, Request, Depends, Query
from shared.exceptions import exception_to_http_exception, DatabaseError
from models.tags import Tags
from shared.dependencies import get_tenant_schema
from database import execute_query

# === CONFIGURACION ===
logger = get_logger("check_signer_permissions")

router = APIRouter(tags=[Tags.DOCUMENTOS])

# Query adaptada de services/documents/signing/numerator.py (lineas 318-355).
# Diferencia clave: usa document_types directamente en lugar de document_draft,
# porque al momento de validar solo tenemos el tipo de documento, no un draft.
CHECK_SIGNER_PERMISSIONS_QUERY = """
    SELECT
        ur.name as user_rank_name,
        ur.level as user_rank_level,
        rr.name as required_rank_name,
        rr.level as required_rank_level,
        dt.name as doc_type_name,
        dt.acronym as doc_type_acronym,
        dep.id as user_department_id,
        dep.name as user_department_name,
        CASE
            WHEN rr.level IS NULL THEN true
            WHEN ur.level IS NULL THEN false
            WHEN ur.level <= rr.level THEN true
            ELSE false
        END as has_rank_permission,
        CASE
            WHEN NOT EXISTS(
                SELECT 1 FROM enabled_document_types_by_sector
                WHERE document_type_id = dt.id
            ) THEN true
            WHEN EXISTS(
                SELECT 1 FROM enabled_document_types_by_sector
                WHERE document_type_id = dt.id
                AND sector_id = u.sector_id
            ) THEN true
            WHEN EXISTS(
                SELECT 1 FROM enabled_document_types_by_sector edts
                WHERE edts.document_type_id = dt.id
                AND edts.sector_id IN (
                    SELECT usp.sector_id
                    FROM user_sector_permissions usp
                    WHERE usp.user_id = u.id AND usp.can_edit = true
                )
            ) THEN true
            ELSE false
        END as has_sector_permission
    FROM document_types dt
    JOIN users u ON u.id = %s
    LEFT JOIN user_seals us ON u.id = us.user_id
    LEFT JOIN city_seals cs ON us.city_seal_id = cs.id
    LEFT JOIN ranks ur ON cs.rank_id = ur.id
    LEFT JOIN sectors sec ON u.sector_id = sec.id
    LEFT JOIN departments dep ON sec.department_id = dep.id
    LEFT JOIN document_types_allowed_by_rank dtabr ON dt.id = dtabr.document_type_id
    LEFT JOIN ranks rr ON dtabr.rank_id = rr.id
    WHERE dt.acronym = %s
"""


def _build_response(row: dict) -> dict:
    """Construye la respuesta JSON a partir de una fila de resultado."""
    has_rank = row["has_rank_permission"]
    has_sector = row["has_sector_permission"]
    can_sign = has_rank and has_sector

    # Construir mensaje descriptivo
    if can_sign:
        message = "OK"
    elif not has_rank and not has_sector:
        message = (
            f"Rango insuficiente: el usuario tiene rango "
            f"'{row['user_rank_name'] or 'sin rango'}' "
            f"pero se requiere '{row['required_rank_name']}'. "
            f"Ademas, el sector del usuario no tiene habilitado "
            f"el tipo de documento '{row['doc_type_name']}'."
        )
    elif not has_rank:
        message = (
            f"Rango insuficiente: el usuario tiene rango "
            f"'{row['user_rank_name'] or 'sin rango'}' "
            f"pero se requiere '{row['required_rank_name']}' "
            f"para firmar documentos de tipo '{row['doc_type_name']}'."
        )
    else:
        message = (
            f"Sector no habilitado: el sector del usuario no tiene "
            f"habilitado el tipo de documento '{row['doc_type_name']}'."
        )

    return {
        "can_sign": can_sign,
        "has_rank_permission": has_rank,
        "has_sector_permission": has_sector,
        "user_rank": row["user_rank_name"],
        "required_rank": row["required_rank_name"],
        "document_type": row["doc_type_name"],
        "message": message,
    }


def _build_fail_open_response() -> dict:
    """Respuesta fail-open cuando no se encuentran datos (tipo o usuario inexistente)."""
    return {
        "can_sign": True,
        "has_rank_permission": True,
        "has_sector_permission": True,
        "user_rank": None,
        "required_rank": None,
        "document_type": None,
        "message": "OK",
    }


@router.get(
    "/check-signer-permissions",
    summary="Validar permisos de firmante numerador",
    description="""Verifica si un usuario tiene el rango y sector necesarios
    para firmar como numerador un tipo de documento dado.

    **Uso en frontend:**
    - Al guardar documento, antes de enviar a firma
    - Validacion preventiva para mostrar advertencias al usuario

    **Logica:**
    - Verifica rango del usuario vs rango requerido por el tipo de documento
    - Verifica que el sector del usuario tenga habilitado ese tipo de documento
    - Fail-open: si no se encuentra el tipo o usuario, retorna can_sign=true

    **Diferencia con la firma real:**
    - Este endpoint NO necesita un document_draft existente
    - Solo necesita document_type_acronym + user_id
    """,
)
async def check_signer_permissions(
    request: Request,
    document_type_acronym: str = Query(..., description="Acronimo del tipo de documento (ej: DEC, IF, RES)"),
    user_id: str = Query(..., description="UUID del firmante numerador a validar"),
    schema_name: str = Depends(get_tenant_schema),
) -> dict:
    """
    Valida si un usuario puede firmar como numerador un tipo de documento.

    Args:
        document_type_acronym: Acronimo del tipo de documento a validar
        user_id: UUID del usuario firmante
        schema_name: Schema del tenant (inyectado por dependency)

    Returns:
        dict con can_sign, has_rank_permission, has_sector_permission,
        user_rank, required_rank, document_type, message
    """
    try:
        logger.info(
            f"Validando permisos de firma: user={user_id}, "
            f"doc_type={document_type_acronym}, schema={schema_name}"
        )

        result = execute_query(
            CHECK_SIGNER_PERMISSIONS_QUERY,
            (user_id, document_type_acronym),
            fetch_one=True,
            schema_name=schema_name,
        )

        # Fail-open: si no hay resultado, permitir (tipo o usuario no existe)
        if not result:
            logger.warning(
                f"No se encontraron datos para user={user_id}, "
                f"doc_type={document_type_acronym}. Fail-open: can_sign=true"
            )
            return _build_fail_open_response()

        response = _build_response(result)

        logger.info(
            f"Resultado validacion: can_sign={response['can_sign']}, "
            f"rank={response['has_rank_permission']}, "
            f"sector={response['has_sector_permission']}"
        )

        return response

    except DatabaseError as e:
        logger.error(f"Error de base de datos al validar permisos: {e}")
        raise exception_to_http_exception(e)
