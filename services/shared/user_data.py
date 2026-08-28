
from typing import Dict, Any, List, Optional
from database import fetch_all, fetch_one
from shared.exceptions import ValidationError


async def get_user_complete_data(user_id: str, *, schema_name: str) -> Optional[Dict[str, Any]]:
    query = _build_creator_user_query()
    result = await fetch_one(query, user_id, schema_name=schema_name)

    if not result:
        return None

    return _format_creator_data(dict(result))


async def get_document_signers_complete_data(document_id: str, *, schema_name: str) -> List[Dict[str, Any]]:
    query = _build_document_signers_query()
    result = await fetch_all(query, document_id, schema_name=schema_name)

    signers = [_format_signer_data(dict(signer)) for signer in result]

    _validate_numerator_rules(signers, document_id)

    return signers


def _validate_numerator_rules(signers: List[Dict[str, Any]], document_id: str) -> None:
    numerators = [s for s in signers if s.get('is_numerator') is True]
    numerator_count = len(numerators)

    if numerator_count == 0:
        raise ValidationError(f"Documento {document_id} debe tener al menos 1 numerador")
    elif numerator_count > 1:
        numerator_names = [n.get('full_name', 'Sin nombre') for n in numerators]
        raise ValidationError(
            f"Documento {document_id} tiene {numerator_count} numeradores, debe tener exactamente 1. "
            f"Numeradores encontrados: {', '.join(numerator_names)}"
        )


def _build_creator_user_query() -> str:
    return """
        SELECT DISTINCT ON (u.id)
            u.id AS user_id,
            u.full_name,
            u.profile_picture_url,
            cs.name as seal_name,
            d.acronym as department_acronym,
            s.acronym as sector_acronym,
            s.primary_color as sector_color
        FROM users u
        LEFT JOIN user_seals us ON u.id = us.user_id
        LEFT JOIN city_seals cs ON us.city_seal_id = cs.id
        LEFT JOIN sectors s ON u.sector_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        WHERE u.id = $1
        ORDER BY u.id, cs.name
    """


def _build_document_signers_query() -> str:
    return """
        SELECT DISTINCT ON (COALESCE(ds.user_id, ds.citizen_id))
            ds.user_id,
            ds.citizen_id,
            COALESCE(u.full_name, c.full_name) as full_name,
            u.email,
            c.country_id as country_id,
            ds.is_numerator,
            ds.signed_at,
            u.profile_picture_url,
            cs.name as seal_name,
            d.acronym as department_acronym,
            s.acronym as sector_acronym,
            s.primary_color as sector_color,
            CASE
                WHEN ds.status = 'signed' THEN true
                ELSE false
            END as has_signed
        FROM document_signers ds
        LEFT JOIN users u ON ds.user_id = u.id
        LEFT JOIN citizens c ON ds.citizen_id = c.id
        LEFT JOIN user_seals us ON u.id = us.user_id
        LEFT JOIN city_seals cs ON us.city_seal_id = cs.id
        LEFT JOIN sectors s ON u.sector_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        WHERE ds.document_id = $1
        ORDER BY COALESCE(ds.user_id, ds.citizen_id), cs.name
    """


def _format_creator_data(user_raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "user_id": str(user_raw['user_id']),
        "full_name": user_raw['full_name'],
        "profile_picture_url": user_raw.get('profile_picture_url'),
        "seal_name": user_raw.get('seal_name'),
        "department_acronym": user_raw.get('department_acronym'),
        "sector_acronym": user_raw.get('sector_acronym'),
        "sector_color": user_raw.get('sector_color')
    }


def _format_signer_data(signer_raw: Dict[str, Any]) -> Dict[str, Any]:
    citizen_id = signer_raw.get('citizen_id')
    country_id = signer_raw.get('country_id')
    if citizen_id:
        seal_name = f"CIUDADANO · {country_id}"
        department_acronym = "TAD"
        sector_acronym = None
        sector_color = None
    else:
        seal_name = signer_raw.get('seal_name')
        department_acronym = signer_raw.get('department_acronym')
        sector_acronym = signer_raw.get('sector_acronym')
        sector_color = signer_raw.get('sector_color')
    return {
        "user_id": str(signer_raw['user_id']) if signer_raw.get('user_id') else None,
        "citizen_id": str(citizen_id) if citizen_id else None,
        "country_id": country_id,
        "full_name": signer_raw['full_name'],
        "email": signer_raw.get('email', ''),
        "is_numerator": bool(signer_raw.get('is_numerator', False)),
        "profile_picture_url": signer_raw.get('profile_picture_url'),
        "seal_name": seal_name,
        "department_acronym": department_acronym,
        "sector_acronym": sector_acronym,
        "sector_color": sector_color,
        "has_signed": bool(signer_raw.get('has_signed', False)),
        "signed_at": signer_raw.get('signed_at'),
    }


async def get_document_signers_for_preview(document_id: str, *, schema_name: str) -> List[Dict[str, Any]]:
    query = _build_document_signers_query()
    result = await fetch_all(query, document_id, schema_name=schema_name)

    return [_format_signer_data(dict(signer)) for signer in result]
