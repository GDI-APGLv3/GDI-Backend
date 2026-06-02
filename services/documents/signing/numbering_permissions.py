"""
Validador único de permisos de numeración de documentos.

REGLA DE NEGOCIO:
  Un usuario puede numerar un tipo de documento si y solo si cumple DOS condiciones:

  1. has_rank_permission (condicional):
     - Si el tipo NO tiene filas en document_types_allowed_by_rank → pasa (sin restricción).
     - Si SÍ tiene filas → el usuario debe ser head_user_id de algún departamento cuyo
       rank_id esté entre los permitidos (document_types_allowed_by_rank).
       El rango viene de la TITULARIDAD del departamento (departments.rank_id),
       NO del sello del usuario (user_seals/city_seals).
       Titular de depto sin rank_id → RECHAZADO.
       Usuario sin titularidad → RECHAZADO.

  2. has_sector_permission (condicional):
     - Si el tipo NO tiene filas en enabled_document_types_by_sector → pasa (todos habilitados).
     - Si SÍ tiene filas → el sector del usuario (users.sector_id) debe estar listado,
       O el usuario debe tener un user_sector_permissions con can_edit=true apuntando a
       alguno de los sectores habilitados.

APLICA SOLO AL NUMERADOR. Los firmantes comunes no usan esta validación.

FUENTE ÚNICA: Todos los paths que validan numeración llaman a esta función.
  - services/documents/signing/numerator.py  (firma electrónica)
  - services/documents/signing/dispatcher.py (firma digital)
  - endpoints/documents/check_signer_permissions.py (validación preventiva frontend)
  - api_gateway/tools/system.py              (tool MCP check_signer_permissions)

DOCUMENTOS AUTOMÁTICOS (CAEX/PV vía _document_creator_base.py) quedan EXCLUIDOS
a propósito — los genera el sistema, no un usuario numerador.
"""

from typing import Tuple
from database import fetch_one
from shared.logging import get_logger

logger = get_logger(__name__)


async def can_user_number_document_type(
    user_id: str,
    document_type_id: int,
    *,
    schema_name: str,
) -> Tuple[bool, bool, str]:
    """
    Verifica si un usuario puede numerar un tipo de documento dado.

    UNA sola query SQL que evalúa ambas condiciones como columnas CASE/EXISTS.

    Args:
        user_id:          UUID del usuario numerador (como str).
        document_type_id: ID entero del tipo de documento (document_types.id).
        schema_name:      Schema del tenant (keyword-only, obligatorio).

    Returns:
        Tupla (has_rank_permission, has_sector_permission, reason).
        - has_rank_permission: True si pasa la validación de titularidad/rango.
        - has_sector_permission: True si pasa la validación de sector.
        - reason: mensaje legible explicando el resultado. "OK" si ambas pasan.
          Si alguna falla, describe la razón del rechazo. Si el tipo de documento
          no existe, devuelve un mensaje de error y ambas flags en False.

    Raises:
        No lanza excepciones propias. Errores de BD se propagan al caller.
    """
    logger.info(
        f"can_user_number_document_type: user={user_id[:8]}... "
        f"doc_type_id={document_type_id}"
    )

    row = await fetch_one(
        """
        SELECT
            dt.name            AS doc_type_name,
            -- RANK: condicional — activo solo si el tipo tiene ranks configurados.
            -- Pasa si: (a) no hay restricción, o (b) el usuario es head_user_id
            -- de un departamento cuyo rank_id está entre los ranks permitidos.
            -- El rango viene de departments.rank_id (titularidad), NO del sello.
            CASE
                WHEN NOT EXISTS(
                    SELECT 1 FROM document_types_allowed_by_rank
                    WHERE document_type_id = dt.id
                ) THEN true
                WHEN EXISTS(
                    SELECT 1
                    FROM departments d_head
                    JOIN document_types_allowed_by_rank dtabr
                      ON d_head.rank_id = dtabr.rank_id
                    WHERE d_head.head_user_id = u.id
                      AND dtabr.document_type_id = dt.id
                ) THEN true
                ELSE false
            END                AS has_rank_permission,
            -- SECTOR: condicional — activo solo si el tipo tiene sectores configurados.
            -- Pasa si: (a) no hay restricción, (b) el sector del usuario está habilitado,
            -- o (c) tiene un permiso adicional con can_edit=true en un sector habilitado.
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
            END                AS has_sector_permission,
            -- Nombre del departamento del usuario (para mensajes de error)
            dep.name           AS user_department_name
        FROM document_types dt
        JOIN users u ON u.id = $1
        LEFT JOIN sectors sec ON u.sector_id = sec.id
        LEFT JOIN departments dep ON sec.department_id = dep.id
        WHERE dt.id = $2
        """,
        user_id,
        document_type_id,
        schema_name=schema_name,
    )

    if not row:
        reason = (
            f"No se encontraron datos para el usuario o tipo de documento "
            f"id={document_type_id}. Verificar que ambos existan."
        )
        logger.warning(f"can_user_number_document_type: no data — {reason}")
        return False, False, reason

    has_rank: bool = bool(row["has_rank_permission"])
    has_sector: bool = bool(row["has_sector_permission"])
    doc_type_name: str = row["doc_type_name"] or f"tipo id={document_type_id}"
    dept_name: str = row["user_department_name"] or "sin departamento"

    if has_rank and has_sector:
        reason = "OK"
    elif not has_rank and not has_sector:
        reason = (
            f"Sin permiso de rango para numerar '{doc_type_name}': debe ser titular "
            f"de una repartición cuyo rango esté habilitado para este tipo de documento. "
            f"Además, el sector del departamento '{dept_name}' no tiene habilitado "
            f"el tipo de documento '{doc_type_name}'."
        )
    elif not has_rank:
        reason = (
            f"Sin permiso de rango para numerar '{doc_type_name}'. "
            f"Debe ser titular de una repartición cuyo rango esté "
            f"habilitado para este tipo de documento."
        )
    else:
        reason = (
            f"El sector del departamento '{dept_name}' no tiene habilitado "
            f"el tipo de documento '{doc_type_name}'."
        )

    logger.info(
        f"can_user_number_document_type: has_rank={has_rank} "
        f"has_sector={has_sector} reason={reason[:80]}"
    )
    return has_rank, has_sector, reason
