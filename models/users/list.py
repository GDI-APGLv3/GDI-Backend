"""
Modelos Pydantic para el endpoint de listado de usuarios.
"""

from pydantic import BaseModel, Field
from typing import List


class UserListItem(BaseModel):
    """Modelo para un item individual en la lista de usuarios."""
    user_id: str = Field(..., description="UUID único del usuario")
    full_name: str = Field(..., description="Nombre completo del usuario")
    email: str = Field(..., description="Correo electrónico del usuario")


class UserListResponse(BaseModel):
    """Modelo para la respuesta del endpoint de listado de usuarios."""
    users: List[UserListItem] = Field(..., description="Lista de usuarios activos")
    total: int = Field(..., description="Número total de usuarios")
