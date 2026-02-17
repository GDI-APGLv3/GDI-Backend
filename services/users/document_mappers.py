"""
Mapeadores de datos para documentos de usuario.
Transforma datos de base de datos a formato de API.
"""

from typing import Dict, Any, List
from services.documents.catalog.states import get_display_state_name

def map_display_status(status: str, rol_usuario: str, usuario_ya_firmo: bool,
                      documento_completado: bool, es_numerador: bool,
                      numeracion_pendiente: bool, documento_rechazado: bool,
                      all_common_signers_completed: bool = False, *, schema_name: str) -> str:
    """
    Mapea el estado del documento a display status correcto según el rol.

    DISPLAY STATUS:
    - "En edición": Solo creador en draft
    - "Firmar ahora": Usuario puede firmar (común en paralelo/turno o numerador cuando es su turno)
    - "En proceso de firma": Usuario ya firmó o numerador esperando su turno
    - "Firmado": TODO el ciclo completo (incluyendo numerador)

    Args:
        status: Estado del documento
        rol_usuario: 'creator', 'signer', 'numerator'
        usuario_ya_firmo: Si el usuario ya firmó
        documento_completado: Si TODO está completo (incluyendo numerador)
        es_numerador: Si el usuario es numerador
        numeracion_pendiente: Si falta numeración
        documento_rechazado: Si fue rechazado
        all_common_signers_completed: Si todos los comunes firmaron
        schema_name: Schema del tenant (multi-tenant)
    """
    # Caso especial: rechazado
    if documento_rechazado:
        # Solo el creador ve documentos rechazados (como "En edición")
        if rol_usuario == "creator":
            return "En edición"
        else:
            # Para firmantes/numeradores, retornamos None para filtrar
            return None
    
    # Caso especial: TODO completo (incluyendo numerador)
    if documento_completado:
        return "Firmado"
    
    # CREADOR
    if rol_usuario == "creator":
        if status == "draft":
            return "En edición"
        else:
            # Creador ve "En proceso de firma" mientras hay firmas pendientes
            return "En proceso de firma"
    
    # FIRMANTE COMÚN (no numerador)
    elif rol_usuario == "signer":
        if status == "sent_to_sign":
            if usuario_ya_firmo:
                # Ya firmó, esperando otros
                return "En proceso de firma"
            else:
                # Puede firmar ahora
                return "Firmar ahora"
        elif status == "signed":
            # Ya está signed pero no completado = faltan firmas (numerador)
            return "En proceso de firma"
    
    # NUMERADOR
    elif rol_usuario == "numerator":
        if status == "sent_to_sign":
            if all_common_signers_completed and not usuario_ya_firmo:
                # Es su turno, puede firmar
                return "Firmar ahora"
            else:
                # Esperando que terminen los comunes
                return "En proceso de firma"
        elif status == "signed":
            # Documento signed pero si llegó aquí es porque no está completado
            # entonces o ya firmó y está esperando, o aún no firmó
            if usuario_ya_firmo:
                return "En proceso de firma"  # Ya firmó, esperando finalización
            else:
                return "Firmar ahora"  # Puede firmar ahora
    
    # Fallback
    return "En proceso de firma"


def determine_user_role(is_creator: bool, is_numerator_for_user: bool) -> str:
    """
    Determina el rol del usuario en el documento.
    
    Args:
        is_creator: Si el usuario es el creador
        is_numerator_for_user: Si el usuario es numerador
        
    Returns:
        Rol del usuario ('creator', 'numerator', 'signer')
    """
    if is_creator:
        return "creator"
    elif is_numerator_for_user:
        return "numerator"
    else:
        return "signer"


def map_document_data(doc: Dict[str, Any], *, schema_name: str) -> Dict[str, Any]:
    """
    Mapea un documento de BD a formato de API.

    Args:
        doc: Documento raw de la base de datos
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Documento formateado para la API
    """
    user_role = determine_user_role(doc['is_creator'], doc['is_numerator_for_user'])

    display_status = map_display_status(
        status=doc['status'],
        rol_usuario=user_role,
        usuario_ya_firmo=doc['user_already_signed'],
        documento_completado=doc['is_completed'],
        es_numerador=doc['is_numerator_for_user'],
        numeracion_pendiente=doc['has_pending_numeration'],
        documento_rechazado=doc['is_rejected'],
        all_common_signers_completed=doc.get('all_common_signers_completed', False),
        schema_name=schema_name
    )
    
    return {
        "id": doc['id'],
        "reference": doc['reference'],
        "display_status": display_status,
        "updated_at": doc['updated_at'].isoformat() if doc['updated_at'] else None,
        "document_type": {
            "name": doc['document_type_name'],
            "acronym": doc['document_type_acronym']
        },
        "user_role": user_role,
        # Solo la información del último editor que necesita el frontend
        "last_editor_name": doc.get('last_editor_name'), 
        "last_editor_profile_picture_url": doc.get('last_editor_profile_picture_url'),
        # Número oficial para documentos oficializados
        "official_number": doc.get('official_number')
    }


def map_documents_list(documents_data: List[Dict[str, Any]], *, schema_name: str) -> List[Dict[str, Any]]:
    """
    Mapea una lista de documentos de BD a formato de API.
    Filtra documentos que no deben ser visibles para el usuario.

    Args:
        documents_data: Lista de documentos raw de la BD
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Lista de documentos formateados para la API (sin documentos filtrados)
    """
    mapped_docs = []
    for doc in documents_data:
        mapped_doc = map_document_data(doc, schema_name=schema_name)
        # Solo incluir documentos con display_status válido (no None)
        if mapped_doc['display_status'] is not None:
            mapped_docs.append(mapped_doc)
    return mapped_docs