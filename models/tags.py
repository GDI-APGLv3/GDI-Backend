"""
Define etiquetas (tags) y descripciones para agrupar endpoints en la documentación.
"""
from enum import Enum

class Tags(str, Enum):
    """Categorías para agrupar endpoints en la documentación."""
    DOCUMENTOS = "documentos"
    SECTORS = "sectors"
    USERS = "users"
    SISTEMA = "sistema"

# Descripciones de las etiquetas
# Orden de aparición en Swagger: DOCUMENTOS → SECTORS → USERS → SISTEMA
tag_metadata = [
    {
        "name": Tags.DOCUMENTOS,
        "description": "Operaciones relacionadas con documentos (creación, consulta, actualización, eliminación)"
    },
    {
        "name": Tags.SECTORS,
        "description": "Gestión de sectores y departamentos (listado, asignación, organización)"
    },
    {
        "name": Tags.USERS,
        "description": "Gestión de usuarios (perfiles, documentos por usuario, búsqueda, selección de firmantes)"
    },
    {
        "name": Tags.SISTEMA,
        "description": "Operaciones relacionadas con el sistema (pruebas, monitoreo)"
    }
]