from typing import Optional
from fastapi import Request, HTTPException, status


def get_tenant_schema(request: Request) -> str:
    schema = getattr(request.state, 'schema_name', None)

    if not schema:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Schema de tenant no disponible. Verifique autenticación."
        )

    return schema


def get_auth_source(request: Request) -> Optional[str]:
    return getattr(request.state, 'auth_source', None)
