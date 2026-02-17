"""
Importing: Gestion de documentos importados (PDFs externos).

NOTA: Carpeta 'importing' en lugar de 'import' porque es palabra reservada Python.

Modulos:
- import_service.py: Crear/reemplazar PDFs importados
"""

from .import_service import (
    create_imported_document,
    replace_imported_pdf,
)

__all__ = [
    # import_service
    "create_imported_document",
    "replace_imported_pdf",
]
