
from typing import Optional
import asyncpg
from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response, JSONResponse
from fastapi.concurrency import run_in_threadpool

from auth import decode_jwt_from_request
from shared.tenant_validation import validate_tenant_access, is_valid_schema
from shared.context import set_correlation_id, get_correlation_id, clear_correlation_id, set_request_endpoint
from shared.logging import get_logger
from database import fetch_one, execute, TESTING_MODE, testing_secret_matches

logger = get_logger(__name__)

TESTING_SECRET_HEADER = "X-Testing-Secret"


def _testing_secret_ok(request: Request) -> bool:
    return testing_secret_matches(request.headers.get(TESTING_SECRET_HEADER))


async def find_user_by_any_identifier(
    schema_name: str,
    user_id: str = None,
    email: str = None,
    auth_id: str = None
) -> Optional[dict]:
    if not any([user_id, email, auth_id]):
        return None

    select_cols = "SELECT id, email, sector_id, estado, auth_id FROM users"

    if auth_id:
        result = await fetch_one(
            f"{select_cols} WHERE auth_id = $1 ORDER BY id LIMIT 1",
            auth_id,
            schema_name=schema_name,
        )
        if result:
            return result

    if user_id:
        result = await fetch_one(
            f"{select_cols} WHERE id = $1 LIMIT 1",
            user_id,
            schema_name=schema_name,
        )
        if result:
            return result

    if email:
        result = await fetch_one(
            f"{select_cols} WHERE email = $1 ORDER BY id LIMIT 1",
            email.lower(),
            schema_name=schema_name,
        )
        if result:
            return result

    return None


async def _try_autocomplete_auth_id(request, user, schema_name, correlation_id):
    if user["auth_id"]:
        return
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return
    token = auth_header[7:]
    if len(token) == 36 and token.count('-') == 4:
        return
    try:
        jwt_payload = await run_in_threadpool(decode_jwt_from_request, request)
        real_auth_id = jwt_payload.get("sub")
        if real_auth_id:
            await execute(
                "UPDATE users SET auth_id = $1 WHERE id = $2",
                real_auth_id, str(user["id"]),
                schema_name=schema_name,
            )
            logger.info(f"[{correlation_id}] auth_id auto-completado: user_id={user['id']}")
    except asyncpg.exceptions.UniqueViolationError:
        logger.warning(
            "AUTH_ID_RECONCILE_NEEDED: fila user_id=%s email=%s desvinculada por "
            "migracion 078 (auth_id ya vinculado a otra fila), requiere reconciliacion manual",
            user["id"], user.get("email"),
        )
    except Exception as e:
        logger.warning(f"[{correlation_id}] Error auto-completando auth_id: {e}")


class TenantMiddleware(BaseHTTPMiddleware):

    EXCLUDED_PATHS = {
        "/health",
        "/livez",
        "/api/v1/system/health",
        "/favicon.ico",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/api/auth/onboarding",
        "/digital-signature/storage",
        "/_debug/boom",
    }

    async def dispatch(self, request: Request, call_next) -> Response:
        incoming_correlation_id = request.headers.get("X-Correlation-ID")
        if incoming_correlation_id:
            set_correlation_id(incoming_correlation_id)
            correlation_id = incoming_correlation_id
        else:
            correlation_id = get_correlation_id()
        request.state.correlation_id = correlation_id

        set_request_endpoint(request.method, request.url.path)

        if request.method == "OPTIONS":
            logger.debug(f"[{correlation_id}] OPTIONS request - skipping tenant validation")
            return await call_next(request)

        path = request.url.path
        if path in self.EXCLUDED_PATHS or path.startswith("/docs") or path.startswith("/openapi"):
            logger.debug(f"[{correlation_id}] Public path '{path}' - skipping tenant validation")
            return await call_next(request)

        if TESTING_MODE and _testing_secret_ok(request):
            schema_name = request.headers.get("X-Tenant-Schema") or "100_test"

            if schema_name.lower() == "public":
                logger.warning(
                    f"[{correlation_id}] X-Tenant-Schema='public' rechazado (no es un tenant)"
                )
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Tenant no válido"},
                )

            if not await is_valid_schema(schema_name):
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Tenant no válido"}
                )

            request.state.schema_name = schema_name

            logger.debug(f"MIDDLEWARE path='{path}', method='{request.method}'")

            user_id = request.headers.get("X-User-ID")

            if not user_id:
                auth_header = request.headers.get("Authorization", "")
                if auth_header.startswith("Bearer "):
                    token = auth_header[7:]
                    if len(token) == 36 and token.count('-') == 4:
                        user_id = token

            email = request.headers.get("X-User-Email")
            auth_id = None

            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer ") and not user_id:
                try:
                    jwt_payload = await run_in_threadpool(decode_jwt_from_request, request)
                    if not email:
                        email = jwt_payload.get("email") or jwt_payload.get("https://gdilatam.com/email")
                    auth_id = jwt_payload.get("sub")
                except Exception as e:
                    logger.warning(f"[{correlation_id}] TESTING_MODE: Error decodificando JWT: {e}")

            user = await find_user_by_any_identifier(
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
                request.state.auth_source = "testing"

                await _try_autocomplete_auth_id(request, user, schema_name, correlation_id)

                identifier = user_id or email or auth_id
                logger.info(f"[{correlation_id}] TESTING_MODE: {identifier} → schema={schema_name}")
                logger.info(f"[{correlation_id}] TESTING_MODE OK: user_id={user['id']} en {schema_name}")
                return await call_next(request)

            if user_id or email or auth_id:
                identifier = user_id or email or auth_id
                return JSONResponse(
                    status_code=404,
                    content={"detail": f"Usuario {identifier} no encontrado en este tenant"}
                )

            return JSONResponse(
                status_code=400,
                content={"detail": "TESTING_MODE: Header X-User-ID, Bearer <uuid>, o JWT con email requerido"}
            )

        from middleware.auth_router import resolve_auth

        try:
            resolved = await resolve_auth(request)
        except ValueError as ve:
            logger.warning(f"[{correlation_id}] API Key auth rechazada: {ve}")
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Credenciales inválidas"},
            )
        except Exception as e:
            logger.error(f"[{correlation_id}] Error resolviendo API Key auth: {e}", exc_info=True)
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": "Error validando credenciales"},
            )

        if resolved is not None and not resolved.is_jwt:
            if (resolved.schema_name or "").lower() == "public":
                logger.error(
                    f"[{correlation_id}] API Key devolvió schema='public' (no es un tenant)"
                )
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"detail": "Tenant no válido"},
                )

            if not await is_valid_schema(resolved.schema_name):
                logger.error(
                    f"[{correlation_id}] API Key devolvió schema inválido: '{resolved.schema_name}'"
                )
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"detail": "Tenant no válido"},
                )

            request.state.schema_name = resolved.schema_name
            request.state.auth_source = resolved.auth_source
            if resolved.tenant_user_id:
                request.state.tenant_user_id = resolved.tenant_user_id
            if resolved.tenant_email:
                request.state.tenant_email = resolved.tenant_email

            logger.info(
                f"[{correlation_id}] Auth OK via {resolved.auth_source} - "
                f"schema={resolved.schema_name}, user_id={resolved.tenant_user_id}"
            )
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = correlation_id
            return response


        try:
            jwt_payload = await run_in_threadpool(decode_jwt_from_request, request)
            auth_id = jwt_payload.get("sub")

            if not auth_id:
                logger.error(f"[{correlation_id}] JWT no contiene claim 'sub' (auth_id)")
                return JSONResponse(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    content={"detail": "Token JWT no contiene identificador de usuario (sub)"},
                    headers={"WWW-Authenticate": "Bearer"},
                )

            email = (
                jwt_payload.get("email")
                or jwt_payload.get("https://gdilatam.com/email")
            )
            if auth_id:
                logger.info(f"[{correlation_id}] Request de auth_id={auth_id} a {path}")
            else:
                logger.info(f"[{correlation_id}] Request sin auth_id a {path}")

        except HTTPException as he:
            return JSONResponse(
                status_code=he.status_code,
                content={"detail": he.detail},
                headers=he.headers if hasattr(he, 'headers') and he.headers else None,
            )
        except Exception as e:
            logger.error(f"[{correlation_id}] Error extrayendo JWT: {e}", exc_info=True)
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Token inválido o expirado"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        schema_name = request.headers.get("X-Tenant-Schema")

        if not schema_name:
            identifier = email or auth_id
            logger.warning(f"[{correlation_id}] Header X-Tenant-Schema faltante para {identifier}")
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "Header 'X-Tenant-Schema' es requerido para acceder a este recurso"},
            )

        if schema_name.lower() == "public":
            logger.warning(
                f"[{correlation_id}] X-Tenant-Schema='public' rechazado (no es un tenant)"
            )
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "Tenant no válido"},
            )

        try:
            user = await find_user_by_any_identifier(
                schema_name=schema_name,
                auth_id=auth_id,
                email=email
            )

            if user and not email:
                email = user["email"]
                logger.info(f"[{correlation_id}] Email obtenido de BD para user_id={user['id']}")

        except Exception as e:
            logger.error(f"[{correlation_id}] Error buscando usuario en schema: {e}", exc_info=True)
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": "Error validando usuario en municipalidad"},
            )

        if email:
            has_access = await validate_tenant_access(email, schema_name)
            if not has_access:
                logger.warning(f"[{correlation_id}] auth_id={auth_id} sin acceso a schema '{schema_name}'")
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "No tiene permisos para acceder a esta municipalidad"},
                )
        elif not user:
            logger.warning(f"[{correlation_id}] auth_id={auth_id} sin email y sin usuario en schema '{schema_name}'")
            return JSONResponse(
                status_code=status.HTTP_404_NOT_FOUND,
                content={"detail": "Usuario no encontrado en esta municipalidad"},
            )

        if not await is_valid_schema(schema_name):
            logger.error(f"[{correlation_id}] Schema inválido detectado: '{schema_name}'")
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "Tenant no válido"},
            )

        request.state.schema_name = schema_name
        request.state.tenant_email = email
        logger.debug(f"[{correlation_id}] Schema '{schema_name}' guardado en request.state")

        try:
            if not user:
                identifier = email or auth_id
                logger.warning(f"[{correlation_id}] Usuario {identifier} no existe en schema '{schema_name}'")
                return JSONResponse(
                    status_code=status.HTTP_404_NOT_FOUND,
                    content={"detail": "Usuario no encontrado en esta municipalidad"},
                )

            user_estado_efectivo = user["estado"]
            if user["estado"] == 0:
                auth_id_actual = user["auth_id"] if user["auth_id"] is not None else ""
                if str(auth_id_actual).startswith("PENDING_"):
                    real_auth_id = jwt_payload.get("sub")
                    full_name = jwt_payload.get("name", "")
                    picture = jwt_payload.get("picture")

                    if real_auth_id:
                        activate_query = """
                            UPDATE users
                            SET estado = 1,
                                auth_id = $1,
                                full_name = COALESCE(NULLIF($2, ''), full_name),
                                profile_picture_url = COALESCE($3, profile_picture_url),
                                last_access = NOW()
                            WHERE id = $4
                        """
                        await execute(
                            activate_query,
                            real_auth_id, full_name, picture, str(user["id"]),
                            schema_name=schema_name,
                        )
                        logger.info(f"[{correlation_id}] Usuario user_id={user['id']} activado automaticamente en {schema_name}")
                        user_estado_efectivo = 1
                else:
                    logger.warning(f"[{correlation_id}] Usuario user_id={user['id']} inactivo en schema '{schema_name}'")
                    return JSONResponse(
                        status_code=status.HTTP_403_FORBIDDEN,
                        content={"detail": "Usuario inactivo en esta municipalidad"},
                    )
            else:
                user_estado_efectivo = user["estado"]

            if user_estado_efectivo != 1:
                logger.warning(f"[{correlation_id}] Usuario user_id={user['id']} con estado invalido en schema '{schema_name}'")
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"detail": "Usuario inactivo en esta municipalidad"},
                )

            request.state.tenant_user_id = str(user["id"])
            request.state.auth_source = "jwt"

            await _try_autocomplete_auth_id(request, user, schema_name, correlation_id)

            logger.info(
                f"[{correlation_id}] Tenant validation OK - user_id={user['id']}, "
                f"schema={schema_name}, estado={user_estado_efectivo}"
            )

        except Exception as e:
            logger.error(f"[{correlation_id}] Error validando usuario en schema: {e}", exc_info=True)
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"detail": "Error validando usuario en municipalidad"},
            )

        response = await call_next(request)

        response.headers["X-Correlation-ID"] = correlation_id

        return response
