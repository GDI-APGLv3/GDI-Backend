
from shared.logging import get_logger
import httpx

logger = get_logger(__name__)
from fastapi import HTTPException, Depends, status, Header, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt as pyjwt
from jwt.algorithms import RSAAlgorithm
from jwt.exceptions import PyJWTError, ExpiredSignatureError, InvalidAudienceError, InvalidIssuerError
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from database import AUTH0_DOMAIN, AUTH0_AUDIENCE, AUTH0_ALGORITHMS, AUTH0_ISSUERS, TESTING_MODE, testing_secret_matches
from services import user_service
from models.schemas import AuthenticatedUser, SectorPermission
from shared.exceptions import TransientLookupError

security = HTTPBearer(auto_error=False)

JWKS_HTTP_TIMEOUT_SECONDS = 5.0

_jwks_cache_by_issuer: Dict[str, Any] = {}

def get_jwks_for_issuer(issuer: str) -> Dict[str, Any]:
    entry = _jwks_cache_by_issuer.get(issuer)
    if entry and datetime.now() < entry["expiry"]:
        return entry["jwks"]

    try:
        jwks_url = f"{issuer}.well-known/jwks.json"
        response = httpx.get(jwks_url, timeout=JWKS_HTTP_TIMEOUT_SECONDS)
        response.raise_for_status()

        jwks = response.json()
        _jwks_cache_by_issuer[issuer] = {
            "jwks": jwks,
            "expiry": datetime.now() + timedelta(minutes=30),
        }
        return jwks

    except httpx.HTTPError as e:
        if entry:
            logger.warning(
                f"JWKS de {issuer} no se pudo refrescar ({type(e).__name__}); "
                "se sirve la copia cacheada vencida"
            )
            return entry["jwks"]
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"No se pudo obtener las claves de Auth0: {str(e)}"
        )


def _extract_unverified_iss(token: str) -> Optional[str]:
    try:
        unverified = pyjwt.decode(
            token,
            options={"verify_signature": False},
            algorithms=AUTH0_ALGORITHMS,
        )
        iss = unverified.get("iss")
        if isinstance(iss, str) and iss:
            return iss.rstrip("/") + "/"
        return None
    except PyJWTError:
        return None


def get_rsa_key(token: str) -> Optional[Dict[str, Any]]:
    try:
        unverified_header = pyjwt.get_unverified_header(token)

        iss = _extract_unverified_iss(token) or f"https://{AUTH0_DOMAIN}/"

        jwks = get_jwks_for_issuer(iss)

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
    try:
        iss = _extract_unverified_iss(token)
        if not iss or iss not in AUTH0_ISSUERS:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Claims del token incorrectos. Verifica audience e issuer.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        rsa_key = get_rsa_key(token)

        if not rsa_key:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No se pudo encontrar la clave para validar el token",
                headers={"WWW-Authenticate": "Bearer"},
            )

        payload = pyjwt.decode(
            token,
            rsa_key,
            algorithms=AUTH0_ALGORITHMS,
            audience=AUTH0_AUDIENCE,
            issuer=iss,
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

async def load_user_permissions(user_id: str, *, schema_name: str) -> list:
    try:
        permissions_data = await user_service.get_user_sector_permissions(user_id, schema_name=schema_name)

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

    except TransientLookupError:
        raise
    except Exception as e:
        logger.error(f"Error cargando permisos para user_id {user_id}: {str(e)}", exc_info=True)
        return []

async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    x_user_id: Optional[str] = Header(None, alias="X-User-ID")
) -> AuthenticatedUser:
    schema_name = getattr(request.state, 'schema_name', None)

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
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key auth no provee usuario para este endpoint",
        )

    if TESTING_MODE and testing_secret_matches(request.headers.get("X-Testing-Secret")):
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

        test_user_id = x_user_id

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

    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No se proporcionaron credenciales de autenticación",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = verify_token(credentials.credentials)
    
    auth_id = payload.get("sub")
    email = payload.get("email") or payload.get("https://gdilatam.com/email")
    permissions = payload.get("permissions", [])
    
    if not auth_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token no contiene información de usuario válida",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if not schema_name:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Schema no configurado. TenantMiddleware no se ejecutó?",
        )

    user_data = await user_service.get_user_by_auth_id(auth_id, schema_name=schema_name)

    if not user_data and email:
        user_data = await user_service.get_user_by_email(email.lower(), schema_name=schema_name)

    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Usuario no encontrado en la base de datos. Es posible que necesite registrarse primero.",
        )
    
    if user_data.get("estado") != 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuario inactivo",
        )

    user_permissions = await load_user_permissions(str(user_data["user_id"]), schema_name=schema_name)

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

    auth_header = request.headers.get("Authorization")

    if not auth_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No se proporcionó header de autorización",
            headers={"WWW-Authenticate": "Bearer"},
        )

    parts = auth_header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Formato de autorización inválido. Use: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = parts[1]

    payload = verify_token(token)

    return payload

