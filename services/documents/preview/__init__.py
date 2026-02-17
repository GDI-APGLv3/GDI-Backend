"""
Preview Module - Servicio de previsualización de documentos refactorizado.

Arquitectura modular aplicando Clean Code y Single Responsibility Principle.
"""

from .core import generate_document_preview

# Exportar solo la función principal
__all__ = ['generate_document_preview']