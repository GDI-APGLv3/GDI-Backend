"""
Modelos Pydantic para sistema multi-tenant.
Define estructuras de datos para acceso de usuarios a múltiples municipalidades.
"""

from typing import Optional, List
from pydantic import BaseModel, Field, EmailStr


class TenantAccess(BaseModel):
    """
    Representa el acceso de un usuario a una municipalidad (tenant).
    """
    schema_name: str = Field(
        ...,
        description="Nombre del schema PostgreSQL de la municipalidad",
        example="san_miguel"
    )

    display_name: str = Field(
        ...,
        description="Nombre para mostrar de la municipalidad",
        example="Municipalidad de San Miguel"
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
        description="Schema name de la municipalidad por defecto",
        example="san_miguel"
    )

    default_profile: Optional[UserProfile] = Field(
        None,
        description="Perfil del usuario en la municipalidad por defecto (si existe)"
    )
