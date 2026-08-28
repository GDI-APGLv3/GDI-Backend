from typing import Any, Dict, Optional
from database import fetch_one
from shared.exceptions import DocumentNotFoundError


def extract_html_from_content(content: Optional[dict]) -> str:
    if not isinstance(content, dict):
        return ""
    if 'schema' in content and 'data' in content:
        from services.documents.ffcc_renderer import ffcc_to_html
        return ffcc_to_html(content.get('schema') or [], content.get('data') or {})
    return content.get('html') or content.get('detalle', '')


async def get_official_document_content(document_id: str, schema_name: str) -> Dict[str, Any]:
    result = await fetch_one(
        """
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
        WHERE od.id = $1
          AND od.signed_at IS NOT NULL
        """,
        document_id,
        schema_name=schema_name,
    )

    if not result:
        raise DocumentNotFoundError(f"Documento oficial {document_id} no encontrado")

    html_content = extract_html_from_content(result['content'])

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
