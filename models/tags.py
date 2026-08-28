from enum import Enum

class Tags(str, Enum):
    DOCUMENTOS = "documentos"
    SECTORS = "sectors"
    USERS = "users"
    SISTEMA = "sistema"

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