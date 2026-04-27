"""
Modelos Pydantic para búsqueda de usuarios - Clean Code Implementation.
Aplicando principios SOLID: Single Responsibility y Interface Segregation.
"""

from pydantic import BaseModel, Field
from typing import List, Optional

class UserSearchDetail(BaseModel):
    """
    Información detallada de un usuario para búsqueda.
    Aplicando Single Responsibility: Solo información necesaria para búsqueda.
    """
    user_id: Optional[str] = Field(None, description="UUID único del usuario (null si es email virtual)")
    full_name: str = Field(..., description="Nombre completo del usuario")
    email: Optional[str] = Field(None, description="Email del usuario (especialmente para usuarios virtuales)")
    department_acronym: Optional[str] = Field(None, description="Acrónimo del departamento (ej: ADGEN, OBPU)")
    sector_acronym: Optional[str] = Field(None, description="Acrónimo del sector (ej: MESA, RRHH)")
    seal_name: Optional[str] = Field(None, description="Nombre del sello asignado")
    profile_picture_url: Optional[str] = Field(None, description="URL de foto de perfil")
    is_active: bool = Field(True, description="Estado activo del usuario")

class UserSearchResponse(BaseModel):
    """
    Response para búsqueda de usuarios con metadata completa.
    Aplicando Interface Segregation: Separar datos de usuarios de metadata.
    """
    users: List[UserSearchDetail] = Field(..., description="Lista de usuarios encontrados")
    total_found: int = Field(..., description="Total de usuarios que coinciden")
    search_query: str = Field(..., description="Término de búsqueda utilizado")
    
    class Config:
        """Configuración del modelo para mejor rendimiento."""
        validate_assignment = True
        use_enum_values = True