"""
Definiciones de modelos de Pydantic para validación y documentación de la API.
"""

from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from typing import Optional, List, Dict, Any

# Esquemas para Documentos
class UserDocument(BaseModel):
    """Modelo para representar un documento del usuario"""
    document_id: str = Field(..., description="UUID del documento")
    reference: str = Field(..., description="Asunto o referencia del documento")
    status: str = Field(..., description="Estado del documento (draft, sent_to_sign, signed, etc.)")
    display_status: str = Field(..., description="Estado para mostrar en frontend (En edición, En proceso de firma, Firmar ahora, Firmado)")
    doc_type: str = Field(..., description="Tipo de documento (draft o official)")
    last_modified_at: Optional[datetime] = Field(None, description="Fecha de última modificación")
    last_editor_id: Optional[str] = Field(None, description="UUID del último editor")
    last_editor_full_name: Optional[str] = Field(None, description="Nombre completo del último editor")
    last_editor_profile_picture_id: Optional[str] = Field(None, description="ID de la foto de perfil del editor")
    acronym: Optional[str] = Field(None, description="Acrónimo del tipo de documento (IF, ME, etc.)")
    official_number: Optional[str] = Field(None, description="Número oficial del documento")
    rol_usuario: str = Field(..., description="Rol del usuario (creador, firmante, numerador, otro)")
    usuario_ya_firmo: bool = Field(..., description="Indica si el usuario ya firmó el documento")
    todos_firmantes_comunes_firmaron: bool = Field(..., description="Indica si todos los firmantes comunes ya firmaron")
    
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                # Ejemplo 1: Documento en edición para el creador
                {
                    "document_id": "550e8400-e29b-41d4-a716-446655440000",
                    "reference": "Solicitud de presupuesto para obras",
                    "status": "draft",
                    "display_status": "En edición",
                    "doc_type": "draft",
                    "last_modified_at": "2025-09-23T14:30:45",
                    "last_editor_id": "ea77e311-5bc3-467e-900b-41d9fc1503a7",
                    "last_editor_full_name": "Juan Pérez",
                    "last_editor_profile_picture_id": "ae45f212-5bc3-467e-900b-41d9fc150123",
                    "acronym": "ME",
                    "official_number": None,
                    "rol_usuario": "creador",
                    "usuario_ya_firmo": False,
                    "todos_firmantes_comunes_firmaron": False
                },
                # Ejemplo 2: Documento pendiente de firma para un firmante
                {
                    "document_id": "550e8400-e29b-41d4-a716-446655440111",
                    "reference": "Aprobación de presupuesto municipal",
                    "status": "sent_to_sign",
                    "display_status": "Firmar ahora",
                    "doc_type": "draft",
                    "last_modified_at": "2025-09-24T10:15:30",
                    "last_editor_id": "ea77e311-5bc3-467e-900b-41d9fc1503a7",
                    "last_editor_full_name": "Juan Pérez",
                    "last_editor_profile_picture_id": "ae45f212-5bc3-467e-900b-41d9fc150123",
                    "acronym": "IF",
                    "official_number": None,
                    "rol_usuario": "firmante",
                    "usuario_ya_firmo": False,
                    "todos_firmantes_comunes_firmaron": False
                }
            ]
        }
    )

class DocumentDisplayState(BaseModel):
    """Modelo para representar un estado visual de documento"""
    display_state_code: str = Field(..., description="Código único del estado visual")
    display_state_name: str = Field(..., description="Nombre para mostrar del estado visual")
    description: Optional[str] = Field(None, description="Descripción del estado visual")

class DocumentStatesResponse(BaseModel):
    """Respuesta para la lista de estados de documentos"""
    states: List[DocumentDisplayState] = Field(..., description="Lista de estados visuales disponibles")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "states": [
                    {
                        "display_state_code": "EDITING",
                        "display_state_name": "En edición",
                        "description": "Documento en proceso de edición"
                    },
                    {
                        "display_state_code": "SIGNING_PROCESS",
                        "display_state_name": "En proceso de firma",
                        "description": "Documento en proceso de firma por varios usuarios"
                    },
                    {
                        "display_state_code": "SIGN_NOW",
                        "display_state_name": "Firmar ahora",
                        "description": "Documento que requiere la firma del usuario"
                    },
                    {
                        "display_state_code": "SIGNED",
                        "display_state_name": "Firmado",
                        "description": "Documento ya firmado"
                    }
                ]
            }
        }
    )

class UserDocumentsResponse(BaseModel):
    """Respuesta para la lista de documentos del usuario"""
    total: int = Field(..., description="Total de documentos encontrados")
    page: int = Field(..., description="Número de página actual")
    page_size: int = Field(..., description="Número de documentos por página")
    total_pages: int = Field(..., description="Número total de páginas")
    documents: List[UserDocument] = Field(..., description="Lista de documentos del usuario")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "total": 25,
                "page": 1,
                "page_size": 10,
                "total_pages": 3,
                "documents": [
                    {
                        "document_id": "550e8400-e29b-41d4-a716-446655440000",
                        "reference": "Solicitud de presupuesto para obras",
                        "status": "draft",
                        "display_status": "En edición",
                        "doc_type": "draft",
                        "last_modified_at": "2025-09-23T14:30:45",
                        "last_editor_id": "ea77e311-5bc3-467e-900b-41d9fc1503a7",
                        "last_editor_full_name": "Juan Pérez",
                        "last_editor_profile_picture_id": "ae45f212-5bc3-467e-900b-41d9fc150123",
                        "acronym": "ME",
                        "official_number": None,
                        "rol_usuario": "creador",
                        "usuario_ya_firmo": False,
                        "todos_firmantes_comunes_firmaron": False
                    },
                    {
                        "document_id": "660e8400-e29b-41d4-a716-446655440111",
                        "reference": "Resolución de trámite administrativo",
                        "status": "signed",
                        "display_status": "Firmado",
                        "doc_type": "official",
                        "last_modified_at": "2025-09-22T09:15:30",
                        "last_editor_id": "fb88e311-5bc3-467e-900b-41d9fc150456",
                        "last_editor_full_name": "María López",
                        "last_editor_profile_picture_id": "cd45f212-5bc3-467e-900b-41d9fc150789",
                        "acronym": "IF",
                        "official_number": "IF-2025-000123-TNV-INT",
                        "rol_usuario": "firmante",
                        "usuario_ya_firmo": True,
                        "todos_firmantes_comunes_firmaron": True
                    }
                ]
            }
        }
    )

class DocumentType(BaseModel):
    """
    Modelo para representar un tipo de documento en el sistema.
    
    Cada tipo de documento tiene un nombre completo (name) y un código abreviado (acronym)
    que se utiliza para identificarlo de forma rápida en la interfaz y en los documentos oficiales.
    """
    id: int = Field(
        ..., 
        description="ID numérico del tipo de documento",
        json_schema_extra={"example": 1}
    )
    name: str = Field(
        ..., 
        description="Nombre completo del tipo de documento (ej: 'Informe Final', 'Memorándum')",
        json_schema_extra={"example": "Informe Final"}
    )
    acronym: str = Field(
        ..., 
        description="Acrónimo o abreviatura del tipo de documento que se usa para identificarlo (ej: 'IF', 'ME')",
        json_schema_extra={"example": "IF"}
    )
    
    model_config = {
        "json_schema_extra": {
            "examples": [
                {"id": 1, "name": "Informe Final", "acronym": "IF"}
            ]
        }
    }
    
class DocumentTypesResponse(BaseModel):
    """
    Respuesta para el endpoint /document-types.
    
    Contiene una lista de todos los tipos de documentos configurados en el sistema.
    Estos tipos de documentos son utilizados para categorizar los documentos
    al momento de su creación y para filtrar las búsquedas.
    
    Ejemplo de respuesta:
    ```json
    {
      "document_types": [
        {
          "name": "Informe Final",
          "acronym": "IF"
        },
        {
          "name": "Memorándum",
          "acronym": "ME"
        }
      ]
    }
    ```
    """
    document_types: List[DocumentType] = Field(
        ..., 
        description="Lista de tipos de documentos disponibles en el sistema con nombre y acrónimo",
        json_schema_extra={
            "example": [
                {"id": 1, "name": "Informe Final", "acronym": "IF"},
                {"id": 2, "name": "Memorándum", "acronym": "ME"}
            ]
        }
    )
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "document_types": [
                    {
                        "id": 1,
                        "name": "Informe Final",
                        "acronym": "IF"
                    },
                    {
                        "id": 2,
                        "name": "Memorándum",
                        "acronym": "ME"
                    },
                    {
                        "id": 3,
                        "name": "Oficio",
                        "acronym": "OF"
                    },
                    {
                        "id": 4,
                        "name": "Decreto",
                        "acronym": "DECRE"
                    }
                ]
            }
        }
    )

class CreateDocumentRequest(BaseModel):
    """Modelo para solicitud de creación de documento"""
    document_type_acronym: str = Field(..., description="Acrónimo del tipo de documento (IF, ME, OF, etc.)")
    reference: str = Field(..., min_length=1, max_length=250, description="Asunto o referencia del documento (máximo 250 caracteres)")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "document_type_acronym": "ME",
                "reference": "Solicitud de presupuesto para obras municipales"
            }
        }
    )

class CreateDocumentResponse(BaseModel):
    """Respuesta a la creación de un documento"""
    document_id: str = Field(..., description="UUID del documento creado")
    status: str = Field(..., description="Estado del documento creado (draft)")
    message: str = Field(..., description="Mensaje descriptivo del resultado de la operación")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "document_id": "550e8400-e29b-41d4-a716-446655440000",
                "status": "draft",
                "message": "Documento creado exitosamente"
            }
        }
    )

# Esquemas para Usuarios
class User(BaseModel):
    """Modelo para representar un usuario completo"""
    user_id: str = Field(..., description="UUID del usuario")
    auth_id: Optional[str] = Field(None, description="ID de Auth0 del usuario")
    full_name: str = Field(..., description="Nombre completo del usuario")
    email: str = Field(..., description="Email del usuario")
    cuit: Optional[str] = Field(None, description="CUIT del usuario")
    profile_picture_url: Optional[str] = Field(None, description="URL de la foto de perfil")
    sector_id: Optional[str] = Field(None, description="UUID del sector al que pertenece")
    sector_acronym: Optional[str] = Field(None, description="Acrónimo/nombre del sector (ej: SECOBRA, MESA)")
    department_id: Optional[str] = Field(None, description="UUID del departamento")
    department_name: Optional[str] = Field(None, description="Nombre del departamento (ej: Secretaría de Obras)")
    department_acronym: Optional[str] = Field(None, description="Acrónimo del departamento")
    last_access: Optional[datetime] = Field(None, description="Último acceso del usuario")
    created_at: Optional[datetime] = Field(None, description="Fecha de creación del usuario")
    default_seal_id: Optional[int] = Field(None, description="ID del sello por defecto")
    default_seal_name: Optional[str] = Field(None, description="Nombre del sello por defecto")
    default_seal_acronym: Optional[str] = Field(None, description="Acrónimo del sello por defecto")
    estado: int = Field(..., description="Estado del usuario (1=activo, 0=inactivo)")
    additional_sectors: Optional[List[dict]] = Field(None, description="Sectores adicionales con permisos")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_id": "550e8400-e29b-41d4-a716-446655440000",
                "auth_id": "auth0|64a1b2c3d4e5f67890abcdef",
                "full_name": "Juan Pérez González",
                "email": "juan.perez@municipalidad.cl",
                "cuit": "20-12345678-9",
                "profile_picture_url": "https://s.gravatar.com/avatar/123abc.jpg",
                "sector_id": "770e8400-e29b-41d4-a716-446655440222",
                "sector_acronym": "SECOBRA",
                "department_id": "880e8400-e29b-41d4-a716-446655440333",
                "department_name": "Secretaría de Obras",
                "department_acronym": "SECOBR",
                "last_access": "2025-09-26T10:30:00",
                "created_at": "2025-09-01T09:00:00",
                "default_seal_id": 1,
                "default_seal_name": "Innovador",
                "default_seal_acronym": "INNO",
                "estado": 1
            }
        }
    )

class CreateUserRequest(BaseModel):
    """Modelo para solicitud de creación de usuario"""
    auth_id: str = Field(..., description="ID de Auth0 del usuario")
    full_name: str = Field(..., min_length=1, max_length=255, description="Nombre completo del usuario")
    email: str = Field(..., description="Email del usuario")
    cuit: Optional[str] = Field(None, description="CUIT del usuario")
    profile_picture_url: Optional[str] = Field(None, description="URL de la foto de perfil")
    sector_id: Optional[str] = Field(None, description="UUID del sector")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "auth_id": "auth0|64a1b2c3d4e5f67890abcdef",
                "full_name": "Juan Pérez González",
                "email": "juan.perez@municipalidad.cl",
                "cuit": "20-12345678-9",
                "profile_picture_url": "https://s.gravatar.com/avatar/123abc.jpg",
                "sector_id": "770e8400-e29b-41d4-a716-446655440222"
            }
        }
    )

class CreateUserResponse(BaseModel):
    """Respuesta a la creación de un usuario"""
    user_id: str = Field(..., description="UUID del usuario creado")
    message: str = Field(..., description="Mensaje descriptivo del resultado")
    user: User = Field(..., description="Datos del usuario creado")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_id": "550e8400-e29b-41d4-a716-446655440000",
                "message": "Usuario creado exitosamente",
                "user": {
                    "user_id": "550e8400-e29b-41d4-a716-446655440000",
                    "auth_id": "auth0|64a1b2c3d4e5f67890abcdef",
                    "full_name": "Juan Pérez González",
                    "email": "juan.perez@municipalidad.cl",
                    "cuit": "20-12345678-9",
                    "profile_picture_id": "660e8400-e29b-41d4-a716-446655440111",
                    "sector_id": "770e8400-e29b-41d4-a716-446655440222",
                    "last_access": "2025-09-26T10:30:00",
                    "created_at": "2025-09-26T10:30:00",
                    "default_seal_id": None,
                    "estado": 1
                }
            }
        }
    )

class UpdateUserRequest(BaseModel):
    """Modelo para actualización de perfil de usuario"""
    full_name: Optional[str] = Field(None, min_length=1, max_length=255, description="Nombre completo del usuario")
    cuit: Optional[str] = Field(None, description="CUIT del usuario")
    profile_picture_url: Optional[str] = Field(None, description="URL de la foto de perfil")
    sector_id: Optional[str] = Field(None, description="UUID del sector")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "full_name": "Juan Carlos Pérez González",
                "cuit": "20-12345678-9",
                "profile_picture_url": "https://s.gravatar.com/avatar/456def.jpg"
            }
        }
    )

class SectorPermission(BaseModel):
    """Modelo para representar permisos de un sector"""
    sector_id: str = Field(..., description="UUID del sector")
    sector_acronym: str = Field(..., description="Acrónimo del sector (ej: SECOBRA, MESA)")
    department_id: str = Field(..., description="UUID del departamento")
    department_name: str = Field(..., description="Nombre del departamento")
    department_acronym: str = Field(..., description="Acrónimo del departamento")
    can_view: bool = Field(..., description="Permiso para ver documentos/expedientes del sector")
    can_edit: bool = Field(..., description="Permiso para editar documentos/expedientes del sector")
    is_primary: bool = Field(..., description="Indica si es el sector principal del usuario")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "sector_id": "770e8400-e29b-41d4-a716-446655440222",
                "sector_acronym": "SECOBRA",
                "department_id": "880e8400-e29b-41d4-a716-446655440333",
                "department_name": "Secretaría de Obras",
                "department_acronym": "SECOBR",
                "can_view": True,
                "can_edit": True,
                "is_primary": True
            }
        }
    )


class AuthenticatedUser(BaseModel):
    """Modelo para usuario autenticado (incluye claims del JWT)"""
    user_id: str = Field(..., description="UUID del usuario")
    auth_id: str = Field(..., description="ID de Auth0 del usuario")
    full_name: str = Field(..., description="Nombre completo del usuario")
    email: str = Field(..., description="Email del usuario")
    permissions: List[SectorPermission] = Field(default=[], description="Lista de sectores con permisos del usuario")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "user_id": "550e8400-e29b-41d4-a716-446655440000",
                "auth_id": "auth0|64a1b2c3d4e5f67890abcdef",
                "full_name": "Juan Pérez González",
                "email": "juan.perez@municipalidad.cl",
                "permissions": [
                    {
                        "sector_id": "770e8400-e29b-41d4-a716-446655440222",
                        "sector_acronym": "SECOBRA",
                        "department_id": "880e8400-e29b-41d4-a716-446655440333",
                        "department_name": "Secretaría de Obras",
                        "department_acronym": "SECOBR",
                        "can_view": True,
                        "can_edit": True,
                        "is_primary": True
                    },
                    {
                        "sector_id": "990e8400-e29b-41d4-a716-446655440444",
                        "sector_acronym": "MESA",
                        "department_id": "aa0e8400-e29b-41d4-a716-446655440555",
                        "department_name": "Mesa de Entradas",
                        "department_acronym": "MESA",
                        "can_view": True,
                        "can_edit": False,
                        "is_primary": False
                    }
                ]
            }
        }
    )


# Esquemas para Autocompletado de Documentos
class AutocompleteDocumentItem(BaseModel):
    """Modelo para un item de autocompletado de documento"""
    document_id: str = Field(..., description="UUID del documento")
    official_number: str = Field(..., description="Número oficial del documento")
    reference: str = Field(..., description="Referencia o asunto del documento")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "document_id": "550e8400-e29b-41d4-a716-446655440000",
                "official_number": "ANEXO-2025-000001-SMG-ADGEN",
                "reference": "Solicitud de presupuesto para obras"
            }
        }
    )


class AutocompleteDocumentsResponse(BaseModel):
    """Modelo de respuesta para autocompletado de documentos"""
    documents: List[AutocompleteDocumentItem] = Field(..., description="Lista de documentos encontrados")
    total: int = Field(..., description="Número total de documentos devueltos en esta página")
    query: str = Field(..., description="Texto de búsqueda proporcionado")
    page: int = Field(default=1, description="Número de página actual")
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "documents": [
                    {
                        "document_id": "550e8400-e29b-41d4-a716-446655440000",
                        "official_number": "ANEXO-2025-000001-SMG-ADGEN",
                        "reference": "Solicitud de presupuesto para obras"
                    },
                    {
                        "document_id": "550e8400-e29b-41d4-a716-446655440001",
                        "official_number": "ANEXO-2025-000002-SMG-ADGEN",
                        "reference": "Informe de auditoría"
                    }
                ],
                "total": 2,
                "query": "ANEXO-2025",
                "page": 1
            }
        }
    )