"""
Autenticación REST con API Key por Schema.

Valida API Key contra tabla public.api_keys,
obtiene municipality_id y schema_name automáticamente.

Incluye validación de Backup API Keys (key_type='backup')
con 3 capas: key válida, IP/DNS, rate limit.
"""
import hashlib
import hmac
import os
import re
import sys
import socket
import logging
from typing import Tuple, Optional
from datetime import datetime, timezone
from functools import lru_cache
import time

# Service auth: paths donde se acepta AGENT_GATEWAY_SECRET (shared secret).
# Sin esta allowlist, el secret seria una llave de acceso total.
SERVICE_AUTH_ALLOWED_PATHS = [
    re.compile(r'^/api/v1/documents/[^/]+/url$'),
]

# Service account UUID usado para auditoria cuando entra por service auth (AI Worker).
_SERVICE_AUTH_USER_ID = "a1000000-0000-0000-0000-000000000100"

# Agregar path del backend para imports
backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_root)

from api_gateway.context import MCPContext
from database import validate_schema_name

logger = logging.getLogger(__name__)


async def validate_rest_api_key(api_key: str, user_id: str = None, request=None) -> MCPContext:
    """
    Valida API Key y retorna contexto MCP con user_id.

    La API Key determina automáticamente la municipalidad y schema.
    El user_id es REQUERIDO y debe estar autorizado para esta API Key.

    Si se pasa `request` y el `api_key` coincide con AGENT_GATEWAY_SECRET
    (env var), se intenta una autenticacion de servicio (service-to-service)
    restringida a SERVICE_AUTH_ALLOWED_PATHS y que requiere header
    X-Tenant-Schema. Esta rama NO requiere X-User-ID.

    Args:
        api_key: Header X-API-Key
        user_id: Header X-User-ID (REQUERIDO en el path normal)
        request: Starlette Request opcional. Necesario para service auth.

    Returns:
        MCPContext con api_key, municipality_id, schema_name y user_id

    Raises:
        ValueError: Si API Key inválida, inactiva, expirada, o user_id no autorizado
    """
    from database import fetch_one, execute

    if not api_key:
        raise ValueError("X-API-Key header requerido")

    # ============================================================
    # SERVICE AUTH (shared secret) - se evalua ANTES del path normal.
    # Solo activo si AGENT_GATEWAY_SECRET esta seteada y el caller
    # paso `request` (asi los callers viejos no rompen).
    # ============================================================
    service_secret = os.getenv("AGENT_GATEWAY_SECRET")
    if request is not None and service_secret and hmac.compare_digest(api_key, service_secret):
        # a) Path allowlist - el secret SOLO funciona en endpoints especificos.
        path = request.url.path
        if not any(p.match(path) for p in SERVICE_AUTH_ALLOWED_PATHS):
            logger.warning(f"[Service Auth] Intento en path no permitido: {path}")
            raise ValueError("Service auth no permitido en este endpoint")

        # b) Validar header X-Tenant-Schema (requerido en service auth).
        schema_name = request.headers.get("X-Tenant-Schema")
        if not schema_name:
            raise ValueError("X-Tenant-Schema header requerido para service auth")

        # Defense in depth contra SQL injection del nombre de schema.
        validate_schema_name(schema_name)

        # c) Validar que el schema existe y esta activo en municipalities.
        try:
            row = await fetch_one(
                """
                SELECT id
                FROM public.municipalities
                WHERE schema_name = $1 AND is_active = true
                """,
                schema_name,
                schema_name="public"
            )
        except Exception as e:
            logger.error(f"[Service Auth] Error consultando municipalities: {e}")
            raise ValueError("Error validando schema")

        if not row:
            logger.warning(f"[Service Auth] Schema invalido o inactivo: {schema_name}")
            raise ValueError("Schema invalido o inactivo")

        # d) Audit log
        logger.info(f"[Service Auth] OK: path={path} schema={schema_name}")

        # e) Retornar MCPContext como si fuera una API Key valida.
        return MCPContext(
            api_key=api_key,
            municipality_id=str(row["id"]),
            schema_name=schema_name,
            auth_source="service",
            user_id=_SERVICE_AUTH_USER_ID,
        )

    # === Path normal: validacion contra public.api_keys (sin tocar) ===

    if not user_id:
        raise ValueError("X-User-ID header requerido para REST API")

    # Hashear la key entrante para comparar con BD
    api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    # Buscar API Key en tabla public.api_keys por hash
    try:
        result = await fetch_one(
            """
            SELECT
                ak.id,
                ak.api_key_prefix,
                ak.municipality_id,
                ak.name as key_name,
                ak.expires_at,
                ak.rate_limit_per_minute,
                ak.is_active as key_active,
                m.schema_name,
                m.name as municipality_name,
                m.is_active as muni_active
            FROM public.api_keys ak
            JOIN public.municipalities m ON ak.municipality_id = m.id
            WHERE ak.api_key_hash = $1
            """,
            api_key_hash,
            schema_name="public"
        )
    except Exception as e:
        logger.error(f"[REST Auth] Error consultando API Key: {e}")
        raise ValueError("Error validando API Key")

    if not result:
        logger.warning(f"[REST Auth] API Key no encontrada: {api_key[:12]}...")
        raise ValueError("API Key inválida")

    # Verificar que la key está activa
    if not result.get("key_active"):
        logger.warning(f"[REST Auth] API Key inactiva: {result['key_name']}")
        raise ValueError("API Key inactiva")

    # Verificar que la municipalidad está activa
    if not result.get("muni_active"):
        logger.warning(f"[REST Auth] Municipalidad inactiva: {result['municipality_name']}")
        raise ValueError("Municipalidad inactiva")

    # Verificar expiración
    expires_at = result.get("expires_at")
    if expires_at and expires_at < datetime.now(expires_at.tzinfo):
        logger.warning(f"[REST Auth] API Key expirada: {result['key_name']}")
        raise ValueError("API Key expirada")

    # Verificar que user_id está autorizado para esta API Key
    api_key_id = result["id"]
    schema_name = result["schema_name"]
    validate_schema_name(schema_name)  # Defense in depth

    try:
        user_allowed = await fetch_one(
            """
            SELECT user_id FROM public.api_key_users
            WHERE api_key_id = $1 AND user_id = $2 AND schema_name = $3
            """,
            api_key_id, user_id, schema_name,
            schema_name="public"
        )
    except Exception as e:
        logger.error(f"[REST Auth] Error verificando usuario autorizado: {e}")
        raise ValueError("Error validando usuario")

    if not user_allowed:
        logger.warning(f"[REST Auth] Usuario {user_id} no autorizado para API Key {result['key_name']}")
        raise ValueError(f"Usuario no autorizado para esta API Key")

    # Rate limit per-API-Key (usa rate_limit_per_minute de BD)
    rate_limit_per_minute = result.get("rate_limit_per_minute")
    if rate_limit_per_minute:
        from api_gateway.rate_limiter import rate_limiter, RateLimitExceeded
        rate_limiter.check(f"rest_key:{api_key_id}", rate_limit_per_minute)

    # Actualizar last_used_at (async, no bloquea si falla)
    await _update_last_used(api_key_id)

    ctx = MCPContext(
        api_key=api_key,
        municipality_id=str(result["municipality_id"]),
        schema_name=schema_name,
        auth_source="api_key",  # Trazabilidad: origen REST API
        user_id=user_id  # Usuario validado
    )

    logger.info(f"[REST Auth] API Key válida: {result['key_name']}, schema: {schema_name}, user: {user_id}")
    return ctx


async def _update_last_used(api_key_id: str) -> None:
    """
    Actualiza el timestamp de último uso de la API Key.
    No falla si hay error (operación no crítica).
    """
    from database import execute

    try:
        await execute(
            "UPDATE public.api_keys SET last_used_at = NOW() WHERE id = $1",
            api_key_id,
            schema_name="public"
        )
    except Exception as e:
        # No fallar por esto, solo loguear
        logger.debug(f"[REST Auth] Error actualizando last_used_at: {e}")


async def get_api_key_info(api_key: str) -> Optional[dict]:
    """
    Obtiene información completa de una API Key (para debugging/admin).

    Args:
        api_key: La API Key a consultar

    Returns:
        Dict con toda la información de la key, o None si no existe
    """
    from database import fetch_one

    api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    try:
        result = await fetch_one(
            """
            SELECT
                ak.id,
                ak.api_key_prefix,
                ak.municipality_id,
                ak.name,
                ak.description,
                ak.is_active,
                ak.created_at,
                ak.expires_at,
                ak.last_used_at,
                ak.rate_limit_per_minute,
                ak.created_by,
                m.schema_name,
                m.name as municipality_name
            FROM public.api_keys ak
            JOIN public.municipalities m ON ak.municipality_id = m.id
            WHERE ak.api_key_hash = $1
            """,
            api_key_hash,
            schema_name="public"
        )

        if result:
            return {
                "id": str(result["id"]),
                "name": result["name"],
                "description": result["description"],
                "is_active": result["is_active"],
                "created_at": str(result["created_at"]) if result["created_at"] else None,
                "expires_at": str(result["expires_at"]) if result["expires_at"] else None,
                "last_used_at": str(result["last_used_at"]) if result["last_used_at"] else None,
                "rate_limit_per_minute": result["rate_limit_per_minute"],
                "created_by": result["created_by"],
                "municipality": {
                    "id": str(result["municipality_id"]),
                    "name": result["municipality_name"],
                    "schema_name": result["schema_name"]
                }
            }
        return None

    except Exception as e:
        logger.error(f"[REST Auth] Error obteniendo info de API Key: {e}")
        return None


# ============================================================================
# BACKUP API KEY AUTHENTICATION
# ============================================================================

class BackupAuthError(Exception):
    """Error de autenticación backup con status HTTP."""
    def __init__(self, message: str, status_code: int = 401, retry_after: int = None):
        self.message = message
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(message)


# Cache DNS: {domain: (ips, timestamp)}
_dns_cache: dict = {}
_DNS_CACHE_TTL = 300  # 5 minutos
_DNS_CACHE_MAX = 50   # Max entradas (eviction de expiradas)


def _resolve_dns(domain: str) -> list:
    """Resuelve dominio a lista de IPs con cache de 5 minutos."""
    now = time.time()
    cached = _dns_cache.get(domain)
    if cached and (now - cached[1]) < _DNS_CACHE_TTL:
        return cached[0]

    # Evict entradas expiradas si el cache crece demasiado
    if len(_dns_cache) >= _DNS_CACHE_MAX:
        expired = [k for k, (_, ts) in _dns_cache.items() if (now - ts) >= _DNS_CACHE_TTL]
        for k in expired:
            del _dns_cache[k]

    try:
        results = socket.getaddrinfo(domain, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        ips = list(set(r[4][0] for r in results))
        _dns_cache[domain] = (ips, now)
        return ips
    except socket.gaierror:
        logger.warning(f"[Backup Auth] DNS resolution failed for {domain}")
        return []


def _check_origin(client_ip: str, allowed_origins: list) -> bool:
    """Verifica si client_ip está en la lista de orígenes permitidos."""
    if allowed_origins is None:
        return True

    for origin in allowed_origins:
        # Comparación directa (IP)
        if origin == client_ip:
            return True
        # Si parece dominio (no es IP), resolver DNS
        if not origin.replace(".", "").replace(":", "").isdigit():
            resolved_ips = _resolve_dns(origin)
            if client_ip in resolved_ips:
                return True

    return False


async def validate_backup_api_key(request) -> dict:
    """
    Valida Backup API Key con 2 capas de seguridad.

    Capa 1: Key válida + key_type='backup' + activa + muni activa + no expirada
    Capa 2: IP/DNS contra allowed_origins

    Rate limit se maneja por separado en check_and_log_sync_access().

    Args:
        request: Starlette Request object

    Returns:
        dict con api_key_id, municipality_id, schema_name, rate_limit_per_minute

    Raises:
        BackupAuthError: Con status_code apropiado (401, 403)
    """
    from database import fetch_one

    # Guardia: X-User-ID PROHIBIDO para backup keys
    if request.headers.get("X-User-ID"):
        logger.warning("[Backup Auth] Request con X-User-ID prohibido")
        raise BackupAuthError("Acceso denegado", 401)

    # Obtener API Key
    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise BackupAuthError("Acceso denegado", 401)

    # === CAPA 1: Key válida ===
    api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    try:
        result = await fetch_one(
            """
            SELECT
                ak.id,
                ak.api_key_prefix,
                ak.municipality_id,
                ak.name as key_name,
                ak.key_type,
                ak.expires_at,
                ak.is_active as key_active,
                ak.allowed_origins,
                ak.rate_limit_per_minute,
                m.schema_name,
                m.name as municipality_name,
                m.is_active as muni_active
            FROM public.api_keys ak
            JOIN public.municipalities m ON ak.municipality_id = m.id
            WHERE ak.api_key_hash = $1
            """,
            api_key_hash,
            schema_name="public"
        )
    except Exception as e:
        logger.error(f"[Backup Auth] Error consultando API Key: {e}")
        raise BackupAuthError("Acceso denegado", 401)

    # Validaciones genéricas (sin revelar motivo específico)
    if not result:
        logger.warning(f"[Backup Auth] Key no encontrada: {api_key[:12]}...")
        raise BackupAuthError("Acceso denegado", 401)

    if result.get("key_type") != "backup":
        logger.warning(f"[Backup Auth] Key tipo '{result.get('key_type')}' no es backup: {result['key_name']}")
        raise BackupAuthError("Acceso denegado", 401)

    if not result.get("key_active"):
        logger.warning(f"[Backup Auth] Key inactiva: {result['key_name']}")
        raise BackupAuthError("Acceso denegado", 401)

    if not result.get("muni_active"):
        logger.warning(f"[Backup Auth] Municipalidad inactiva: {result['municipality_name']}")
        raise BackupAuthError("Acceso denegado", 401)

    expires_at = result.get("expires_at")
    if expires_at and expires_at < datetime.now(expires_at.tzinfo):
        logger.warning(f"[Backup Auth] Key expirada: {result['key_name']}")
        raise BackupAuthError("Acceso denegado", 401)

    api_key_id = result["id"]
    schema_name = result["schema_name"]
    validate_schema_name(schema_name)  # Defense in depth

    # === CAPA 2: Verificar IP/DNS ===
    client_ip = request.client.host if request.client else None
    allowed_origins = result.get("allowed_origins")

    if not _check_origin(client_ip, allowed_origins):
        logger.warning(f"[Backup Auth] IP {client_ip} no autorizada para key {result['key_name']}")
        raise BackupAuthError("Acceso denegado", 403)

    # Actualizar last_used_at
    await _update_last_used(api_key_id)

    logger.info(f"[Backup Auth] Acceso autorizado: key={result['key_name']}, schema={schema_name}, ip={client_ip}")

    return {
        "api_key_id": str(api_key_id),
        "municipality_id": str(result["municipality_id"]),
        "schema_name": schema_name,
        "rate_limit_per_minute": result.get("rate_limit_per_minute") or 60
    }


async def check_and_log_sync_access(
    api_key_id: str,
    schema_name: str,
    action: str,
    ip: str,
    user_agent: str,
    rate_limit_per_minute: int
) -> Optional[int]:
    """
    Check rate limit y log de acceso atómico (INSERT ... WHERE NOT EXISTS).

    Si el acceso está permitido, inserta el log y retorna None.
    Si está rate-limited, retorna retry_after en segundos.

    Args:
        api_key_id: UUID de la API key
        schema_name: Schema del tenant
        action: Acción (ej: 'sync_data')
        ip: IP del cliente
        user_agent: User-Agent header
        rate_limit_per_minute: Requests por minuto permitidos

    Returns:
        None si acceso permitido (ya logueado), int retry_after si rate-limited
    """
    from database import fetch_one

    interval_seconds = 60 / max(rate_limit_per_minute, 1)

    try:
        # INSERT atómico: solo inserta si no hay acceso reciente
        result = await fetch_one(
            """
            INSERT INTO public.backup_access_log
                (api_key_id, schema_name, action, ip_address, user_agent)
            SELECT $1, $2, $3, $4, $5
            WHERE NOT EXISTS (
                SELECT 1 FROM public.backup_access_log
                WHERE api_key_id = $6 AND action = $7
                AND created_at > NOW() - make_interval(secs => $8)
            )
            RETURNING id
            """,
            api_key_id, schema_name, action, ip, user_agent,
            api_key_id, action, int(interval_seconds),
            schema_name="public"
        )
    except Exception as e:
        logger.error(f"[Backup Auth] Error en check_and_log_sync_access: {e}")
        # Fail-closed: si la BD falla, el sync tampoco puede leer datos.
        # Mejor fallar temprano que dejar pasar y fallar despues sin registro.
        return 60  # retry_after = 60s (retorna int = rate limited)

    if result:
        # Acceso permitido, ya quedó logueado
        return None

    # Rate limited — calcular retry_after
    try:
        last_access = await fetch_one(
            """
            SELECT created_at FROM public.backup_access_log
            WHERE api_key_id = $1 AND action = $2
            ORDER BY created_at DESC LIMIT 1
            """,
            api_key_id, action,
            schema_name="public"
        )
    except Exception:
        return int(interval_seconds)

    if last_access and last_access.get("created_at"):
        last_ts = last_access["created_at"]
        now = datetime.now(last_ts.tzinfo)
        elapsed = (now - last_ts).total_seconds()
        retry_after = max(1, int(interval_seconds - elapsed))
        return retry_after

    return int(interval_seconds)
