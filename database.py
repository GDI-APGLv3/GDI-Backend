import os
import re
import json as _json
import asyncpg
from contextlib import asynccontextmanager
from typing import Optional, Any
from dotenv import load_dotenv
import logging
import asyncio

load_dotenv()

logger = logging.getLogger(__name__)

# ============================================================================
# Config de conexión (idéntica al anterior — solo fuente de env vars)
# ============================================================================
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "railway")

# asyncpg usa DSN estándar. El search_path se setea por conexión con SET LOCAL,
# no en la URL (a diferencia del psycopg2 anterior con ?options=).
DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# Apuntamos directo a Postgres (asyncpg tiene su propio pool). El pool fuerza
# statement_cache_size=0 siempre (ver init_pool): es obligatorio en multi-tenant
# por el search_path dinámico, no solo por PgBouncer.
USE_PGBOUNCER = DB_PORT == "6432"

# Defaults conservadores. El consumo real es max_size × num_workers (ver Plan §5.2).
# ARIES/ARG Backend → setear ASYNCPG_MAX_SIZE=8 por secret explícito.
ASYNCPG_MIN_SIZE = int(os.getenv("ASYNCPG_MIN_SIZE", "2"))
ASYNCPG_MAX_SIZE = int(os.getenv("ASYNCPG_MAX_SIZE", "8"))
ASYNCPG_COMMAND_TIMEOUT = int(os.getenv("ASYNCPG_COMMAND_TIMEOUT", "60"))

# ============================================================================
# Auth0 / modos de operación (sin cambios — otros módulos los importan)
# ============================================================================
AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN", "")
AUTH0_AUDIENCE = os.getenv("AUTH0_AUDIENCE", "")
AUTH0_CLIENT_ID = os.getenv("AUTH0_CLIENT_ID", "")
AUTH0_CLIENT_SECRET = os.getenv("AUTH0_CLIENT_SECRET")
AUTH0_ALGORITHMS = ["RS256"]

TESTING_MODE = os.getenv("TESTING_MODE", "false").lower() == "true"
if TESTING_MODE:
    if os.getenv("FLY_APP_NAME"):
        fly_app = os.getenv("FLY_APP_NAME", "")
        allowed = "-dev" in fly_app or fly_app.startswith("demo-")
        if not allowed:
            TESTING_MODE = False
            logger.warning(f"TESTING_MODE desactivado: Fly.io producción ({fly_app})")
        else:
            logger.warning(f"TESTING_MODE habilitado en Fly.io ({fly_app})")
    elif os.getenv("ALLOW_TESTING_MODE_LOCAL", "false").lower() == "true":
        # Fail-closed: en entornos sin FLY_APP_NAME ni RAILWAY_ENVIRONMENT_NAME
        # (VPS, Docker plano, plataforma desconocida) el bypass de JWT solo se
        # permite si se opta-in EXPLÍCITAMENTE con ALLOW_TESTING_MODE_LOCAL=true.
        # Esto evita que un secret TESTING_MODE=true olvidado abra auth en un
        # entorno productivo no reconocido.
        logger.warning("TESTING_MODE habilitado (ALLOW_TESTING_MODE_LOCAL=true) - Auth0 bypass activo. NO usar en producción.")
    else:
        TESTING_MODE = False
        logger.warning(
            "TESTING_MODE solicitado pero entorno NO reconocido como seguro "
            "(sin FLY_APP_NAME dev/demo ni ALLOW_TESTING_MODE_LOCAL=true). "
            "DESACTIVADO por seguridad (fail-closed)."
        )

DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true"

# ============================================================================
# Constantes del negocio (sin cambios)
# ============================================================================
MUNICIPIO_PRINCIPAL_ID = "550e8400-e29b-41d4-a716-446655440000"
MUNICIPIO_ACRONYM = "SMG"
EXPEDIENTE_PREFIX = "EE"
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

# ============================================================================
# Validación de schema (S1-011: unificada con BackOffice-Back)
# ============================================================================
_SCHEMA_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9_]+$')
_RESERVED_SCHEMAS = {"information_schema", "pg_catalog", "pg_toast"}

def validate_schema_name(schema_name: str) -> str:
    """
    Valida schema_name contra SQL injection.
    - Elimina espacios (NO baja a minúsculas: los schemas pueden tener mayúsculas, ej. 100_INTE).
    - Solo permite letras (may/min), números y guión bajo.
    - Máximo 63 caracteres (límite PostgreSQL).
    - Bloquea schemas reservados de PostgreSQL.
    Ejemplos válidos: "100_test", "100_INTE", "municipio_abc".
    """
    if not schema_name or not schema_name.strip():
        raise ValueError(
            "schema_name es REQUERIDO. "
            "Obtener de request.state.schema_name o pasar explícitamente."
        )
    # NO bajar a minusculas: los schemas de municipios pueden tener mayusculas
    # (ej. 100_INTE). El .lower() de S1-011 rompia el SET search_path en PRD.
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

# ============================================================================
# Constantes de retry ante conexiones muertas post-resume (MejoraArranque FIX B)
# ============================================================================
# Excepciones que pueden disparar el pre-flight SELECT 1 cuando la conexion del
# pool murio durante la suspension de Fly.io (TCP cortado silenciosamente que
# asyncpg.Connection.is_closed() NO detecta — solo chequea estado local del
# protocolo). El pre-flight las atrapa y descarta la conn antes del yield.
_DEAD_CONNECTION_ERRORS = (
    asyncpg.exceptions.ConnectionDoesNotExistError,
    asyncpg.exceptions.InterfaceError,
    ConnectionResetError,
    OSError,                     # incluye BrokenPipeError, ConnectionAbortedError, etc.
    asyncio.TimeoutError,        # pre-flight excedio _CONN_HEALTHCHECK_TIMEOUT
)
# Maximo de reintentos para adquirir una conexion viva. 2 es suficiente: el primer
# reintento ya pide una conexion nueva al pool (el pool descarta automaticamente
# las conns marcadas como closed/aborted al release).
_CONN_MAX_RETRIES = 2
# Pausa minima entre reintentos (segundos). Corta para no impactar latencia.
_CONN_RETRY_DELAY = 0.05
# Timeout del pre-flight SELECT 1. Conns sanas en red local Fly responden en <1ms.
# Si tarda mas de 500ms, asumimos TCP roto post-suspend.
_CONN_HEALTHCHECK_TIMEOUT = 0.5


async def _acquire_healthy_conn() -> asyncpg.Connection:
    """
    Adquiere una conexion del pool, valida que este viva con SELECT 1 pre-flight,
    y la devuelve. Si la conn esta muerta, la termina, la libera al pool (que la
    descarta automaticamente al verla closed/aborted), y reintenta hasta
    _CONN_MAX_RETRIES veces.

    IMPORTANTE: Este helper NO es un contextmanager. El caller la libera con
    `await get_pool().release(conn)` despues del yield. El retry de acquire DEBE
    estar separado del yield para evitar RuntimeError("generator didn't stop after
    athrow()") cuando una excepcion entra post-yield (caller con conn rota a
    mitad de query, o COMMIT que falla).

    Patron de uso (en get_conn/transaction):
        conn = await _acquire_healthy_conn()
        try:
            async with conn.transaction():
                ... setup ...
                yield conn      # yield UNICO, no retryable
        finally:
            await get_pool().release(conn)
    """
    pool_ref = get_pool()
    last_exc: Optional[Exception] = None
    for attempt in range(_CONN_MAX_RETRIES + 1):
        conn = await pool_ref.acquire()
        try:
            # Pre-flight: SELECT 1 con timeout corto. Detecta TCP cortado durante
            # el suspend que is_closed() no ve (asyncpg solo chequea estado local
            # del protocolo, no la salud real del socket).
            await asyncio.wait_for(
                conn.fetchval("SELECT 1"),
                timeout=_CONN_HEALTHCHECK_TIMEOUT,
            )
            return conn
        except _DEAD_CONNECTION_ERRORS as exc:
            last_exc = exc
            # Conn muerta: terminate() (abort inmediato, no intenta flush como close)
            # y release() para que el pool la descarte. release() con conn cerrada
            # libera el holder sin reusar la conn (asyncpg internamente chequea
            # is_closed() y descarta).
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
                raise
        except Exception:
            # Cualquier otra excepcion: liberar la conn (no es problema de salud,
            # es un bug real) y propagar.
            try:
                await pool_ref.release(conn)
            except Exception:
                pass
            raise
    # Inalcanzable por la logica del raise dentro del loop, pero mypy lo pide:
    raise last_exc if last_exc is not None else RuntimeError("acquire failed")

# ============================================================================
# Pool asyncpg
# ============================================================================
pool: Optional[asyncpg.Pool] = None


async def _init_conn(conn: asyncpg.Connection) -> None:
    """Registra codecs para que asyncpg decodifique automáticamente."""
    await conn.set_type_codec(
        'json', encoder=_json.dumps, decoder=_json.loads, schema='pg_catalog'
    )
    await conn.set_type_codec(
        'jsonb', encoder=_json.dumps, decoder=_json.loads, schema='pg_catalog'
    )
    # UUID como str — restaura comportamiento psycopg2 y es compatible con Pydantic str fields
    await conn.set_type_codec(
        'uuid', encoder=str, decoder=str, schema='pg_catalog'
    )


async def init_pool() -> asyncpg.Pool:
    """Inicializar pool asyncpg. Llamar en el lifespan de FastAPI."""
    global pool
    if pool is not None:
        return pool
    pool = await asyncpg.create_pool(
        dsn=DATABASE_URL,
        min_size=ASYNCPG_MIN_SIZE,
        max_size=ASYNCPG_MAX_SIZE,
        command_timeout=ASYNCPG_COMMAND_TIMEOUT,
        init=_init_conn,
        # CRITICO multi-tenant: search_path dinamico por SET LOCAL es incompatible
        # con el cache de prepared statements de asyncpg (un statement preparado para
        # un schema se reusa en otro -> "relation does not exist" / leak cross-tenant).
        # En DEV no se nota (1 solo schema); en PRD con schemas por municipio revienta.
        statement_cache_size=0,
        # MejoraArranque FIX B: max_inactive_connection_lifetime acotado a 60s.
        # NO resuelve el caso post-resume de Fly.io: durante el suspend el event
        # loop esta detenido y asyncpg no recicla nada; ademas el timer solo se
        # evalua al hacer release(), no en background. Pero sigue ayudando en
        # escenarios de conn idle cortada por intermediarios de red entre
        # requests cercanos. El mecanismo real anti-resume es el pre-flight
        # SELECT 1 en _acquire_healthy_conn(). Ver get_conn() / transaction().
        max_inactive_connection_lifetime=60,
    )
    logger.info(
        "Pool asyncpg inicializado (min=%d max=%d max_inactive_lifetime=60s)",
        ASYNCPG_MIN_SIZE,
        ASYNCPG_MAX_SIZE,
    )
    return pool


async def close_pool() -> None:
    """Cerrar el pool al apagar la aplicación."""
    global pool
    if pool is not None:
        await pool.close()
        pool = None


def get_pool() -> asyncpg.Pool:
    if pool is None:
        raise RuntimeError("Pool asyncpg no inicializado. Verificar lifespan en main.py.")
    return pool


# ============================================================================
# Contexto multi-tenant: adquiere conexión, setea search_path + GUC auditoría
# ============================================================================
@asynccontextmanager
async def get_conn(
    *,
    schema_name: str,
    user_id: Optional[str] = None,
    auth_source: Optional[str] = None,
):
    """
    Adquiere una conexión del pool con tenant context SEGURO.
    Reemplaza a get_db_connection + get_db_cursor.

    SEGURIDAD MULTI-TENANT (CRÍTICO):
    Toda adquisición abre SIEMPRE una transacción y usa SET LOCAL search_path
    + set_config(..., true). El scope transaccional garantiza que al COMMIT/ROLLBACK
    (incluso por cancelación de corrutina), el search_path y los GUC vuelven al
    default del pool. La conexión NUNCA regresa al pool contaminada con el tenant anterior.

    NUNCA usar SET search_path sin LOCAL ni set_config(..., false) en asyncpg con pool.

    MejoraArranque FIX B (resiliencia post-resume Fly.io):
    El acquire+pre-flight se delega a _acquire_healthy_conn() con retry. El yield
    de abajo es UNICO y NO retryable: si la conn muere DESPUES del yield (durante
    una query del caller o en el COMMIT), el error original se propaga limpio.
    Reintentar una TX parcial no tiene sentido y ademas rompe el contextmanager
    con RuntimeError("generator didn't stop after athrow()").
    """
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
        # Al cerrar conn.transaction(): COMMIT (o ROLLBACK si hubo excepción).
        # search_path y GUC se revierten automáticamente. Conexión limpia al pool.
    finally:
        try:
            await get_pool().release(conn)
        except Exception as release_exc:
            # No enmascarar la excepcion original del bloque: solo logear y seguir.
            logger.warning("get_conn: error al liberar conn al pool — %s", release_exc)


async def with_tenant(
    conn: asyncpg.Connection,
    *,
    schema_name: str,
    user_id: Optional[str] = None,
    auth_source: Optional[str] = None,
) -> None:
    """
    Establece tenant context en una conexión que ya tiene una transacción abierta.
    Usar cuando el caller pasa conn explícitamente (ej: servicios de cases).
    SIEMPRE llamar dentro de async with conn.transaction().
    """
    validated = validate_schema_name(schema_name)
    await conn.execute(f'SET LOCAL search_path TO "{validated}", public')
    if user_id is not None:
        await conn.execute("SELECT set_config('app.user_id', $1, true)", str(user_id))
    if auth_source is not None:
        await conn.execute("SELECT set_config('app.auth_source', $1, true)", auth_source)


# ============================================================================
# Helpers públicos — reemplazan execute_query / execute_update / execute_single_update
# ============================================================================
async def fetch_all(sql: str, *params, schema_name: str) -> list[asyncpg.Record]:
    """SELECT que devuelve múltiples filas. Reemplaza execute_query(fetch=True)."""
    async with get_conn(schema_name=schema_name) as conn:
        return await conn.fetch(sql, *params)


async def fetch_one(sql: str, *params, schema_name: str) -> Optional[asyncpg.Record]:
    """SELECT que devuelve una fila o None. Reemplaza execute_query(fetch_one=True)."""
    async with get_conn(schema_name=schema_name) as conn:
        return await conn.fetchrow(sql, *params)


async def fetch_val(sql: str, *params, schema_name: str, column: int = 0) -> Any:
    """Escalar de la primera columna. Reemplaza fetchone()[0] y INSERT ... RETURNING id."""
    async with get_conn(schema_name=schema_name) as conn:
        return await conn.fetchval(sql, *params, column=column)


async def execute(
    sql: str,
    *params,
    schema_name: str,
    user_id: Optional[str] = None,
    auth_source: Optional[str] = None,
) -> str:
    """INSERT/UPDATE/DELETE sin RETURNING. Devuelve el status (ej 'UPDATE 1').
    Reemplaza execute_update y execute_single_update(returning=False)."""
    async with get_conn(schema_name=schema_name, user_id=user_id, auth_source=auth_source) as conn:
        return await conn.execute(sql, *params)


# ============================================================================
# Transacción atómica multi-statement
# ============================================================================
@asynccontextmanager
async def transaction(
    *,
    schema_name: str,
    user_id: Optional[str] = None,
    auth_source: Optional[str] = None,
):
    """
    Reemplaza execute_transaction. Uso:
        async with transaction(schema_name=s, user_id=u, auth_source="jwt") as conn:
            await conn.execute("INSERT ...", a, b)
            doc_id = await conn.fetchval("INSERT ... RETURNING id", c)
        # COMMIT automático al salir sin excepción; ROLLBACK si excepción.

    MejoraArranque FIX B: comparte _acquire_healthy_conn() con get_conn. El yield
    es unico, no retryable. Si la conn muere a mitad de TX o en el COMMIT, el
    error original se propaga (sin enmascarar con RuntimeError, sin reintentar
    una TX parcial).
    """
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


# ============================================================================
# Funciones de validación y utilitarios (ahora async)
# ============================================================================
async def test_connection() -> bool:
    """Probar conexión a la base de datos."""
    try:
        result = await fetch_val("SELECT 1", schema_name="public")
        return result == 1
    except Exception as e:
        logger.error(f"Error probando conexión: {e}")
        return False


async def check_user_exists(user_id: str, *, schema_name: str) -> bool:
    """Verifica si un usuario existe."""
    row = await fetch_one(
        "SELECT id FROM users WHERE id = $1 LIMIT 1",
        user_id,
        schema_name=schema_name,
    )
    return row is not None


async def check_document_exists(document_id: str, *, schema_name: str) -> bool:
    """Verifica si un documento existe."""
    row = await fetch_one(
        "SELECT id FROM document_draft WHERE id = $1 LIMIT 1",
        document_id,
        schema_name=schema_name,
    )
    return row is not None


async def check_case_exists(case_id: str, *, schema_name: str) -> bool:
    """Verifica si un expediente existe."""
    row = await fetch_one(
        "SELECT id FROM cases WHERE id = $1 LIMIT 1",
        case_id,
        schema_name=schema_name,
    )
    return row is not None


async def get_document_basic_info(document_id: str, *, schema_name: str) -> Optional[asyncpg.Record]:
    """Información básica de un documento."""
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
    """Genera template de número de expediente (sin BD, no cambia)."""
    from datetime import datetime
    if year is None:
        year = datetime.now().year
    return f"{EXPEDIENTE_PREFIX}-{year}-{{sequence:06d}}-{municipality_acronym}-{department_acronym}"
