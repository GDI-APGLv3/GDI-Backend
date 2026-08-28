
from typing import Dict, Any
from database import fetch_one
from shared.exceptions import ValidationError
from shared.logging import get_logger

logger = get_logger(__name__)


async def get_signer_data(user_id: str, *, schema_name: str) -> Dict[str, Any]:
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


async def get_citizen_signer_data(citizen_id: str, *, schema_name: str) -> Dict[str, Any]:
    logger.info(f"Obteniendo datos de ciudadano {citizen_id[:8]}...")

    result = await fetch_one(
        "SELECT full_name, country_id FROM citizens WHERE id = $1",
        citizen_id,
        schema_name=schema_name,
    )

    if not result:
        raise ValidationError(f"Ciudadano {citizen_id} no encontrado")

    municipality_name = None
    if schema_name:
        muni_result = await fetch_one(
            "SELECT name FROM public.municipalities WHERE schema_name = $1",
            schema_name,
            schema_name="public",
        )
        municipality_name = muni_result["name"] if muni_result else None

    signer_data = {
        "full_name": result["full_name"],
        "seal": f"CIUDADANO · {result['country_id']}",
        "department_name": "TRÁMITES A DISTANCIA",
        "municipality_name": municipality_name or "Municipalidad Del Futuro",
        "department_acronym": "TAD",
        "sector_acronym": "",
    }

    logger.info(f"Datos de ciudadano obtenidos: {signer_data['full_name']} / {signer_data['seal']}")

    return signer_data
