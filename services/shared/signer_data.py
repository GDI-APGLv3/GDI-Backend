"""
Servicio compartido para obtener datos de firmantes.

Centraliza la lógica de recuperación de datos de usuarios para firma de documentos,
eliminando duplicación de código en signing.py, numerator.py, cover_creator.py y transfer_document_creator.py.
"""

from typing import Dict, Any
from database import fetch_one
from shared.exceptions import ValidationError
from shared.logging import get_logger

logger = get_logger(__name__)


async def get_signer_data(user_id: str, *, schema_name: str) -> Dict[str, Any]:
    """
    Obtiene datos completos de un firmante para procesos de firma de documentos.

    Centraliza la lógica de obtención de datos de usuario que se usa en:
    - signing.py (firmantes comunes)
    - numerator.py (firmantes numeradores)
    - cover_creator.py (carátulas de expedientes)
    - transfer_document_creator.py (documentos de transferencia)

    Args:
        user_id: UUID del usuario firmante
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Dict con todos los campos necesarios para firma:
        {
            "full_name": str,              # Nombre completo del firmante
            "seal": str,                   # Sello (city_seals.name via users.default_seal_id)
            "department_name": str,        # Nombre del departamento
            "municipality_name": str,      # Nombre del municipio
            "department_acronym": str,     # Acrónimo del departamento (para logs)
            "sector_acronym": str          # Acrónimo del sector (para logs)
        }

    Raises:
        ValidationError: Si el usuario no existe

    Notes:
        - Usa LEFT JOIN para tolerar datos faltantes
        - Aplica valores por defecto cuando los datos son NULL:
          * seal: "Sin sello asignado"
          * department_name: "Sin departamento"
          * municipality_name: "Municipalidad Del Futuro"
        - El sello se obtiene de city_seals.name via users.default_seal_id
    """
    logger.info(f"Obteniendo datos para usuario {user_id[:8]}...")

    query = """
        SELECT
            u.full_name,
            cs.name as seal,
            dep.name as department_name,
            dep.acronym as department_acronym,
            sec.acronym as sector_acronym
        FROM users u
        LEFT JOIN user_seals us ON u.id = us.user_id
        LEFT JOIN city_seals cs ON us.city_seal_id = cs.id
        LEFT JOIN sectors sec ON u.sector_id = sec.id
        LEFT JOIN departments dep ON sec.department_id = dep.id
        WHERE u.id = $1
    """

    result = await fetch_one(query, user_id, schema_name=schema_name)

    # Obtener nombre del municipio desde public.municipalities usando schema_name
    municipality_name = None
    if schema_name:
        muni_result = await fetch_one(
            "SELECT name FROM public.municipalities WHERE schema_name = $1",
            schema_name,
            schema_name="public",
        )
        municipality_name = muni_result["name"] if muni_result else None

    if not result:
        raise ValidationError(f"Usuario {user_id} no encontrado")

    # Aplicar fallbacks para valores NULL
    signer_data = {
        "full_name": result["full_name"],
        "seal": result["seal"] or "Sin sello asignado",
        "department_name": result["department_name"] or "Sin departamento",
        "municipality_name": municipality_name or "Municipalidad Del Futuro",
        "department_acronym": result["department_acronym"] or "",
        "sector_acronym": result["sector_acronym"] or ""
    }

    logger.info(f"Datos obtenidos exitosamente:")
    logger.info(f"  - Nombre: {signer_data['full_name']}")
    logger.info(f"  - Sello: {signer_data['seal']}")
    logger.info(f"  - Departamento: {signer_data['department_name']}")
    logger.info(f"  - Municipio: {signer_data['municipality_name']}")

    return signer_data
