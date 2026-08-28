
from typing import Dict, Any, Optional
from database import fetch_one


async def get_document_basic_info(document_id: str, *, schema_name: str) -> Optional[Dict[str, Any]]:
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
        WHERE d.id = $1
    """

    row = await fetch_one(query, document_id, schema_name=schema_name)

    if not row:
        return None

    doc = dict(row)
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
