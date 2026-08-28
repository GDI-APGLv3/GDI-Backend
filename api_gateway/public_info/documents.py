from typing import Optional

from database import fetch_one
from shared.logging import get_logger
from config.constants import SEMANTIC_SEARCH_EXCLUDED_TYPES
from services.documents.retrieval.content import extract_html_from_content

from api_gateway.public_info.queries import DOCUMENT_CONTENT_PUBLIC_SQL

logger = get_logger(__name__)


async def get_document_content_public(*, schema_name: str, document_id: str) -> Optional[dict]:
    row = await fetch_one(
        DOCUMENT_CONTENT_PUBLIC_SQL,
        document_id,
        list(SEMANTIC_SEARCH_EXCLUDED_TYPES),
        schema_name=schema_name,
    )
    if not row:
        return None

    html_content = extract_html_from_content(row["content"])
    return {
        "document_id": str(row["id"]),
        "official_number": row["official_number"],
        "reference": row["reference"],
        "document_type": {
            "name": row["document_type_name"] or "Sin tipo",
            "acronym": row["document_type_acronym"] or "",
        },
        "content": {"html": html_content, "format": "html"},
        "signed_at": row["signed_at"].isoformat() if row["signed_at"] else None,
    }
