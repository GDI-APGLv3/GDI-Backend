"""
Retrieval: Obtencion y consulta de documentos.

Modulos:
- unified_details.py: Router automatico editing/signing
- content.py: Contenido HTML oficial
- official_search.py: Busqueda por numero
- official_url.py: URL de descarga
"""

from .unified_details import get_unified_document_details
from .content import get_official_document_content
from .official_search import search_official_document_by_number
from .official_url import get_official_document_url

__all__ = [
    # Unified Details
    "get_unified_document_details",
    # Content
    "get_official_document_content",
    # Official Search
    "search_official_document_by_number",
    # Official URL
    "get_official_document_url",
]
