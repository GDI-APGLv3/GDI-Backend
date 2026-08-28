
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict, EmailStr


class TenantAccess(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

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
