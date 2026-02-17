"""
Servicios para metadatos de documentos: estados, tipos e informacion general.
Maneja la logica para obtener estados de visualizacion y tipos de documento.
"""

from typing import List, Dict, Any, Optional
from database import get_db_connection, execute_query
from shared.exceptions import ValidationError
from .states import get_display_state_name


def map_display_status(status: str, rol_usuario: str, usuario_ya_firmo: bool,
                      documento_completado: bool, es_numerador: bool,
                      numeracion_pendiente: bool, documento_rechazado: bool, *, schema_name: str) -> str:
    """
    Mapea el estado del documento a un estado de visualizacion personalizado segun el contexto del usuario.

    Args:
        status: Estado actual del documento
        rol_usuario: Rol del usuario ('creator', 'signer', 'numerator', etc.)
        usuario_ya_firmo: Si el usuario ya firmo este documento
        documento_completado: Si el documento esta completado
        es_numerador: Si el usuario es numerador del documento
        numeracion_pendiente: Si hay numeracion pendiente
        documento_rechazado: Si el documento fue rechazado
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Estado de visualizacion personalizado
    """
    # Casos especiales primero
    if documento_rechazado:
        if rol_usuario == "creator":
            return "Rechazado - Requiere Revision"
        else:
            return "Rechazado"

    if documento_completado:
        return "Firmado"

    # Estados por rol de usuario - usando base de datos como fuente unica
    if rol_usuario == "creator":
        if status == "draft":
            return get_display_state_name("editing", schema_name=schema_name)
        elif status in ["pending_signature", "signed"]:
            return get_display_state_name("signing_process", schema_name=schema_name)
        elif status == "pending_numeration":
            return "Pendiente de Numeracion"  # Estado especifico, no en tabla de display

    elif rol_usuario == "signer":
        if usuario_ya_firmo:
            if numeracion_pendiente:
                return "Firmado - Esperando Numeracion"  # Estado compuesto especifico
            else:
                return "Firmado - En Proceso"  # Estado compuesto especifico
        else:
            if status == "pending_signature":
                return "Pendiente de mi Firma"  # Estado especifico del usuario
            else:
                return "Esperando Turno de Firma"  # Estado especifico del usuario

    elif rol_usuario == "numerator":
        if es_numerador:
            if status == "pending_numeration":
                return "Listo para Numerar"  # Estado especifico del numerador
            elif status == "signed":
                return "Firmado - Esperando mi Numeracion"  # Estado especifico del numerador
            else:
                return get_display_state_name("signing_process", schema_name=schema_name)
        else:
            return get_display_state_name(status, schema_name=schema_name)

    # Estado por defecto usando la base de datos
    return get_display_state_name(status, schema_name=schema_name)



def get_document_basic_info(document_id: str, *, schema_name: str) -> Optional[Dict[str, Any]]:
    """
    Obtiene informacion basica de un documento.

    Args:
        document_id: UUID del documento
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Dict con informacion basica o None si no existe
    """
    query = """
        SELECT
            d.id,
            d.reference,
            d.status,
            d.created_at,
            d.updated_at,
            d.creator_id,
            dt.name as document_type_name,
            dt.acronym as document_type_acronym
        FROM documents d
        JOIN document_types dt ON d.document_type_id = dt.id
        WHERE d.id = %s
    """

    result = execute_query(query, [document_id], schema_name=schema_name)

    if not result:
        return None

    doc = result[0]
    return {
        "document_id": doc['id'],
        "reference": doc['reference'],
        "status": doc['status'],
        "creator_id": doc['creator_id'],
        "document_type": {
            "name": doc['document_type_name'],
            "acronym": doc['document_type_acronym']
        },
        "created_at": doc['created_at'].isoformat() if doc['created_at'] else None,
        "updated_at": doc['updated_at'].isoformat() if doc['updated_at'] else None
    }
