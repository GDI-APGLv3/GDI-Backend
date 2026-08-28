
from pydantic import BaseModel, Field
from typing import List


class UserListItem(BaseModel):
    user_id: str = Field(..., description="UUID único del usuario")
    full_name: str = Field(..., description="Nombre completo del usuario")
    email: str = Field(..., description="Correo electrónico del usuario")


class UserListResponse(BaseModel):
    users: List[UserListItem] = Field(..., description="Lista de usuarios activos")
    total: int = Field(..., description="Número total de usuarios")
