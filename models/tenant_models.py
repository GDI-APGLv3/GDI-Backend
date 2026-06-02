"""
Modelos Pydantic para sistema multi-tenant.
Define estructuras de datos para acceso de usuarios a múltiples municipalidades.
"""

from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict, EmailStr


class TenantAccess(BaseModel):
    """
    Representa el acceso de un usuario a una municipalidad (tenant).

    SEC-31 (REVERTIDO por hotfix 2026-05-31): la idea era no exponer schema_name al
    cliente y usar municipality_id (UUID opaco) como identificador público. Se revirtió
    porque el frontend y el middleware (X-Tenant-Schema) dependen de schema_name, y
    ocultarlo rompía el login en DEV y PRD. Hoy schema_name SÍ se serializa.

    municipality_id se sigue devolviendo (informativo) pero el identificador de tenant
    en uso es schema_name.
    TODO(S4-007/SEC-31): para volver a ocultar schema_name hay que primero migrar
    frontend + middleware a municipality_id (endpoint de resolución). Ver VersionJUNIO.
    """
    model_config = ConfigDict(populate_by_name=True)

    # SEC-31 (revertido por hotfix): el frontend aún depende de schema_name para el
    # header X-Tenant-Schema y el middleware lo valida contra whitelist. Re-exponemos
    # schema_name hasta completar SEC-31 (endpoint de resolución municipality_id->schema).
    schema_name: str = Field(
        ...,
        description="Nombre del schema PostgreSQL (usado por el cliente como X-Tenant-Schema)",
    )

    municipality_id: Optional[str] = Field(
        None,
        description="UUID opaco de la municipalidad (identificador público para el cliente)",
        example="a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    )

    display_name: str = Field(
        ...,
        description="Nombre para mostrar de la municipalidad",
        example="Municipalidad del Futuro"
    )

    is_default: bool = Field(
        default=False,
        description="Indica si es la municipalidad por defecto del usuario"
    )

    logo_url: Optional[str] = Field(
        None,
        description="URL del logo del municipio en R2",
        example="https://pub-xxx.r2.dev/logos/smg-logo.png"
    )

    isologo_url: Optional[str] = Field(
        None,
        description="URL del isologo del municipio (menú colapsado)",
        example="https://pub-xxx.r2.dev/logos/smg-isologo.png"
    )

    primary_color: Optional[str] = Field(
        None,
        description="Color primario del municipio (hex)",
        example="#1E3A8A"
    )


class OnboardingUser(BaseModel):
    """
    Información básica del usuario para onboarding multi-tenant.
    """
    email: EmailStr = Field(
        ...,
        description="Email del usuario autenticado",
        example="juan.perez@example.com"
    )

    full_name: str = Field(
        ...,
        description="Nombre completo del usuario",
        example="Juan Pérez"
    )

    profile_picture_url: Optional[str] = Field(
        None,
        description="URL de foto de perfil desde Auth0",
        example="https://s.gravatar.com/avatar/123.jpg"
    )


class UserProfile(BaseModel):
    """
    Perfil completo del usuario en una municipalidad específica.
    """
    user_id: str = Field(
        ...,
        description="UUID del usuario en el sistema",
        example="550e8400-e29b-41d4-a716-446655440000"
    )

    email: str = Field(
        ...,
        description="Email del usuario",
        example="juan.perez@example.com"
    )

    sector_id: Optional[str] = Field(
        None,
        description="UUID del sector asignado",
        example="770e8400-e29b-41d4-a716-446655440222"
    )

    department_id: Optional[str] = Field(
        None,
        description="UUID del departamento",
        example="880e8400-e29b-41d4-a716-446655440333"
    )

    estado: int = Field(
        ...,
        description="Estado del usuario (1=activo, 0=inactivo)",
        example=1
    )


class OnboardingResponse(BaseModel):
    """
    Response del endpoint /api/auth/onboarding para usuarios multi-tenant.
    Retorna la lista de municipalidades a las que tiene acceso.
    """
    user: OnboardingUser = Field(
        ...,
        description="Información básica del usuario"
    )

    tenants: List[TenantAccess] = Field(
        ...,
        description="Lista de municipalidades (tenants) a las que tiene acceso"
    )

    default_tenant: Optional[str] = Field(
        None,
        description="schema_name de la municipalidad por defecto (lo usa el cliente como X-Tenant-Schema). "
                    "SEC-31 fue revertido: volvió a ser schema_name, no municipality_id.",
        example="100_test"
    )

    default_profile: Optional[UserProfile] = Field(
        None,
        description="Perfil del usuario en la municipalidad por defecto (si existe)"
    )
