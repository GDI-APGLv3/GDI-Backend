"""
Lifecycle: Operaciones del ciclo de vida de documentos.

Contiene:
- creation.py: Crear documentos
- editing.py: Editar y guardar
- deletion.py: Soft delete
- rejection.py: Rechazo de documentos
"""

# Creation
from .creation import create_document

# Editing
from .editing import (
    get_document_details_for_editing,
    save_document_changes,
    check_document_can_be_edited
)

# Deletion
from .deletion import (
    delete_document,
    can_user_delete_document
)

# Rejection
from .rejection import (
    reject_document,
    get_document_rejections,
    get_rejected_documents_for_user,
    can_user_reject_document
)

__all__ = [
    # Creation
    "create_document",
    # Editing
    "get_document_details_for_editing",
    "save_document_changes",
    "check_document_can_be_edited",
    # Deletion
    "delete_document",
    "can_user_delete_document",
    # Rejection
    "reject_document",
    "get_document_rejections",
    "get_rejected_documents_for_user",
    "can_user_reject_document",
]
