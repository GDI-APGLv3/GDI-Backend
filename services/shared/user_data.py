"""
Servicio compartido para obtener datos de usuarios de forma consistente.
Aplicando DRY principle - reutilizable en múltiples contextos.

Sigue Clean Architecture y Single Responsibility Principle.
"""

from typing import Dict, Any, List, Optional
from database import fetch_all, fetch_one
from shared.exceptions import ValidationError


async def get_user_complete_data(user_id: str, *, schema_name: str) -> Optional[Dict[str, Any]]:
    """
    Obtiene datos básicos de un usuario para preview - OPTIMIZADA.

    Solo campos necesarios para el creador en preview.

    Args:
        user_id: UUID del usuario
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Dict con datos básicos del usuario o None si no existe
    """
    query = _build_creator_user_query()
    result = await fetch_one(query, user_id, schema_name=schema_name)

    if not result:
        return None

    return _format_creator_data(dict(result))


async def get_multiple_users_complete_data(user_ids: List[str], *, schema_name: str) -> List[Dict[str, Any]]:
    """
    Obtiene datos completos de múltiples usuarios.

    Optimizado para obtener firmantes de forma eficiente.

    Args:
        user_ids: Lista de UUIDs de usuarios
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Lista de diccionarios con datos de usuarios
    """
    if not user_ids:
        return []

    query = _build_multiple_users_query(len(user_ids))
    result = await fetch_all(query, *user_ids, schema_name=schema_name)

    return [_format_single_user_data(dict(user)) for user in result]


async def get_document_signers_complete_data(document_id: str, *, schema_name: str) -> List[Dict[str, Any]]:
    """
    Obtiene datos completos de firmantes de un documento específico.

    Incluye información de orden de firma y si es numerador.
    Elimina usuarios duplicados y valida reglas de numerador.

    Args:
        document_id: UUID del documento
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Lista de firmantes con datos completos validados

    Raises:
        ValidationError: Si no hay numerador o hay más de uno
    """
    query = _build_document_signers_query()
    result = await fetch_all(query, document_id, schema_name=schema_name)

    signers = [_format_signer_data(dict(signer)) for signer in result]

    # Validar reglas de numerador
    _validate_numerator_rules(signers, document_id)

    return signers


# ============================================================================
# VALIDACIONES DE NEGOCIO - Business Rules
# ============================================================================

def _validate_numerator_rules(signers: List[Dict[str, Any]], document_id: str) -> None:
    """
    Valida las reglas de numerador para un documento.

    Reglas:
    - Debe haber exactamente 1 numerador (mínimo 1, máximo 1)

    Args:
        signers: Lista de firmantes del documento
        document_id: ID del documento para mensajes de error

    Raises:
        ValidationError: Si no se cumplen las reglas de numerador
    """
    from shared.exceptions import ValidationError

    # Contar numeradores
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


# ============================================================================
# CONSTRUCCIÓN DE QUERIES - Single Responsibility Principle
# ============================================================================

def _build_creator_user_query() -> str:
    """
    Construye query para obtener datos básicos del creador - OPTIMIZADA.

    Solo campos necesarios para el creador del documento.
    """
    return """
        SELECT DISTINCT ON (u.id)
            u.id AS user_id,
            u.full_name,
            u.profile_picture_url,
            cs.name as seal_name,
            d.acronym as department_acronym,
            s.acronym as sector_acronym
        FROM users u
        LEFT JOIN user_seals us ON u.id = us.user_id
        LEFT JOIN city_seals cs ON us.city_seal_id = cs.id
        LEFT JOIN sectors s ON u.sector_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        WHERE u.id = $1
        ORDER BY u.id, cs.name
    """


def _build_single_user_query() -> str:
    """
    Construye query para obtener datos completos de un usuario.

    Incluye JOINs necesarios para traer todos los datos relacionados.
    """
    return """
        SELECT DISTINCT ON (u.id)
            u.id as user_id,
            u.full_name,
            u.email,
            u.profile_picture_url,
            u.estado as is_active,
            cs.name as seal_name,
            d.acronym as department_acronym,
            d.name as department_name
        FROM users u
        LEFT JOIN user_seals us ON u.id = us.user_id
        LEFT JOIN city_seals cs ON us.city_seal_id = cs.id
        LEFT JOIN sectors s ON u.sector_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        WHERE u.id = $1
        ORDER BY u.id, cs.name
    """


def _build_multiple_users_query(user_count: int) -> str:
    """
    Construye query para obtener datos de múltiples usuarios.

    Args:
        user_count: Número de usuarios para generar placeholders
    """
    placeholders = ', '.join([f'${i+1}' for i in range(user_count)])

    return f"""
        SELECT DISTINCT ON (u.id)
            u.id as user_id,
            u.full_name,
            u.email,
            u.profile_picture_url,
            u.estado as is_active,
            cs.name as seal_name,
            d.acronym as department_acronym,
            d.name as department_name
        FROM users u
        LEFT JOIN user_seals us ON u.id = us.user_id
        LEFT JOIN city_seals cs ON us.city_seal_id = cs.id
        LEFT JOIN sectors s ON u.sector_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        WHERE u.id IN ({placeholders})
        ORDER BY u.id, cs.name
    """


def _build_document_signers_query() -> str:
    """
    Construye query para obtener firmantes de un documento - OPTIMIZADA.

    Solo trae campos necesarios: datos básicos + estado de firma.
    Elimina usuarios duplicados - si tiene varios sellos, toma el primero.
    """
    return """
        SELECT DISTINCT ON (ds.user_id)
            ds.user_id,
            u.full_name,
            u.email,
            ds.is_numerator,
            ds.signed_at,
            u.profile_picture_url,
            cs.name as seal_name,
            d.acronym as department_acronym,
            CASE
                WHEN ds.status = 'signed' THEN true
                ELSE false
            END as has_signed
        FROM document_signers ds
        JOIN users u ON ds.user_id = u.id
        LEFT JOIN user_seals us ON u.id = us.user_id
        LEFT JOIN city_seals cs ON us.city_seal_id = cs.id
        LEFT JOIN sectors s ON u.sector_id = s.id
        LEFT JOIN departments d ON s.department_id = d.id
        WHERE ds.document_id = $1
        ORDER BY ds.user_id, cs.name
    """


# ============================================================================
# FORMATEO DE DATOS - Single Responsibility Principle
# ============================================================================

def _format_single_user_data(user_raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Formatea datos crudos de un usuario a estructura estándar.

    Mantiene consistencia con el servicio de búsqueda optimizado.
    """
    return {
        "user_id": str(user_raw['user_id']),
        "full_name": user_raw['full_name'],
        "email": user_raw['email'],
        "profile_picture_url": user_raw.get('profile_picture_url'),
        "profile_photo_url": user_raw.get('profile_picture_url'),  # Alias para compatibilidad
        "is_active": bool(user_raw.get('is_active', 1)),
        "seal_name": user_raw.get('seal_name'),
        "seal_description": user_raw.get('seal_name'),  # Alias para compatibilidad
        "department_acronym": user_raw.get('department_acronym'),
        "department_name": user_raw.get('department_name')
    }


def _format_creator_data(user_raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Formatea datos del creador - SOLO CAMPOS BÁSICOS.

    Mantiene compatibilidad con estructura original sin campos extras.
    """
    return {
        "user_id": str(user_raw['user_id']),
        "full_name": user_raw['full_name'],
        "profile_picture_url": user_raw.get('profile_picture_url'),
        "seal_name": user_raw.get('seal_name'),
        "department_acronym": user_raw.get('department_acronym'),
        "sector_acronym": user_raw.get('sector_acronym')
    }


def _format_signer_data(signer_raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Formatea datos de un firmante - SOLO CAMPOS NECESARIOS.

    Mantiene compatibilidad con estructura original + estado de firma.
    """
    return {
        "user_id": str(signer_raw['user_id']),
        "full_name": signer_raw['full_name'],
        "email": signer_raw.get('email', ''),
        "is_numerator": bool(signer_raw.get('is_numerator', False)),
        "profile_picture_url": signer_raw.get('profile_picture_url'),
        "seal_name": signer_raw.get('seal_name'),
        "department_acronym": signer_raw.get('department_acronym'),
        "has_signed": bool(signer_raw.get('has_signed', False)),
        "signed_at": signer_raw.get('signed_at'),
    }


async def get_document_signers_for_preview(document_id: str, *, schema_name: str) -> List[Dict[str, Any]]:
    """
    Obtiene datos de firmantes para previsualización SIN validaciones.

    Esta función no valida numeradores ni otros requisitos de negocio.
    Solo obtiene los firmantes que existan, si los hay.

    Args:
        document_id: UUID del documento
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Lista de firmantes (puede estar vacía si no hay firmantes)
    """
    query = _build_document_signers_query()
    result = await fetch_all(query, document_id, schema_name=schema_name)

    # Retornar firmantes formateados SIN validaciones
    return [_format_signer_data(dict(signer)) for signer in result]
