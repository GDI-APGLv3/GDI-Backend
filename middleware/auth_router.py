
from dataclasses import dataclass
from typing import Optional

from shared.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ResolvedAuth:
    auth_source: str
    schema_name: Optional[str] = None
    tenant_user_id: Optional[str] = None
    tenant_email: Optional[str] = None
    is_jwt: bool = False


async def resolve_auth(request) -> Optional[ResolvedAuth]:
    api_key = request.headers.get("X-API-Key")
    auth_header = request.headers.get("Authorization", "")

    if api_key:
        from api_gateway.auth_rest import validate_rest_api_key
        user_id = request.headers.get("X-User-ID")
        ctx = await validate_rest_api_key(api_key, user_id=user_id, request=request)
        return ResolvedAuth(
            auth_source="api_key",
            schema_name=ctx.schema_name,
            tenant_user_id=ctx.user_id,
            tenant_email=None,
        )

    if auth_header.startswith("Bearer "):
        return ResolvedAuth(auth_source="jwt", is_jwt=True)

    return None
