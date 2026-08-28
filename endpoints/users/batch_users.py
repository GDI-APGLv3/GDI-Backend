
from fastapi import APIRouter, Depends

from models.users.search import UsersBatchRequest, UserSearchResponse
from models.tags import Tags
from auth import get_current_user
from services.users.search import get_users_by_ids
from shared.exceptions import exception_to_http_exception
from shared.dependencies import get_tenant_schema

router = APIRouter()


@router.post(
    "/users/batch",
    tags=[Tags.USERS],
    response_model=UserSearchResponse,
    summary="Obtener usuarios por lista de ids (batch)",
    dependencies=[Depends(get_current_user)],
    description="""
    **CONTRATO (GDI-139):**

    - Body: `{"ids": ["uuid1", "uuid2", ...]}` — mínimo 1, máximo 50 UUIDs.
      Más de 50 → 422.
    - Response: mismo shape que `GET /users/search` (`UserSearchResponse`),
      con los campos que el front hidrata hoy: `user_id`, `full_name`,
      `email`, `department_acronym`, `sector_acronym`, `seal_name`,
      `profile_picture_url`, `is_active`.
    - `search_query` en la respuesta queda vacío (no aplica a búsqueda batch).
    - Ids que no existen (o pertenecen a otro schema) simplemente no aparecen
      en `users` — no es un error.
    """,
)
async def batch_users_endpoint(
    body: UsersBatchRequest,
    schema_name: str = Depends(get_tenant_schema),
) -> UserSearchResponse:
    try:
        result = await get_users_by_ids(body.ids, schema_name=schema_name)
        return UserSearchResponse(search_query="", **result)
    except Exception as e:
        raise exception_to_http_exception(e)
