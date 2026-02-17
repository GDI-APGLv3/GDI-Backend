"""
Endpoints para pruebas de autenticación con Auth0.
Permite verificar que la integración funcione correctamente.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.responses import JSONResponse
from auth import get_current_user
from models.schemas import AuthenticatedUser
import os
from database import AUTH0_DOMAIN, AUTH0_AUDIENCE, AUTH0_CLIENT_ID

router = APIRouter(
    prefix="/api/auth",
    tags=["🔐 Authentication"],
    responses={
        401: {"description": "No autorizado - Token inválido o ausente"},
        403: {"description": "Acceso denegado - Permisos insuficientes"},
    }
)

@router.get("/me")
async def get_current_user_info(request: Request, current_user: AuthenticatedUser = Depends(get_current_user)):
    """
    Obtiene información del usuario autenticado actual.
    
    **Uso en Testing Mode:**
    - Header: `X-User-ID: {uuid-del-usuario}`
    - O Bearer token con UUID en lugar de JWT
    
    **Uso en Producción:**
    - Header: `Authorization: Bearer {jwt-token-de-auth0}`
    
    Returns:
        Información del usuario autenticado
    """
    return {
        "status": "authenticated",
        "user": {
            "user_id": request.state.tenant_user_id,
            "auth_id": current_user.auth_id,
            "full_name": current_user.full_name,
            "email": current_user.email,
            "permissions": current_user.permissions
        },
        "message": "Usuario autenticado correctamente"
    }

@router.get("/config")
async def get_auth_config():
    """
    Obtiene configuración pública de Auth0 para el frontend.
    No incluye información sensible como client_secret.
    
    Returns:
        Configuración pública de Auth0
    """
    return {
        "auth0": {
            "domain": AUTH0_DOMAIN,
            "clientId": AUTH0_CLIENT_ID,
            "audience": AUTH0_AUDIENCE,
            "scope": "openid profile email"
        },
        "testing_mode": os.getenv("TESTING_MODE", "false").lower() == "true"
    }

@router.post("/exchange-token")
async def exchange_auth0_token(token: str):
    """
    Intercambia un token de Auth0 por información del usuario.
    Útil para debugging y verificación de tokens.

    ⚠️ **SOLO DISPONIBLE EN TESTING MODE**
    En producción (TESTING_MODE=false), este endpoint no está disponible.

    Args:
        token: Token JWT de Auth0

    Returns:
        Información decodificada del token
    """
    # 🔒 Verificar si estamos en modo testing
    testing_mode = os.getenv("TESTING_MODE", "false").lower() == "true"

    if not testing_mode:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Endpoint no disponible en modo producción"
        )

    # ✅ Solo ejecuta esto si TESTING_MODE=true
    try:
        from auth import verify_token
        payload = verify_token(token)
        
        return {
            "status": "valid",
            "payload": payload,
            "user_info": {
                "auth_id": payload.get("sub"),
                "email": payload.get("email"),
                "permissions": payload.get("permissions", []),
                "issued_at": payload.get("iat"),
                "expires_at": payload.get("exp")
            }
        }
    except HTTPException as e:
        return JSONResponse(
            status_code=e.status_code,
            content={
                "status": "invalid",
                "error": e.detail
            }
        )