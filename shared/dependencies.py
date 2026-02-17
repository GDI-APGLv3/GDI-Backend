"""
Dependencies compartidas para inyección en endpoints FastAPI.
"""
from typing import Optional
from fastapi import Request, HTTPException, status


def get_tenant_schema(request: Request) -> str:
    """
    Dependency para obtener el schema del tenant actual.

    El schema es seteado por TenantMiddleware en request.state.schema_name.

    Args:
        request: Request de FastAPI

    Returns:
        Nombre del schema del tenant

    Raises:
        HTTPException 400: Si no hay schema disponible
    """
    schema = getattr(request.state, 'schema_name', None)

    if not schema:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Schema de tenant no disponible. Verifique autenticación."
        )

    return schema


def get_auth_source(request: Request) -> Optional[str]:
    """
    Dependency para obtener el origen de autenticación.

    Seteado por TenantMiddleware en request.state.auth_source.
    Valores posibles: jwt, api_key, mcp_oauth, testing, system

    Args:
        request: Request de FastAPI

    Returns:
        String con el origen de autenticación o None si no está disponible
    """
    return getattr(request.state, 'auth_source', None)
