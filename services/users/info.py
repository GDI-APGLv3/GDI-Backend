"""
Servicio para obtener información detallada de un usuario del tenant.

Expone la misma estructura de datos que el endpoint del Gateway (SET B)
`GET /api/v1/system/users/{user_id}` (tools/system.py::get_user_info).
"""
from typing import Dict, Any

from shared.logging import get_logger
from database import fetch_one, fetch_all
from shared.exceptions import NotFoundError

logger = get_logger(__name__)


async def get_user_info(user_id: str, *, schema_name: str) -> Dict[str, Any]:
    """
    Obtener información detallada de un usuario del tenant.

    Devuelve datos del perfil, sector principal, roles y sectores adicionales
    con permisos. Idéntica estructura a la que retorna el endpoint REST del
    Gateway (SET B) para `GET /api/v1/system/users/{user_id}`.

    Args:
        user_id: UUID del usuario a consultar
        schema_name: Schema de la municipalidad (keyword-only)

    Returns:
        Dict con:
        - user_id, full_name, email, profile_picture_url, estado
        - last_access, created_at (ISO strings o None)
        - sector: dict con id, acronym, department_id, department_name, department_acronym
        - roles: lista de nombres de rol
        - additional_sectors: lista de sectores adicionales con permisos

    Raises:
        NotFoundError: Si el usuario no existe en el tenant
    """
    if not user_id:
        raise ValueError("user_id es requerido")

    logger.info(f"get_user_info - user_id={user_id}, schema={schema_name}")

    user_data = await fetch_one(
        """
        SELECT
            u.id          AS user_id,
            u.full_name,
            u.email,
            u.profile_picture_url,
            u.estado,
            u.last_access,
            u.created_at,
            s.id          AS sector_id,
            s.acronym     AS sector_acronym,
            d.id          AS department_id,
            d.name        AS department_name,
            d.acronym     AS department_acronym
        FROM users u
        LEFT JOIN sectors s ON u.sector_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        WHERE u.id = $1
        """,
        user_id,
        schema_name=schema_name,
    )

    if not user_data:
        raise NotFoundError(f"Usuario no encontrado: {user_id}")

    roles_data = await fetch_all(
        """
        SELECT r.role_name
        FROM user_roles ur
        JOIN public.roles r ON ur.role_id = r.role_id
        WHERE ur.user_id = $1
        """,
        user_id,
        schema_name=schema_name,
    )

    additional_data = await fetch_all(
        """
        SELECT
            s.id          AS sector_id,
            s.acronym     AS sector_acronym,
            d.acronym     AS department_acronym,
            d.name        AS department_name,
            usp.can_view,
            usp.can_edit
        FROM user_sector_permissions usp
        JOIN sectors s ON usp.sector_id = s.id
        JOIN departments d ON s.department_id = d.id
        WHERE usp.user_id = $1 AND s.is_active = true
        ORDER BY d.acronym, s.acronym
        """,
        user_id,
        schema_name=schema_name,
    )

    additional_sectors = [
        {
            "sector_id": str(row["sector_id"]),
            "sector_acronym": row["sector_acronym"],
            "department_acronym": row["department_acronym"],
            "department_name": row["department_name"],
            "can_view": row["can_view"],
            "can_edit": row["can_edit"],
        }
        for row in (additional_data or [])
    ]

    result = {
        "user_id": str(user_data["user_id"]),
        "full_name": user_data["full_name"],
        "email": user_data["email"],
        "profile_picture_url": user_data["profile_picture_url"],
        "estado": user_data["estado"],
        "last_access": (
            user_data["last_access"].isoformat()
            if user_data["last_access"] else None
        ),
        "created_at": (
            user_data["created_at"].isoformat()
            if user_data["created_at"] else None
        ),
        "sector": {
            "id": str(user_data["sector_id"]) if user_data["sector_id"] else None,
            "acronym": user_data["sector_acronym"],
            "department_id": (
                str(user_data["department_id"]) if user_data["department_id"] else None
            ),
            "department_name": user_data["department_name"],
            "department_acronym": user_data["department_acronym"],
        } if user_data["sector_id"] else None,
        "roles": [row["role_name"] for row in (roles_data or [])],
        "additional_sectors": additional_sectors,
    }

    logger.info(f"get_user_info - usuario encontrado: {user_data['full_name']}")
    return result
