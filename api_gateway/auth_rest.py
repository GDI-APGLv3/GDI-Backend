import hashlib
import hmac
import os
import re
import sys
import socket
import uuid
from shared.logging import get_logger
from api_gateway.rate_limiter import cap_rate_limit
from typing import Optional
from datetime import datetime
import time

SERVICE_AUTH_ALLOWED_PATHS = [
    re.compile(r'^/api/v1/documents/(?P<resource_id>[^/]+)/url$'),
]

_SERVICE_AUTH_USER_ID = "a1000000-0000-0000-0000-000000000100"

_last_used_writes: dict[str, float] = {}
LAST_USED_WRITE_INTERVAL_SECONDS = 60
_LAST_USED_WRITES_MAX_SIZE = 10_000

API_KEY_CACHE_TTL_SECONDS = int(os.getenv("API_KEY_CACHE_TTL_SECONDS", "60"))
_API_KEY_CACHE_MAX_SIZE = 10_000
_API_KEY_USER_CACHE_MAX_SIZE = 10_000

_api_key_row_cache: dict[str, tuple[dict, float]] = {}
_api_key_user_cache: dict[tuple, float] = {}


def _api_key_cache_get(api_key_hash: str) -> Optional[dict]:
    if API_KEY_CACHE_TTL_SECONDS <= 0:
        return None
    entry = _api_key_row_cache.get(api_key_hash)
    if entry is None:
        return None
    row, ts = entry
    if time.monotonic() - ts > API_KEY_CACHE_TTL_SECONDS:
        return None
    return row


def _api_key_cache_set(api_key_hash: str, row: dict) -> None:
    if API_KEY_CACHE_TTL_SECONDS <= 0:
        return
    if len(_api_key_row_cache) >= _API_KEY_CACHE_MAX_SIZE:
        _api_key_row_cache.clear()
    _api_key_row_cache[api_key_hash] = (row, time.monotonic())


def _api_key_user_cache_get(api_key_id, user_id) -> bool:
    if API_KEY_CACHE_TTL_SECONDS <= 0:
        return False
    ts = _api_key_user_cache.get((api_key_id, user_id))
    if ts is None:
        return False
    return (time.monotonic() - ts) <= API_KEY_CACHE_TTL_SECONDS


def _api_key_user_cache_set(api_key_id, user_id) -> None:
    if API_KEY_CACHE_TTL_SECONDS <= 0:
        return
    if len(_api_key_user_cache) >= _API_KEY_USER_CACHE_MAX_SIZE:
        _api_key_user_cache.clear()
    _api_key_user_cache[(api_key_id, user_id)] = time.monotonic()

SERVICE_JOB_SIGNATURE_REQUIRED = os.getenv("SERVICE_JOB_SIGNATURE_REQUIRED", "false").lower() == "true"
SERVICE_JOB_SIGNATURE_TTL_SECONDS = 300
SERVICE_JOB_SIGNATURE_HEADER = "X-Service-Job-Signature"


def _verify_service_job_signature(
    header_value: Optional[str],
    *,
    service_secret: str,
    schema_name: str,
    resource_id: str,
) -> None:
    if not header_value:
        if SERVICE_JOB_SIGNATURE_REQUIRED:
            raise ValueError(f"{SERVICE_JOB_SIGNATURE_HEADER} header requerido para service auth")
        return

    try:
        ts_str, hmac_hex = header_value.split(".", 1)
        ts = int(ts_str)
    except (ValueError, AttributeError):
        raise ValueError(f"{SERVICE_JOB_SIGNATURE_HEADER} formato invalido")

    now = int(time.time())
    if abs(now - ts) > SERVICE_JOB_SIGNATURE_TTL_SECONDS:
        raise ValueError(f"{SERVICE_JOB_SIGNATURE_HEADER} expirado")

    expected = hmac.new(
        service_secret.encode(),
        f"{schema_name}:{resource_id}:{ts_str}".encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, hmac_hex):
        raise ValueError(f"{SERVICE_JOB_SIGNATURE_HEADER} invalido")

backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, backend_root)

from api_gateway.context import MCPContext
from database import validate_schema_name
from shared.exceptions import DatabaseBusyError

logger = get_logger(__name__)


async def validate_rest_api_key(api_key: str, user_id: str = None, request=None) -> MCPContext:
    from database import fetch_one

    if not api_key:
        raise ValueError("X-API-Key header requerido")

    service_secret = os.getenv("AGENT_GATEWAY_SECRET")
    if request is not None and service_secret and hmac.compare_digest(api_key, service_secret):
        path = request.url.path
        path_match = next((p.match(path) for p in SERVICE_AUTH_ALLOWED_PATHS if p.match(path)), None)
        if not path_match:
            logger.warning(f"[Service Auth] Intento en path no permitido: {path}")
            raise ValueError("Service auth no permitido en este endpoint")

        schema_name = request.headers.get("X-Tenant-Schema")
        if not schema_name:
            raise ValueError("X-Tenant-Schema header requerido para service auth")

        validate_schema_name(schema_name)

        resource_id = path_match.group("resource_id")
        try:
            _verify_service_job_signature(
                request.headers.get(SERVICE_JOB_SIGNATURE_HEADER),
                service_secret=service_secret,
                schema_name=schema_name,
                resource_id=resource_id,
            )
        except ValueError as e:
            logger.warning(f"[Service Auth] {SERVICE_JOB_SIGNATURE_HEADER} rechazado: {e}")
            raise

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
        except DatabaseBusyError:
            raise
        except Exception as e:
            logger.error(f"[Service Auth] Error consultando municipalities: {e}")
            raise ValueError("Error validando schema")

        if not row:
            logger.warning(f"[Service Auth] Schema invalido o inactivo: {schema_name}")
            raise ValueError("Schema invalido o inactivo")

        logger.info(f"[Service Auth] OK: path={path} schema={schema_name}")

        return MCPContext(
            api_key=api_key,
            municipality_id=str(row["id"]),
            schema_name=schema_name,
            auth_source="service",
            user_id=_SERVICE_AUTH_USER_ID,
        )


    if not user_id:
        raise ValueError("X-User-ID header requerido para REST API")

    api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    result = _api_key_cache_get(api_key_hash)
    if result is None:
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
        except DatabaseBusyError:
            raise
        except Exception as e:
            logger.error(f"[REST Auth] Error consultando API Key: {e}")
            raise ValueError("Error validando API Key")

        if not result:
            logger.warning(f"[REST Auth] API Key no encontrada: {api_key[:12]}...")
            raise ValueError("API Key inválida")

        result = dict(result)
        _api_key_cache_set(api_key_hash, result)

    if not result.get("key_active"):
        logger.warning(f"[REST Auth] API Key inactiva: {result['key_name']}")
        raise ValueError("API Key inactiva")

    if not result.get("muni_active"):
        logger.warning(f"[REST Auth] Municipalidad inactiva: {result['municipality_name']}")
        raise ValueError("Municipalidad inactiva")

    expires_at = result.get("expires_at")
    if expires_at and expires_at < datetime.now(expires_at.tzinfo):
        logger.warning(f"[REST Auth] API Key expirada: {result['key_name']}")
        raise ValueError("API Key expirada")

    api_key_id = result["id"]
    schema_name = result["schema_name"]
    validate_schema_name(schema_name)

    if not _api_key_user_cache_get(api_key_id, user_id):
        try:
            user_allowed = await fetch_one(
                """
                SELECT user_id FROM public.api_key_users
                WHERE api_key_id = $1 AND user_id = $2 AND schema_name = $3
                """,
                api_key_id, user_id, schema_name,
                schema_name="public"
            )
        except DatabaseBusyError:
            raise
        except Exception as e:
            logger.error(f"[REST Auth] Error verificando usuario autorizado: {e}")
            raise ValueError("Error validando usuario")

        if not user_allowed:
            logger.warning(f"[REST Auth] Usuario {user_id} no autorizado para API Key {result['key_name']}")
            raise ValueError(f"Usuario no autorizado para esta API Key")

        _api_key_user_cache_set(api_key_id, user_id)

    rate_limit_per_minute = result.get("rate_limit_per_minute")
    if rate_limit_per_minute:
        from api_gateway.rate_limiter import rate_limiter
        rate_limit_per_minute = cap_rate_limit(rate_limit_per_minute, "api", key_id=api_key_id)
        rate_limiter.check(f"rest_key:{api_key_id}", rate_limit_per_minute)

    await _update_last_used(api_key_id)

    ctx = MCPContext(
        api_key=api_key,
        municipality_id=str(result["municipality_id"]),
        schema_name=schema_name,
        auth_source="api_key",
        user_id=user_id
    )

    logger.info(f"[REST Auth] API Key válida: {result['key_name']}, schema: {schema_name}, user: {user_id}")
    return ctx


async def _update_last_used(api_key_id: str) -> None:
    from database import execute

    now = time.monotonic()
    last_write = _last_used_writes.get(api_key_id, 0)
    if now - last_write < LAST_USED_WRITE_INTERVAL_SECONDS:
        return

    try:
        await execute(
            "UPDATE public.api_keys SET last_used_at = NOW() WHERE id = $1",
            api_key_id,
            schema_name="public"
        )
        if len(_last_used_writes) >= _LAST_USED_WRITES_MAX_SIZE:
            _last_used_writes.clear()
        _last_used_writes[api_key_id] = now
    except Exception as e:
        logger.debug(f"[REST Auth] Error actualizando last_used_at: {e}")


class PublicAuthError(Exception):
    def __init__(self, message: str, status_code: int = 401):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


PUBLIC_ALLOWED_KEY_TYPES = frozenset({"api"})

_PUBLIC_AUTH_GENERIC_401 = "API Key invalida"


async def validate_public_api_key(api_key: str, muni_acronym: str) -> str:
    from database import fetch_one

    if not api_key:
        raise PublicAuthError("X-API-Key requerido", status_code=401)

    api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    try:
        row = await fetch_one(
            """
            SELECT
                ak.id,
                ak.is_active AS key_active,
                ak.expires_at,
                ak.rate_limit_per_minute,
                ak.key_type,
                m.schema_name,
                m.acronym,
                m.is_active AS muni_active
            FROM public.api_keys ak
            JOIN public.municipalities m ON ak.municipality_id = m.id
            WHERE ak.api_key_hash = $1
            """,
            api_key_hash,
            schema_name="public",
        )
    except DatabaseBusyError:
        logger.error("[PublicInfo Auth] Pool saturado consultando API Key; se responde 503")
        raise PublicAuthError("Servidor ocupado, reintente en unos segundos",
                              status_code=503)
    except Exception as e:
        logger.error(f"[PublicInfo Auth] Error consultando API Key: {e}")
        raise PublicAuthError(_PUBLIC_AUTH_GENERIC_401, status_code=401)

    if not row:
        logger.warning(f"[PublicInfo Auth] API Key no encontrada: {api_key[:12]}...")
        raise PublicAuthError(_PUBLIC_AUTH_GENERIC_401, status_code=401)

    if (row.get("key_type") or "") not in PUBLIC_ALLOWED_KEY_TYPES:
        logger.warning(f"[PublicInfo Auth] key_type no permitido en bloque publico: {row.get('key_type')!r}")
        raise PublicAuthError(_PUBLIC_AUTH_GENERIC_401, status_code=401)

    if not row.get("key_active"):
        logger.warning("[PublicInfo Auth] API Key inactiva")
        raise PublicAuthError(_PUBLIC_AUTH_GENERIC_401, status_code=401)

    if not row.get("muni_active"):
        logger.warning("[PublicInfo Auth] Municipalidad inactiva")
        raise PublicAuthError(_PUBLIC_AUTH_GENERIC_401, status_code=401)

    expires_at = row.get("expires_at")
    if expires_at and expires_at < datetime.now(expires_at.tzinfo):
        logger.warning("[PublicInfo Auth] API Key expirada")
        raise PublicAuthError(_PUBLIC_AUTH_GENERIC_401, status_code=401)

    key_acronym = (row.get("acronym") or "").strip().lower()
    if key_acronym != muni_acronym:
        logger.warning(
            f"[PublicInfo Auth] API Key del muni '{key_acronym}' intento acceder a '{muni_acronym}'"
        )
        raise PublicAuthError("API Key no valida para este municipio", status_code=403)

    schema_name = row["schema_name"]
    validate_schema_name(schema_name)

    rate_limit_per_minute = row.get("rate_limit_per_minute") or 60
    rate_limit_per_minute = cap_rate_limit(rate_limit_per_minute, "public", key_id=str(row["id"]))
    from api_gateway.rate_limiter import rate_limiter
    rate_limiter.check(f"public_key:{row['id']}", rate_limit_per_minute)

    await _update_last_used(row["id"])

    return schema_name


class TadAuthError(Exception):
    def __init__(self, message: str, status_code: int = 401):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


TAD_ALLOWED_KEY_TYPES = frozenset({"tad"})

TAD_DEFAULT_RATE_LIMIT_PER_MINUTE = 30

_TAD_AUTH_GENERIC_401 = "API Key invalida"


def _looks_like_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


async def validate_tad_api_key(
    api_key: str,
    citizen_ref: Optional[str] = None,
    *,
    strict_rate_limit: Optional[int] = None,
) -> tuple[str, Optional[dict]]:
    from database import fetch_one

    if not api_key:
        raise TadAuthError("X-API-Key requerido", status_code=401)

    api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()

    try:
        row = await fetch_one(
            """
            SELECT
                ak.id,
                ak.is_active AS key_active,
                ak.expires_at,
                ak.rate_limit_per_minute,
                ak.key_type,
                m.schema_name,
                m.is_active AS muni_active
            FROM public.api_keys ak
            JOIN public.municipalities m ON ak.municipality_id = m.id
            WHERE ak.api_key_hash = $1
            """,
            api_key_hash,
            schema_name="public",
        )
    except DatabaseBusyError:
        logger.error("[TAD Auth] Pool saturado consultando API Key; se responde 503")
        raise TadAuthError("Servidor ocupado, reintente en unos segundos",
                           status_code=503)
    except Exception as e:
        logger.error(f"[TAD Auth] Error consultando API Key: {e}")
        raise TadAuthError(_TAD_AUTH_GENERIC_401, status_code=401)

    if not row:
        logger.warning(f"[TAD Auth] API Key no encontrada: {api_key[:12]}...")
        raise TadAuthError(_TAD_AUTH_GENERIC_401, status_code=401)

    if (row.get("key_type") or "") not in TAD_ALLOWED_KEY_TYPES:
        logger.warning(f"[TAD Auth] key_type no permitido en bloque TAD: {row.get('key_type')!r}")
        raise TadAuthError(_TAD_AUTH_GENERIC_401, status_code=401)

    if not row.get("key_active"):
        logger.warning("[TAD Auth] API Key inactiva")
        raise TadAuthError(_TAD_AUTH_GENERIC_401, status_code=401)

    if not row.get("muni_active"):
        logger.warning("[TAD Auth] Municipalidad inactiva")
        raise TadAuthError(_TAD_AUTH_GENERIC_401, status_code=401)

    expires_at = row.get("expires_at")
    if expires_at and expires_at < datetime.now(expires_at.tzinfo):
        logger.warning("[TAD Auth] API Key expirada")
        raise TadAuthError(_TAD_AUTH_GENERIC_401, status_code=401)

    schema_name = row["schema_name"]
    validate_schema_name(schema_name)

    rate_limit_per_minute = row.get("rate_limit_per_minute") or TAD_DEFAULT_RATE_LIMIT_PER_MINUTE
    rate_limit_per_minute = cap_rate_limit(rate_limit_per_minute, "tad", key_id=str(row["id"]))
    from api_gateway.rate_limiter import rate_limiter
    rate_limiter.check(f"tad_key:{row['id']}", rate_limit_per_minute)

    if strict_rate_limit is not None:
        rate_limiter.check(f"tad_key:{row['id']}:strict", strict_rate_limit)

    await _update_last_used(row["id"])

    citizen = None
    if citizen_ref:
        citizen_ref = citizen_ref.strip()
        if _looks_like_uuid(citizen_ref):
            citizen_row = await fetch_one(
                "SELECT id, full_name, country_id, estado FROM citizens WHERE id = $1",
                citizen_ref,
                schema_name=schema_name,
            )
        else:
            citizen_row = await fetch_one(
                "SELECT id, full_name, country_id, estado FROM citizens WHERE country_id = $1 LIMIT 1",
                citizen_ref,
                schema_name=schema_name,
            )
        if citizen_row:
            citizen = dict(citizen_row)
            if citizen.get("estado") == "bloqueado":
                logger.warning(f"[TAD Auth] Ciudadano bloqueado intento operar: {citizen['id']}")
                raise TadAuthError("Ciudadano bloqueado", status_code=403)

    logger.info(f"[TAD Auth] API Key TAD valida, schema: {schema_name}")
    return schema_name, citizen


class BackupAuthError(Exception):
    def __init__(self, message: str, status_code: int = 401, retry_after: int = None):
        self.message = message
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(message)


_dns_cache: dict = {}
_DNS_CACHE_TTL = 300
_DNS_CACHE_MAX = 50


def _resolve_dns(domain: str) -> list:
    now = time.time()
    cached = _dns_cache.get(domain)
    if cached and (now - cached[1]) < _DNS_CACHE_TTL:
        return cached[0]

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
    if allowed_origins is None:
        return True

    for origin in allowed_origins:
        if origin == client_ip:
            return True
        if not origin.replace(".", "").replace(":", "").isdigit():
            resolved_ips = _resolve_dns(origin)
            if client_ip in resolved_ips:
                return True

    return False


async def validate_backup_api_key(request) -> dict:
    from database import fetch_one

    if request.headers.get("X-User-ID"):
        logger.warning("[Backup Auth] Request con X-User-ID prohibido")
        raise BackupAuthError("Acceso denegado", 401)

    api_key = request.headers.get("X-API-Key")
    if not api_key:
        raise BackupAuthError("Acceso denegado", 401)

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
    validate_schema_name(schema_name)

    client_ip = request.client.host if request.client else None
    allowed_origins = result.get("allowed_origins")

    if not _check_origin(client_ip, allowed_origins):
        logger.warning(f"[Backup Auth] IP {client_ip} no autorizada para key {result['key_name']}")
        raise BackupAuthError("Acceso denegado", 403)

    await _update_last_used(api_key_id)

    logger.info(f"[Backup Auth] Acceso autorizado: key={result['key_name']}, schema={schema_name}, ip={client_ip}")

    return {
        "api_key_id": str(api_key_id),
        "municipality_id": str(result["municipality_id"]),
        "schema_name": schema_name,
        "rate_limit_per_minute": cap_rate_limit(
            result.get("rate_limit_per_minute") or 60, "backup", key_id=str(result["id"])
        ),
    }


async def check_and_log_sync_access(
    api_key_id: str,
    schema_name: str,
    action: str,
    ip: str,
    user_agent: str,
    rate_limit_per_minute: int
) -> Optional[int]:
    from database import fetch_one

    interval_seconds = 60 / max(rate_limit_per_minute, 1)

    try:
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
            api_key_id, action, interval_seconds,
            schema_name="public"
        )
    except Exception as e:
        logger.error(f"[Backup Auth] Error en check_and_log_sync_access: {e}")
        return 60

    if result:
        return None

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
        return max(1, int(interval_seconds))

    if last_access and last_access.get("created_at"):
        last_ts = last_access["created_at"]
        now = datetime.now(last_ts.tzinfo)
        elapsed = (now - last_ts).total_seconds()
        retry_after = max(1, int(interval_seconds - elapsed))
        return retry_after

    return max(1, int(interval_seconds))
