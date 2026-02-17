"""
Servicio de edicion de documentos.

DEPRECATED: Este archivo es un proxy para backward compatibility.
La implementacion real esta en: services/documents/lifecycle/editing.py
"""

# Re-export desde lifecycle/editing para backward compatibility
from .lifecycle.editing import (
    get_document_details_for_editing,
    save_document_changes,
    check_document_can_be_edited
)

__all__ = [
    "get_document_details_for_editing",
    "save_document_changes",
    "check_document_can_be_edited"
]
