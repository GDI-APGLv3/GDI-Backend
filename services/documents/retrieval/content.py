"""
Servicio para obtener contenido de documentos oficiales.
Usado por el MCP para que el agente IA pueda leer el contenido HTML de documentos firmados.
"""
from typing import Dict, Any
from database import execute_query
from shared.exceptions import DocumentNotFoundError


def get_official_document_content(document_id: str, schema_name: str) -> Dict[str, Any]:
    """
    Obtiene el contenido HTML de un documento oficial.

    Args:
        document_id: UUID del documento oficial
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Dict con document_id, official_number, reference, content (html),
        document_type (name, acronym), signed_at

    Raises:
        DocumentNotFoundError: Si el documento no existe
    """
    query = """
        SELECT
            od.id,
            od.official_number,
            od.reference,
            od.content,
            od.signed_at,
            dt.name as document_type_name,
            dt.acronym as document_type_acronym
        FROM official_documents od
        LEFT JOIN document_types dt ON od.document_type_id = dt.id
        WHERE od.id = %s
    """

    result = execute_query(query, (document_id,), fetch_one=True, schema_name=schema_name)

    if not result:
        raise DocumentNotFoundError(f"Documento oficial {document_id} no encontrado")

    # Extraer HTML del JSON (soporta 'html' y 'detalle' para compatibilidad)
    html_content = ""
    if result['content']:
        html_content = result['content'].get('html') or result['content'].get('detalle', '')

    return {
        "document_id": str(result['id']),
        "official_number": result['official_number'],
        "reference": result['reference'],
        "content": {
            "html": html_content,
            "format": "html"
        },
        "document_type": {
            "name": result['document_type_name'] or "Sin tipo",
            "acronym": result['document_type_acronym'] or ""
        },
        "signed_at": result['signed_at'].isoformat() if result['signed_at'] else None
    }
