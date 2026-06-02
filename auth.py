"""
Middleware de autenticación para validar tokens JWT de Auth0.
Este módulo contiene la lógica para validar tokens JWT y obtener información del usuario autenticado.
"""

import logging
import requests

logger = logging.getLogger(__name__)
from fastapi import HTTPException, Depends, status, Header, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt as pyjwt
from jwt.algorithms import RSAAlgorithm
from jwt.exceptions import PyJWTError, ExpiredSignatureError, InvalidAudienceError, InvalidIssuerError
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from database import AUTH0_DOMAIN, AUTH0_AUDIENCE, AUTH0_ALGORITHMS, TESTING_MODE
from services import user_service
from models.schemas import AuthenticatedUser, SectorPermission

# Configurar el esquema de seguridad Bearer
security = HTTPBearer(auto_error=False)  # auto_error=False para permitir testing mode

# Cache para las claves públicas de Auth0
_jwks_cache = None
_jwks_cache_expiry = None

def get_jwks() -> Dict[str, Any]:
    """
    Obtiene las claves públicas de Auth0 para validar JWT.
    Implementa un cache simple para evitar consultas frecuentes.
    
    Returns:
        Diccionario con las claves JWKS de Auth0
    """
    global _jwks_cache, _jwks_cache_expiry
    
    # Verificar si el cache sigue válido (30 minutos)
    if _jwks_cache and _jwks_cache_expiry and datetime.now() < _jwks_cache_expiry:
        return _jwks_cache
    
    try:
        # Obtener las claves públicas de Auth0
        jwks_url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
        response = requests.get(jwks_url, timeout=10)
        response.raise_for_status()
        
        _jwks_cache = response.json()
        _jwks_cache_expiry = datetime.now() + timedelta(minutes=30)
        
        return _jwks_cache
        
    except requests.RequestException as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"No se pudo obtener las claves de Auth0: {str(e)}"
        )

def get_rsa_key(token: str) -> Optional[Dict[str, Any]]:
    """
    Obtiene la clave RSA correspondiente al token JWT.
    
    Args:
        token: Token JWT a validar
        
    Returns:
        Diccionario con la clave RSA o None si no se encuentra
    """
    try:
        # Decodificar el header del JWT sin verificar
        unverified_header = pyjwt.get_unverified_header(token)
        
        # Obtener las claves JWKS
        jwks = get_jwks()
        
        # Buscar la clave correspondiente al kid del token
        for key in jwks.get("keys", []):
            if key.get("kid") == unverified_header.get("kid"):
                jwk_dict = {
                    "kty": key.get("kty"),
                    "kid": key.get("kid"),
                    "use": key.get("use"),
                    "n": key.get("n"),
                    "e": key.get("e")
                }
                return RSAAlgorithm.from_jwk(jwk_dict)
        
        return None
        
    except PyJWTError:
        return None

def verify_token(token: str) -> Dict[str, Any]:
    """
    Verifica y decodifica un token JWT de Auth0.
    
    Args:
        token: Token JWT a verificar
        
    Returns:
        Payload del token decodificado
        
    Raises:
        HTTPException: Si el token es inválido
    """
    try:
        # Obtener la clave RSA
        rsa_key = get_rsa_key(token)
        
        if not rsa_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No se pudo encontrar la clave para validar el token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        
        # Verificar y decodificar el token
        payload = pyjwt.decode(
            token,
            rsa_key,
            algorithms=AUTH0_ALGORITHMS,
            audience=AUTH0_AUDIENCE,
            issuer=f"https://{AUTH0_DOMAIN}/"
        )
        
        return payload
        
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except (InvalidAudienceError, InvalidIssuerError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Claims del token incorrectos. Verifica audience e issuer.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No se pudo validar el token",
            headers={"WWW-Authenticate": "Bearer"},
        )

def verify_auth0_token(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Dict[str, Any]:
    """
    Valida token JWT de Auth0 sin requerir que el usuario exista en BD.
    Útil para endpoints de registro/onboarding donde el usuario aún no existe.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No se proporcionaron credenciales de autenticación",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return verify_token(credentials.credentials)

async def load_user_permissions(user_id: str, *, schema_name: str) -> list:
    """
    Carga los permisos de sectores del usuario desde la base de datos.

    Args:
        user_id: UUID del usuario
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Lista de objetos SectorPermission con información de sectores y permisos
    """
    try:
        permissions_data = await user_service.get_user_sector_permissions(user_id, schema_name=schema_name)

        # Convertir cada dict a SectorPermission
        permissions = [
            SectorPermission(
                sector_id=str(p["sector_id"]),
                sector_acronym=p["sector_acronym"],
                department_id=str(p["department_id"]),
                department_name=p["department_name"],
                department_acronym=p["department_acronym"],
                can_view=p["can_view"],
                can_edit=p["can_edit"],
                is_primary=p["is_primary"]
            )
            for p in permissions_data
        ]

        return permissions

    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error cargando permisos para user_id {user_id}: {str(e)}", exc_info=True)
        return []

async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    x_user_id: Optional[str] = Header(None, alias="X-User-ID")
) -> AuthenticatedUser:
    """
    Dependency para obtener el usuario autenticado actual.

    MODO TESTING: Si TESTING_MODE=true, acepta:
    1. Header X-User-ID con el UUID del usuario
    2. Bearer token con UUID en lugar de JWT

    Args:
        request: Request de FastAPI (para obtener schema del TenantMiddleware)
        credentials: Credenciales de autorización HTTP Bearer
        x_user_id: UUID del usuario (solo para testing)

    Returns:
        Usuario autenticado

    Raises:
        HTTPException: Si el token es inválido o el usuario no existe
    """
    # Obtener schema del request (seteado por TenantMiddleware)
    schema_name = getattr(request.state, 'schema_name', None)

    # CAMINO API KEY (FASE 1 S8-001).
    # El TenantMiddleware ya validó la API Key y dejó schema_name + tenant_user_id
    # en request.state. Construimos el AuthenticatedUser igual que el camino JWT,
    # cargando permisos de sector desde BD (esto puebla sender_sector_id en NOTAs).
    # Nota: get_user_by_id filtra WHERE u.id=$1 AND u.estado=1, por lo que si
    # devuelve un resultado el usuario es activo; None significa no encontrado o inactivo.
    auth_source = getattr(request.state, 'auth_source', None)
    if auth_source == "api_key":
        tenant_user_id = getattr(request.state, 'tenant_user_id', None)
        if tenant_user_id and schema_name:
            user_data = await user_service.get_user_by_id(tenant_user_id, schema_name=schema_name)
            if not user_data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Usuario no encontrado en la base de datos.",
                )
            permissions = await load_user_permissions(str(user_data["user_id"]), schema_name=schema_name)
            return AuthenticatedUser(
                user_id=str(user_data["user_id"]),
                auth_id=user_data.get("auth_id") or "api_key",
                full_name=user_data["full_name"],
                email=user_data["email"],
                permissions=permissions
            )
        # API Key sin tenant_user_id no debería llegar acá (validate_rest_api_key lo exige),
        # pero si ocurre por algún bug de wiring → 401 explícito.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key auth no provee usuario para este endpoint",
        )

    # MODO TESTING: Usar tenant_user_id del middleware si está disponible
    if TESTING_MODE:
        # Opción 1: Usar tenant_user_id ya validado por TenantMiddleware
        tenant_user_id = getattr(request.state, 'tenant_user_id', None)
        if tenant_user_id:
            user_data = await user_service.get_user_by_id(tenant_user_id, schema_name=schema_name)

            if user_data:
                permissions = await load_user_permissions(str(user_data["user_id"]), schema_name=schema_name)
                return AuthenticatedUser(
                    user_id=str(user_data["user_id"]),
                    auth_id=user_data.get("auth_id") or "testing",
                    full_name=user_data["full_name"],
                    email=user_data["email"],
                    permissions=permissions
                )

        # Opción 2: Header X-User-ID
        test_user_id = x_user_id

        # Opción 3: Bearer token como UUID
        if not test_user_id and credentials:
            token = credentials.credentials
            if len(token) == 36 and token.count('-') == 4:
                test_user_id = token

        if test_user_id:
            user_data = await user_service.get_user_by_id(test_user_id, schema_name=schema_name)

            if not user_data:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Usuario {test_user_id} no encontrado en testing mode",
                )

            if user_data.get("estado") != 1:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Usuario inactivo",
                )

            permissions = await load_user_permissions(str(user_data["user_id"]), schema_name=schema_name)
            return AuthenticatedUser(
                user_id=str(user_data["user_id"]),
                auth_id=user_data.get("auth_id") or "testing",
                full_name=user_data["full_name"],
                email=user_data["email"],
                permissions=permissions
            )

    # MODO PRODUCCIÓN: Validar Auth0 normalmente
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No se proporcionaron credenciales de autenticación",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Verificar el token
    payload = verify_token(credentials.credentials)
    
    # Extraer información del token
    auth_id = payload.get("sub")
    email = payload.get("email") or payload.get("https://gdilatam.com/email")
    permissions = payload.get("permissions", [])
    
    if not auth_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token no contiene información de usuario válida",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Buscar el usuario en la base de datos (usando schema del tenant)
    if not schema_name:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Schema no configurado. TenantMiddleware no se ejecutó?",
        )

    user_data = await user_service.get_user_by_auth_id(auth_id, schema_name=schema_name)

    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado en la base de datos. Es posible que necesite registrarse primero.",
        )
    
    # Verificar que el usuario esté activo
    if user_data.get("estado") != 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuario inactivo",
        )

    # Cargar permisos de sectores desde BD
    user_permissions = await load_user_permissions(str(user_data["user_id"]), schema_name=schema_name)

    # Retornar usuario autenticado
    return AuthenticatedUser(
        user_id=str(user_data["user_id"]),
        auth_id=user_data["auth_id"],
        full_name=user_data["full_name"],
        email=user_data["email"],
        permissions=user_permissions
    )

async def get_optional_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    x_user_id: Optional[str] = Header(None, alias="X-User-ID")
) -> Optional[AuthenticatedUser]:
    """
    Dependency para obtener el usuario autenticado actual de forma opcional.
    No falla si no hay token, retorna None.
    """
    if not credentials and not x_user_id:
        return None

    try:
        return await get_current_user(request, credentials, x_user_id)
    except HTTPException:
        return None
    except Exception as e:
        logger.warning(f"Unexpected error in get_optional_current_user: {e}")
        return None

def decode_jwt_from_request(request) -> Dict[str, Any]:
    """
    Extrae y valida el JWT del header Authorization de un request.
    Usado por el tenant_middleware para obtener email sin consultar BD.

    Args:
        request: Request de FastAPI

    Returns:
        Payload del JWT decodificado (contiene email, sub, etc.)

    Raises:
        HTTPException: Si no hay token o es inválido
    """
    from fastapi import Request

    # Extraer header Authorization
    auth_header = request.headers.get("Authorization")

    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No se proporcionó header de autorización",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Verificar formato "Bearer <token>"
    parts = auth_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Formato de autorización inválido. Use: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = parts[1]

    # Verificar y decodificar token usando la función existente
    payload = verify_token(token)

    return payload

def require_permissions(required_permissions: list):
    """
    Decorator/dependency para requerir permisos específicos.
    
    Args:
        required_permissions: Lista de permisos requeridos
        
    Returns:
        Función dependency que verifica permisos
    """
    def permission_checker(current_user: AuthenticatedUser = Depends(get_current_user)):
        user_permissions = set(current_user.permissions)
        required_permissions_set = set(required_permissions)
        
        if not required_permissions_set.issubset(user_permissions):
            missing_permissions = required_permissions_set - user_permissions
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permisos insuficientes. Se requieren: {', '.join(missing_permissions)}",
            )
        
        return current_user
    
    return permission_checker