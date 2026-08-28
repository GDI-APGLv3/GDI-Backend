import hmac
import os
import re
import time
import json as _json
import asyncpg
from contextlib import asynccontextmanager
from typing import Optional, Any
from dotenv import load_dotenv
from shared.logging import get_logger
from shared.exceptions import DatabaseBusyError
from config.constants import MAX_PAGE_SIZE
import asyncio

load_dotenv()

logger = get_logger(__name__)

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "railway")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

USE_PGBOUNCER = DB_PORT == "6432"

ASYNCPG_MIN_SIZE = int(os.getenv("ASYNCPG_MIN_SIZE", "2"))
ASYNCPG_MAX_SIZE = int(os.getenv("ASYNCPG_MAX_SIZE", "8"))
ASYNCPG_COMMAND_TIMEOUT = int(os.getenv("ASYNCPG_COMMAND_TIMEOUT", "60"))
ASYNCPG_MAX_INACTIVE_LIFETIME = float(os.getenv("ASYNCPG_MAX_INACTIVE_LIFETIME", "600"))

AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN", "")
AUTH0_AUDIENCE = os.getenv("AUTH0_AUDIENCE", "")
AUTH0_CLIENT_ID = os.getenv("AUTH0_CLIENT_ID", "")
AUTH0_CLIENT_SECRET = os.getenv("AUTH0_CLIENT_SECRET")
AUTH0_ALGORITHMS = ["RS256"]

AUTH0_ISSUERS: list = [
    s.strip().rstrip("/") + "/"
    for s in os.getenv("AUTH0_ISSUERS", f"https://{AUTH0_DOMAIN}/").split(",")
    if s.strip()
]

if not AUTH0_ISSUERS:
    raise RuntimeError(
        "AUTH0_ISSUERS quedo vacia tras normalizar la env var "
        "(revisar AUTH0_ISSUERS / AUTH0_DOMAIN) -- ningun JWT validaria el issuer."
    )

ENVIRONMENT = os.getenv("ENVIRONMENT", "development").strip().lower()
TESTING_SHARED_SECRET = os.getenv("TESTING_SHARED_SECRET", "")

TESTING_MODE = os.getenv("TESTING_MODE", "false").lower() == "true"
if TESTING_MODE:
    if ENVIRONMENT == "production":
        raise RuntimeError(
            "TESTING_MODE=true con ENVIRONMENT=production: el bypass de Auth0 "
            "jamas puede convivir con produccion. Sacar el secret TESTING_MODE "
            "del ambiente o corregir ENVIRONMENT antes de deployar."
        )
    if os.getenv("FLY_APP_NAME"):
        fly_app = os.getenv("FLY_APP_NAME", "")
        allowed = "-dev" in fly_app or fly_app.startswith("demo-")
        if not allowed:
            TESTING_MODE = False
            logger.warning(f"TESTING_MODE desactivado: Fly.io producción ({fly_app})")
        else:
            logger.warning(f"TESTING_MODE habilitado en Fly.io ({fly_app})")
    elif os.getenv("ALLOW_TESTING_MODE_LOCAL", "false").lower() == "true":
        logger.warning("TESTING_MODE habilitado (ALLOW_TESTING_MODE_LOCAL=true) - Auth0 bypass activo. NO usar en producción.")
    else:
        TESTING_MODE = False
        logger.warning(
            "TESTING_MODE solicitado pero entorno NO reconocido como seguro "
            "(sin FLY_APP_NAME dev/demo ni ALLOW_TESTING_MODE_LOCAL=true). "
            "DESACTIVADO por seguridad (fail-closed)."
        )

if TESTING_MODE and not TESTING_SHARED_SECRET:
    TESTING_MODE = False
    logger.critical(
        "TESTING_MODE solicitado pero TESTING_SHARED_SECRET esta vacio. "
        "DESACTIVADO (fail-closed): el bypass de Auth0 exige secreto compartido."
    )


def testing_secret_matches(provided: Optional[str]) -> bool:
    if not TESTING_SHARED_SECRET or not provided:
        return False
    return hmac.compare_digest(provided, TESTING_SHARED_SECRET)

DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true"

MUNICIPIO_PRINCIPAL_ID = "550e8400-e29b-41d4-a716-446655440000"
MUNICIPIO_ACRONYM = "SMG"
EXPEDIENTE_PREFIX = "EE"
DEFAULT_PAGE_SIZE = 20

_SCHEMA_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9_]+$')
_RESERVED_SCHEMAS = {"information_schema", "pg_catalog", "pg_toast"}

def validate_schema_name(schema_name: str) -> str:
    if not schema_name or not schema_name.strip():
        raise ValueError(
            "schema_name es REQUERIDO. "
            "Obtener de request.state.schema_name o pasar explícitamente."
        )
    schema_name = schema_name.strip()
    if not _SCHEMA_NAME_PATTERN.match(schema_name):
        raise ValueError(
            f"schema_name inválido: '{schema_name}'. "
            "Solo se permiten letras, números y guión bajo."
        )
    if len(schema_name) > 63:
        raise ValueError(
            f"schema_name demasiado largo: {len(schema_name)} caracteres (max 63)."
        )
    if schema_name in _RESERVED_SCHEMAS:
        raise ValueError(
            f"schema_name reservado: '{schema_name}' no está permitido."
        )
    return schema_name

_DEAD_CONNECTION_ERRORS = (
    asyncpg.exceptions.ConnectionDoesNotExistError,
    asyncpg.exceptions.InterfaceError,
    ConnectionResetError,
    OSError,
)
_CONN_MAX_RETRIES = 2
_CONN_RETRY_DELAY = 0.05
_CONN_HEALTHCHECK_TIMEOUT = float(os.getenv("CONN_HEALTHCHECK_TIMEOUT", "0.5"))
_POOL_ACQUIRE_TIMEOUT = float(os.getenv("ASYNCPG_ACQUIRE_TIMEOUT", "3.0"))
_ACQUIRE_TOTAL_BUDGET = _POOL_ACQUIRE_TIMEOUT + (_CONN_MAX_RETRIES + 1) * _CONN_HEALTHCHECK_TIMEOUT


async def _acquire_healthy_conn() -> asyncpg.Connection:
    pool_ref = get_pool()
    last_exc: Optional[Exception] = None
    deadline = time.monotonic() + _ACQUIRE_TOTAL_BUDGET
    for attempt in range(_CONN_MAX_RETRIES + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise DatabaseBusyError(
                "Pool de conexiones saturado (presupuesto total de acquire agotado)",
                details={"remaining_budget": remaining},
            ) from last_exc
        try:
            attempt_timeout = min(remaining, _POOL_ACQUIRE_TIMEOUT)
            conn = await pool_ref.acquire(timeout=attempt_timeout)
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise DatabaseBusyError(
                "Pool de conexiones saturado (acquire timeout)",
                details={"remaining_budget": remaining, "attempt_timeout": attempt_timeout},
            ) from exc
        try:
            preflight_timeout = min(deadline - time.monotonic(), _CONN_HEALTHCHECK_TIMEOUT)
            if preflight_timeout <= 0:
                raise asyncio.TimeoutError()
            await asyncio.wait_for(
                conn.fetchval("SELECT 1"),
                timeout=preflight_timeout,
            )
            return conn
        except asyncio.TimeoutError as exc:
            last_exc = exc
            if attempt < _CONN_MAX_RETRIES and (deadline - time.monotonic()) > 0:
                try:
                    await pool_ref.release(conn)
                except Exception:
                    pass
                logger.warning(
                    "_acquire_healthy_conn: pre-flight lento (>%.1fs) en intento "
                    "%d/%d — la conn NO se descarta (no esta muerta, esta "
                    "saturada), se pide otra al pool",
                    _CONN_HEALTHCHECK_TIMEOUT, attempt + 1, _CONN_MAX_RETRIES,
                )
                await asyncio.sleep(min(_CONN_RETRY_DELAY, max(deadline - time.monotonic(), 0)))
                continue
            try:
                await pool_ref.release(conn)
            except Exception:
                pass
            logger.error(
                "_acquire_healthy_conn: pre-flight sigue lento tras agotar "
                "reintentos o presupuesto — Postgres saturado, no un hipo "
                "aislado. Fail-fast en vez de arriesgar la query real: %s",
                exc,
            )
            raise DatabaseBusyError(
                "Pool de conexiones saturado (pre-flight lento tras reintentar)",
                details={"timeout_s": _CONN_HEALTHCHECK_TIMEOUT},
            ) from exc
        except _DEAD_CONNECTION_ERRORS as exc:
            last_exc = exc
            try:
                conn.terminate()
            except Exception:
                pass
            try:
                await pool_ref.release(conn)
            except Exception:
                pass
            if attempt < _CONN_MAX_RETRIES:
                logger.warning(
                    "_acquire_healthy_conn: conexion muerta en pre-flight "
                    "(intento %d/%d), reintentando — %s",
                    attempt + 1, _CONN_MAX_RETRIES, exc,
                )
                await asyncio.sleep(_CONN_RETRY_DELAY)
            else:
                logger.error(
                    "_acquire_healthy_conn: pool no entrego conexion sana tras "
                    "%d reintentos — %s",
                    _CONN_MAX_RETRIES, exc,
                )
                raise DatabaseBusyError(
                    "Pool de conexiones saturado (conexion muerta tras reintentar)",
                    details={"cause": type(exc).__name__},
                ) from exc
        except Exception:
            try:
                await pool_ref.release(conn)
            except Exception:
                pass
            raise
    # Inalcanzable por la logica del raise dentro del loop, pero mypy lo pide:
    raise last_exc if last_exc is not None else RuntimeError("acquire failed")

pool: Optional[asyncpg.Pool] = None


async def _init_conn(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec(
        'json', encoder=_json.dumps, decoder=_json.loads, schema='pg_catalog'
    )
    await conn.set_type_codec(
        'jsonb', encoder=_json.dumps, decoder=_json.loads, schema='pg_catalog'
    )
    await conn.set_type_codec(
        'uuid', encoder=str, decoder=str, schema='pg_catalog'
    )


async def init_pool() -> asyncpg.Pool:
    global pool
    if pool is not None:
        return pool
    pool = await asyncpg.create_pool(
        dsn=DATABASE_URL,
        min_size=ASYNCPG_MIN_SIZE,
        max_size=ASYNCPG_MAX_SIZE,
        command_timeout=ASYNCPG_COMMAND_TIMEOUT,
        init=_init_conn,
        server_settings={"jit": "off"},
        statement_cache_size=0,
        max_inactive_connection_lifetime=ASYNCPG_MAX_INACTIVE_LIFETIME,
    )
    logger.info(
        "Pool asyncpg inicializado (min=%d max=%d max_inactive_lifetime=%.0fs "
        "healthcheck_timeout=%.1fs)",
        ASYNCPG_MIN_SIZE,
        ASYNCPG_MAX_SIZE,
        ASYNCPG_MAX_INACTIVE_LIFETIME,
        _CONN_HEALTHCHECK_TIMEOUT,
    )
    return pool


async def close_pool() -> None:
    global pool
    if pool is not None:
        await pool.close()
        pool = None


def get_pool() -> asyncpg.Pool:
    if pool is None:
        raise RuntimeError("Pool asyncpg no inicializado. Verificar lifespan en main.py.")
    return pool


@asynccontextmanager
async def get_conn(
    *,
    schema_name: str,
    user_id: Optional[str] = None,
    auth_source: Optional[str] = None,
):
    validated = validate_schema_name(schema_name)
    conn = await _acquire_healthy_conn()
    try:
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path TO "{validated}", public')
            if user_id is not None:
                await conn.execute("SELECT set_config('app.user_id', $1, true)", str(user_id))
            if auth_source is not None:
                await conn.execute("SELECT set_config('app.auth_source', $1, true)", auth_source)
            yield conn
    finally:
        try:
            await get_pool().release(conn)
        except Exception as release_exc:
            logger.warning("get_conn: error al liberar conn al pool — %s", release_exc)


async def fetch_all(sql: str, *params, schema_name: str) -> list[asyncpg.Record]:
    async with get_conn(schema_name=schema_name) as conn:
        return await conn.fetch(sql, *params)


async def fetch_one(sql: str, *params, schema_name: str) -> Optional[asyncpg.Record]:
    async with get_conn(schema_name=schema_name) as conn:
        return await conn.fetchrow(sql, *params)


async def fetch_val(sql: str, *params, schema_name: str, column: int = 0) -> Any:
    async with get_conn(schema_name=schema_name) as conn:
        return await conn.fetchval(sql, *params, column=column)


async def execute(
    sql: str,
    *params,
    schema_name: str,
    user_id: Optional[str] = None,
    auth_source: Optional[str] = None,
) -> str:
    async with get_conn(schema_name=schema_name, user_id=user_id, auth_source=auth_source) as conn:
        return await conn.execute(sql, *params)


async def execute_many(conn: asyncpg.Connection, sql: str, *arrays) -> str:
    return await conn.execute(sql, *arrays)


@asynccontextmanager
async def transaction(
    *,
    schema_name: str,
    user_id: Optional[str] = None,
    auth_source: Optional[str] = None,
):
    validated = validate_schema_name(schema_name)
    conn = await _acquire_healthy_conn()
    try:
        async with conn.transaction():
            await conn.execute(f'SET LOCAL search_path TO "{validated}", public')
            if user_id is not None:
                await conn.execute("SELECT set_config('app.user_id', $1, true)", str(user_id))
            if auth_source is not None:
                await conn.execute("SELECT set_config('app.auth_source', $1, true)", auth_source)
            yield conn
    finally:
        try:
            await get_pool().release(conn)
        except Exception as release_exc:
            logger.warning("transaction: error al liberar conn al pool — %s", release_exc)


async def test_connection() -> bool:
    try:
        result = await fetch_val("SELECT 1", schema_name="public")
        return result == 1
    except Exception as e:
        logger.error(f"Error probando conexión: {e}")
        return False


async def check_user_exists(user_id: str, *, schema_name: str) -> bool:
    row = await fetch_one(
        "SELECT id FROM users WHERE id = $1 LIMIT 1",
        user_id,
        schema_name=schema_name,
    )
    return row is not None


async def check_document_exists(document_id: str, *, schema_name: str) -> bool:
    row = await fetch_one(
        "SELECT id FROM document_draft WHERE id = $1 LIMIT 1",
        document_id,
        schema_name=schema_name,
    )
    return row is not None


async def check_case_exists(case_id: str, *, schema_name: str) -> bool:
    row = await fetch_one(
        "SELECT id FROM cases WHERE id = $1 LIMIT 1",
        case_id,
        schema_name=schema_name,
    )
    return row is not None


async def get_document_basic_info(document_id: str, *, schema_name: str) -> Optional[asyncpg.Record]:
    return await fetch_one(
        """
        SELECT
            dd.id as document_id,
            dd.reference,
            dd.status,
            dd.created_by,
            dt.acronym as document_type_acronym,
            dt.name as document_type_name
        FROM document_draft dd
        LEFT JOIN document_types dt ON dd.document_type_id = dt.id
        WHERE dd.id = $1
        """,
        document_id,
        schema_name=schema_name,
    )


def get_case_number_format(
    department_acronym: str,
    municipality_acronym: str,
    year: int = None,
) -> str:
    from datetime import datetime
    if year is None:
        year = datetime.now().year
    return f"{EXPEDIENTE_PREFIX}-{year}-{{sequence:06d}}-{municipality_acronym}-{department_acronym}"
