
from typing import Optional, Literal
from pydantic import BaseModel, Field


class UserSummary(BaseModel):
    
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


