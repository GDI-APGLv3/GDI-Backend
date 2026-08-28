
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional

USERS_BATCH_MAX_IDS = 50


class UsersBatchRequest(BaseModel):
    ids: List[str] = Field(
        ...,
        min_length=1,
        max_length=USERS_BATCH_MAX_IDS,
        description=f"Lista de UUIDs de usuario (1 a {USERS_BATCH_MAX_IDS})"
    )

    @field_validator("ids")
    @classmethod
    def _validate_uuids(cls, value: List[str]) -> List[str]:
        import uuid as uuid_module
        for raw_id in value:
            try:
                uuid_module.UUID(str(raw_id))
            except (ValueError, AttributeError, TypeError):
                raise ValueError(f"'{raw_id}' no es un UUID válido")
        return value

class UserSearchDetail(BaseModel):
    user_id: Optional[str] = Field(None, description="UUID único del usuario (null si es email virtual)")
    full_name: str = Field(..., description="Nombre completo del usuario")
    email: Optional[str] = Field(None, description="Email del usuario (especialmente para usuarios virtuales)")
    department_acronym: Optional[str] = Field(None, description="Acrónimo del departamento (ej: ADGEN, OBPU)")
    sector_acronym: Optional[str] = Field(None, description="Acrónimo del sector (ej: MESA, RRHH)")
    sector_color: Optional[str] = Field(None, description="Color primario del sector (hex)")
    seal_name: Optional[str] = Field(None, description="Nombre del sello asignado")
    profile_picture_url: Optional[str] = Field(None, description="URL de foto de perfil")
    is_active: bool = Field(True, description="Estado activo del usuario")

class UserSearchResponse(BaseModel):
    users: List[UserSearchDetail] = Field(..., description="Lista de usuarios encontrados")
    total_found: int = Field(..., description="Total de usuarios que coinciden")
    search_query: str = Field(..., description="Término de búsqueda utilizado")
    
    class Config:
        validate_assignment = True
        use_enum_values = True