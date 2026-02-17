"""
Modelos Pydantic para sistema de onboarding de usuarios.
Maneja tanto usuarios nuevos como activación de usuarios invitados.
"""

from typing import Optional, Literal
from pydantic import BaseModel, Field, EmailStr


class OnboardingRequest(BaseModel):
    """
    Request para onboarding de usuario desde Auth0.
    
    Utilizado tanto para usuarios nuevos como para activación 
    de usuarios previamente invitados por email.
    """
    auth_id: str = Field(
        ...,
        description="ID de Auth0 del usuario autenticado",
        example="auth0|123456789abcdef"
    )
    
    email: EmailStr = Field(
        ...,
        description="Email del usuario verificado por Auth0",
        example="juan.perez@empresa.com"
    )
    
    full_name: str = Field(
        ...,
        min_length=2,
        max_length=100,
        description="Nombre completo del usuario desde Auth0",
        example="Juan Pérez López"
    )
    
    profile_picture_url: Optional[str] = Field(
        None,
        description="URL de foto de perfil desde Auth0 (opcional)",
        example="https://s.gravatar.com/avatar/123.jpg"
    )


class UserSummary(BaseModel):
    """Resumen de información del usuario para response."""
    
    user_id: str = Field(
        ...,
        description="UUID único del usuario en el sistema"
    )
    
    full_name: str = Field(
        ...,
        description="Nombre completo del usuario"
    )
    
    email: str = Field(
        ...,
        description="Email del usuario"
    )
    
    sector: Optional[dict] = Field(
        None,
        description="Información del sector asignado",
        example={
            "sector_id": "uuid",
            "name": "Secretaría de Hacienda",
            "department": "Administración"
        }
    )
    
    seal: Optional[dict] = Field(
        None,
        description="Información del sello asignado",
        example={
            "seal_id": "123",
            "name": "Sello Oficial Municipal"
        }
    )
    
    is_active: bool = Field(
        ...,
        description="Estado activo del usuario (siempre True tras onboarding exitoso)"
    )


class OnboardingResponse(BaseModel):
    """
    Response simplificado del proceso de onboarding.
    
    SEGÚN ESPECIFICACIONES DEL USUARIO:
    - NO incluye campo 'message' 
    - NO incluye campo 'assignments' o 'assigned_randomly'
    - Solo status, user info y metadato is_first_time
    """
    
    status: Literal["created", "activated", "existing_active"] = Field(
        ...,
        description="Tipo de operación realizada en el onboarding"
    )
    
    user: UserSummary = Field(
        ...,
        description="Información completa del usuario tras onboarding"
    )
    
    is_first_time: bool = Field(
        ...,
        description="True si es primer ingreso (created/activated), False si ya existía activo"
    )


class OnboardingError(BaseModel):
    """
    Response de error en onboarding.
    """
    
    error_type: str = Field(
        ...,
        description="Tipo de error ocurrido",
        example="user_already_exists"
    )
    
    message: str = Field(
        ...,
        description="Mensaje descriptivo del error"
    )
    
    email: str = Field(
        ...,
        description="Email que causó el conflicto"
    )
    
    existing_auth_id: Optional[str] = Field(
        None,
        description="Auth ID existente en caso de conflicto"
    )