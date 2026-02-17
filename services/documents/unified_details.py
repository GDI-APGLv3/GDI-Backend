"""
Servicio unificado para obtener detalles de documentos en cualquier estado.
Delega automaticamente al servicio apropiado segun el estado del documento.

DEPRECATED: Este archivo es un proxy para backward compatibility.
La implementacion real esta en: services/documents/retrieval/unified_details.py
"""

# Re-export desde retrieval para backward compatibility
from .retrieval.unified_details import get_unified_document_details

__all__ = ["get_unified_document_details"]
