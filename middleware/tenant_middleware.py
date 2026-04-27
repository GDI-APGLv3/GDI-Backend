"""
Middleware multi-tenant para validación de acceso por schema.
Valida que cada usuario tenga acceso al schema especificado en X-Tenant-Schema.
"""

from typing import Optional
from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response, JSONResponse
from auth import decode_jwt_from_request
from shared.tenant_validation import validate_tenant_access, is_valid_schema
from shared.context import set_correlation_id, get_correlation_id, clear_correlation_id
from shared.logging import get_logger
from database import execute_query, execute_update, get_db_cursor, TESTING_MODE

logger = get_logger(__name__)


def find_user_by_any_identifier(
    schema_name: str,
    user_id: str = None,
    email: str = None,
    auth_id: str = None
) -> Optional[dict]:
    """
    Busca usuario por cualquier identificador en UNA sola query.
    Optimización: evita N queries separadas por id, email, auth_id.

    Args:
        schema_name: Schema del tenant
        user_id: UUID del usuario (opcional)
        email: Email del usuario (opcional)
        auth_id: Auth0 ID del usuario (opcional)

    Returns:
        dict con datos del usuario o None si no se encuentra
    """
    if not any([user_id, email, auth_id]):
        return None

    # Construir condiciones dinámicamente para evitar OR innecesarios
    conditions = []
    params = []

    if user_id:
        conditions.append("id = %s")
        params.append(user_id)
    if email:
        conditions.append("email = %s")
        params.append(email.lower())
    if auth_id:
        conditions.append("auth_id = %s")
        params.append(auth_id)

    where_clause = " OR ".join(conditions)

    query = f"""
        SELECT id, email, sector_id, estado, auth_id
        FROM users
        WHERE ({where_clause})
        LIMIT 1
    """

    return execute_query(query, tuple(params), fetch_one=True, schema_name=schema_name)


def _try_autocomplete_auth_id(request, user, schema_name, correlation_id):
    """Si el user no tiene auth_id y hay JWT con sub, lo escribe."""
    if user.get("auth_id"):
        return
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return
    token = auth_header[7:]
    if len(token) == 36 and token.count('-') == 4:
        return  # Es UUID, no JWT
    try:
        jwt_payload = decode_jwt_from_request(request)
        real_auth_id = jwt_payload.get("sub")
        if real_auth_id:
            execute_update(
                "UPDATE users SET auth_id = %s WHERE id = %s",
                (real_auth_id, user["id"]),
                schema_name=schema_name,
            )
            logger.info(f"[{correlation_id}] auth_id auto-completado: {user['email']}")
    except Exception as e:
        logger.warning(f"[{correlation_id}] Error auto-completando auth_id: {e}")


class TenantMiddleware(BaseHTTPMiddleware):
    """
    Middleware que valida acceso multi-tenant en cada request.

    Flujo:
    1. Excluir OPTIONS (CORS preflight) y rutas públicas
    2. Extraer email del JWT (NO del body)
    3. Leer header X-Tenant-Schema
    4. Validar acceso del usuario al schema (con cache)
    5. Validar schema contra whitelist (SQL injection prevention)
    6. Ejecutar SET search_path TO {schema}, public
    7. Query users WHERE email=$1, validar estado=1
    8. Guardar request.state.tenant_user_id y request.state.schema_name
    """

    # Rutas que NO requieren validación de tenant
    EXCLUDED_PATHS = {
        "/health",
        "/livez",
        "/api/v1/system/health",
        "/favicon.ico",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/api/auth/onboarding",  # Onboarding debe pasar sin tenant
    }

    async def dispatch(self, request: Request, call_next) -> Response:
        """
        Procesa cada request validando acceso multi-tenant.

        Args:
            request: Request de FastAPI
            call_next: Siguiente middleware o endpoint

        Returns:
            Response del endpoint o error HTTP
        """
        # Generar correlation ID para trazabilidad usando contextvars
        # Primero verificar si viene uno en el header (para propagación entre servicios)
        incoming_correlation_id = request.headers.get("X-Correlation-ID")
        if incoming_correlation_id:
            set_correlation_id(incoming_correlation_id)
            correlation_id = incoming_correlation_id
        else:
            # get_correlation_id() genera uno nuevo si no existe
            correlation_id = get_correlation_id()
        request.state.correlation_id = correlation_id

        # 1. EXCLUIR OPTIONS (CORS preflight)
        if request.method == "OPTIONS":
            logger.debug(f"[{correlation_id}] OPTIONS request - skipping tenant validation")
            return await call_next(request)

        # 2. EXCLUIR rutas públicas
        path = request.url.path
        if path in self.EXCLUDED_PATHS or path.startswith("/docs") or path.startswith("/openapi"):
            logger.debug(f"[{correlation_id}] Public path '{path}' - skipping tenant validation")
            return await call_next(request)

        # 2.5 MODO TESTING: leer X-Tenant-Schema si viene, fallback a 100_test
        if TESTING_MODE:
            schema_name = request.headers.get("X-Tenant-Schema") or "100_test"

            # Validar que el schema sea válido (prevenir SQL injection)
            if not is_valid_schema(schema_name):
                return JSONResponse(
                    status_code=400,
                    content={"detail": f"Schema '{schema_name}' no es válido"}
                )

            request.state.schema_name = schema_name

            # GET /users es público en TESTING_MODE (para página de login)
            logger.debug(f"MIDDLEWARE path='{path}', method='{request.method}'")
            if request.method == "GET" and (path == "/users" or path == "/users/"):
                logger.info(f"[{correlation_id}] TESTING_MODE: GET /users público (login page)")
                return await call_next(request)

            # Opción 1: Header X-User-ID con UUID
            user_id = request.headers.get("X-User-ID")

            # Opción 2: Bearer token como UUID
            if not user_id:
                auth_header = request.headers.get("Authorization", "")
                if auth_header.startswith("Bearer "):
                    token = auth_header[7:]
                    # Verificar si parece UUID (36 chars, 4 guiones)
                    if len(token) == 36 and token.count('-') == 4:
                        user_id = token

            # Extraer todos los identificadores posibles
            email = request.headers.get("X-User-Email")
            auth_id = None

            # Intentar decodificar JWT si hay Bearer token (no UUID)
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer ") and not user_id:
                try:
                    jwt_payload = decode_jwt_from_request(request)
                    if not email:
                        email = jwt_payload.get("email")
                    auth_id = jwt_payload.get("sub")
                except Exception as e:
                    logger.warning(f"[{correlation_id}] TESTING_MODE: Error decodificando JWT: {e}")

            # UNA SOLA QUERY para buscar usuario por cualquier identificador
            user = find_user_by_any_identifier(
                schema_name=schema_name,
                user_id=user_id,
                email=email,
                auth_id=auth_id
            )

            if user:
                if user["estado"] != 1:
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "Usuario inactivo en modo testing"}
                    )

                request.state.tenant_user_id = str(user["id"])
                request.state.tenant_email = user["email"]
                request.state.auth_source = "testing"  # Trazabilidad: origen testing

                _try_autocomplete_auth_id(request, user, schema_name, correlation_id)

                identifier = user_id or email or auth_id
                logger.info(f"[{correlation_id}] TESTING_MODE: {identifier} → schema={schema_name}")
                logger.info(f"[{correlation_id}] TESTING_MODE OK: {user['email']} en {schema_name}")
                return await call_next(request)

            # Si teníamos algún identificador pero no encontramos usuario
            if user_id or email or auth_id:
                identifier = user_id or email or auth_id
                return JSONResponse(
                    status_code=404,
                    content={"detail": f"Usuario {identifier} no encontrado en schema {schema_name}"}
                )

            # Ninguna opción funcionó
            return JSONResponse(
                status_code=400,
                content={"detail": "TESTING_MODE: Header X-User-ID, Bearer <uuid>, o JWT con email requerido"}
            )

        # 3. EXTRAER auth_id (sub) Y EMAIL DEL JWT
        # auth_id es el identificador primario; email es redundancia/fallback
        try:
            jwt_payload = decode_jwt_from_request(request)
            auth_id = jwt_payload.get("sub")

            # auth_id (sub) SIEMPRE debe existir en un JWT válido
            if not auth_id:
                logger.error(f"[{correlation_id}] JWT no contiene claim 'sub' (auth_id)")
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "Token JWT no contiene identificador de usuario (sub)"},
                    headers={"WWW-Authenticate": "Bearer"},
                )

            # Email con fallback chain: JWT claim > namespace claim
            email = (
                jwt_payload.get("email")
                or jwt_payload.get("https://gdilatam.com/email")
            )
            if email:
                logger.info(f"[{correlation_id}] Request de {email} (auth_id={auth_id}) a {path}")
            else:
                logger.info(f"[{correlation_id}] Request de auth_id={auth_id} (sin email) a {path}")

        except HTTPException as he:
            # Convertir HTTPException a JSONResponse (BaseHTTPMiddleware no maneja bien HTTPException)
            return JSONResponse(
                status_code=he.status_code,
                content={"detail": he.detail},
                headers=he.headers if hasattr(he, 'headers') and he.headers else None,
            )
        except Exception as e:
            logger.error(f"[{correlation_id}] Error extrayendo JWT: {e}", exc_info=True)
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": f"Error validando token: {str(e)}"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 4. LEER HEADER X-Tenant-Schema
        schema_name = request.headers.get("X-Tenant-Schema")

        if not schema_name:
            identifier = email or auth_id
            logger.warning(f"[{correlation_id}] Header X-Tenant-Schema faltante para {identifier}")
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "Header 'X-Tenant-Schema' es requerido para acceder a este recurso"},
            )

        # 5. BUSCAR USUARIO POR auth_id (primario) con email como fallback
        # Usa find_user_by_any_identifier para una sola query eficiente
        try:
            user = find_user_by_any_identifier(
                schema_name=schema_name,
                auth_id=auth_id,
                email=email
            )

            # Si encontramos usuario, obtener email de BD si no lo teníamos del JWT
            if user and not email:
                email = user["email"]
                logger.info(f"[{correlation_id}] Email obtenido de BD: {email}")

        except Exception as e:
            logger.error(f"[{correlation_id}] Error buscando usuario en schema: {e}", exc_info=True)
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": f"Error validando usuario en municipalidad: {str(e)}"},
            )

        # 6. VALIDAR ACCESO DEL USUARIO AL SCHEMA (con cache)
        # Necesitamos email para validate_tenant_access
        if email:
            has_access = validate_tenant_access(email, schema_name)
            if not has_access:
                logger.warning(f"[{correlation_id}] Usuario {email} sin acceso a schema '{schema_name}'")
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": f"No tiene permisos para acceder a la municipalidad '{schema_name}'"},
                )
        elif not user:
            # Sin email y sin usuario encontrado por auth_id: no podemos validar acceso
            logger.warning(f"[{correlation_id}] auth_id={auth_id} sin email y sin usuario en schema '{schema_name}'")
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"detail": f"Usuario no encontrado en municipalidad '{schema_name}'"},
            )

        # 7. VALIDAR SCHEMA CONTRA WHITELIST (SQL injection prevention)
        if not is_valid_schema(schema_name):
            logger.error(f"[{correlation_id}] Schema inválido detectado: '{schema_name}'")
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": f"Schema '{schema_name}' no es válido"},
            )

        # 8. GUARDAR schema_name en request.state
        # Los endpoints/services deben pasar schema_name explícitamente a las funciones de BD
        request.state.schema_name = schema_name
        request.state.tenant_email = email
        logger.debug(f"[{correlation_id}] Schema '{schema_name}' guardado en request.state")

        # 9. VALIDAR USUARIO ENCONTRADO
        try:
            if not user:
                identifier = email or auth_id
                logger.warning(f"[{correlation_id}] Usuario {identifier} no existe en schema '{schema_name}'")
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content={"detail": f"Usuario no encontrado en municipalidad '{schema_name}'"},
                )

            # ACTIVACION AUTOMATICA de usuarios pendientes (estado=0, auth_id=PENDING_*)
            if user["estado"] == 0:
                auth_id_actual = user.get("auth_id", "") or ""
                if str(auth_id_actual).startswith("PENDING_"):
                    # Obtener auth_id real del JWT
                    real_auth_id = jwt_payload.get("sub")
                    full_name = jwt_payload.get("name", "")
                    picture = jwt_payload.get("picture")

                    if real_auth_id:
                        activate_query = """
                            UPDATE users
                            SET estado = 1,
                                auth_id = %s,
                                full_name = COALESCE(NULLIF(%s, ''), full_name),
                                profile_picture_url = COALESCE(%s, profile_picture_url),
                                last_access = NOW()
                            WHERE id = %s
                        """
                        execute_query(activate_query, (real_auth_id, full_name, picture, user["id"]), schema_name=schema_name)
                        logger.info(f"[{correlation_id}] Usuario {email} activado automaticamente en {schema_name}")
                        # Actualizar estado local para continuar
                        user["estado"] = 1
                else:
                    # Usuario desactivado manualmente (no es pendiente)
                    logger.warning(f"[{correlation_id}] Usuario {email} inactivo en schema '{schema_name}'")
                    return JSONResponse(
                        status_code=status.HTTP_403_FORBIDDEN,
                        content={"detail": "Usuario inactivo en esta municipalidad"},
                    )

            # VALIDAR estado=1 final
            if user["estado"] != 1:
                logger.warning(f"[{correlation_id}] Usuario {email} con estado invalido en schema '{schema_name}'")
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "Usuario inactivo en esta municipalidad"},
                )

            # 10. GUARDAR EN request.state para uso en endpoints
            request.state.tenant_user_id = str(user["id"])
            request.state.auth_source = "jwt"  # Trazabilidad: origen Frontend Auth0

            # 11. AUTO-COMPLETAR auth_id si esta vacio (usuarios migrados o creados sin auth)
            _try_autocomplete_auth_id(request, user, schema_name, correlation_id)

            logger.info(
                f"[{correlation_id}] Tenant validation OK - user_id={user['id']}, "
                f"schema={schema_name}, estado={user['estado']}"
            )

        except Exception as e:
            logger.error(f"[{correlation_id}] Error validando usuario en schema: {e}", exc_info=True)
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": f"Error validando usuario en municipalidad: {str(e)}"},
            )

        # Continuar con el request
        response = await call_next(request)

        # Agregar correlation ID al response para debugging
        response.headers["X-Correlation-ID"] = correlation_id

        return response
