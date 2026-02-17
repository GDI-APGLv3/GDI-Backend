"""
Servicio para obtener contenido de documentos oficiales.
Usado por el MCP para que el agente IA pueda leer el contenido HTML de documentos firmados.

DEPRECATED: Este archivo es un proxy para backward compatibility.
La implementacion real esta en: services/documents/retrieval/content.py
"""

# Re-export desde retrieval para backward compatibility
from .retrieval.content import get_official_document_content

__all__ = ["get_official_document_content"]
