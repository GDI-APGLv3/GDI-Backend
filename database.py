import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import SimpleConnectionPool
import os
import sys
import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Optional, Dict, Any
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
load_dotenv()

# ============================================================================
# MULTI-TENANT: Schema se pasa explícitamente via request.state.schema_name
# ============================================================================
# El TenantMiddleware setea request.state.schema_name en cada request.
# Las funciones de base de datos reciben schema_name como parámetro explícito.
# Este enfoque es más robusto que ContextVar para contextos async/sync mixtos.

# ============================================================================
# PGBOUNCER TRANSACTION MODE: Reutilización de conexiones en llamadas anidadas
# ============================================================================
# Para soportar 300-500 conexiones concurrentes usamos PgBouncer en transaction mode.
# ContextVar permite reutilizar la misma conexión en llamadas anidadas (ej: service → service).
# SET LOCAL en lugar de SET garantiza que search_path se resetea al final de la transacción.
_current_connection: ContextVar[Optional[Any]] = ContextVar('db_conn', default=None)

# Configuración básica para conectar a PostgreSQL
# Lee credenciales desde variables de entorno para seguridad

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "GDI-MVP")

# Detectar si estamos usando PgBouncer (puerto 6432) o PostgreSQL directo (puerto 5432)
# PgBouncer no soporta parámetros "options" en la URL, usa SERVER_RESET_QUERY en su lugar
USE_PGBOUNCER = DB_PORT == "6432"

# Modo transaccional de PgBouncer (requiere SET LOCAL en lugar de SET)
# CRITICAL: En transaction mode, SET persiste entre requests → riesgo de seguridad multi-tenant
# SET LOCAL se resetea automáticamente al final de cada transacción
PGBOUNCER_TRANSACTION_MODE = os.getenv("PGBOUNCER_TRANSACTION_MODE", "false").lower() == "true"

if USE_PGBOUNCER:
    # PgBouncer: Sin options (usa PGBOUNCER_SERVER_RESET_QUERY en Railway)
    DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    print("[INFO] Usando PgBouncer - search_path configurado via SERVER_RESET_QUERY", file=sys.stderr)
else:
    # PostgreSQL directo: Con search_path en URL para desarrollo local
    DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?options=-c%20search_path%3Dpublic"
    print("[INFO] Usando PostgreSQL directo - search_path en URL", file=sys.stderr)

# Configuración de Auth0 (desde variables de entorno)
AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN", "gdilatam.us.auth0.com")
AUTH0_AUDIENCE = os.getenv("AUTH0_AUDIENCE", "https://gdilatam.us.auth0.com/api/v2/")
AUTH0_CLIENT_ID = os.getenv("AUTH0_CLIENT_ID", "")
AUTH0_CLIENT_SECRET = os.getenv("AUTH0_CLIENT_SECRET")  # REQUERIDO en producción
AUTH0_ALGORITHMS = ["RS256"]

# Modo de testing (permite usar header X-User-ID en lugar de Auth0)
# SECURITY: Default es false para evitar bypass de autenticación en producción
# En desarrollo local, configurar TESTING_MODE=true en .env
TESTING_MODE = os.getenv("TESTING_MODE", "false").lower() == "true"

if TESTING_MODE:
    # Bloquear TESTING_MODE en ambientes de producción de Railway
    railway_env = os.getenv("RAILWAY_ENVIRONMENT_NAME", "").lower()
    if "production" in railway_env or "prod" in railway_env:
        print("[SECURITY] TESTING_MODE=true detectado en producción - DESACTIVANDO", file=sys.stderr)
        TESTING_MODE = False
    else:
        print("[WARNING] TESTING_MODE está habilitado - Auth0 bypass activo. NO usar en producción.", file=sys.stderr)

# Configuración del sistema de expedientes
MUNICIPIO_PRINCIPAL_ID = "550e8400-e29b-41d4-a716-446655440000"  # San Miguel
MUNICIPIO_ACRONYM = "SMG"

# Configuración de numeración de expedientes
EXPEDIENTE_PREFIX = "EE"  # Prefijo para expedientes

# Configuración de estados
CASE_STATUS_ACTIVE = "active"
CASE_STATUS_INACTIVE = "inactive" 
CASE_STATUS_ARCHIVED = "archived"

# Tipos de movimientos
MOVEMENT_TYPES = {
    "CREATION": "creation",
    "TRANSFER": "transfer", 
    "ASSIGNMENT": "assignment",
    "STATUS_CHANGE": "status_change"
}

# Configuración de paginación
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

# Pool de conexiones (inicializada más tarde)
connection_pool: Optional[SimpleConnectionPool] = None

def init_db_pool():
    """Inicializar pool de conexiones a la base de datos"""
    global connection_pool
    try:
        connection_pool = SimpleConnectionPool(
            minconn=5,   # Aumentado de 2 a 5 para mejor rendimiento
            maxconn=50,  # Aumentado de 20 a 50 (seguro para Railway Pro con 100 max)
            dsn=DATABASE_URL,
            cursor_factory=RealDictCursor
        )
        print("[OK] Pool de conexiones PostgreSQL inicializado correctamente")
        return True
    except Exception as e:
        print(f"[ERROR] Error inicializando pool de conexiones: {e}")
        return False


def validate_schema_name(schema_name: str) -> str:
    """
    Valida que schema_name sea seguro para usar en SQL.

    SECURITY: Previene SQL injection en SET search_path.
    Solo permite: letras, números, guión bajo.
    Ejemplos válidos: "100_test", "public", "municipio_abc"

    Args:
        schema_name: Nombre del schema a validar

    Returns:
        El schema_name validado (sin cambios si es válido)

    Raises:
        ValueError: Si el schema_name contiene caracteres no permitidos
    """
    if not schema_name or not schema_name.strip():
        raise ValueError(
            "schema_name es REQUERIDO. "
            "Obtener de request.state.schema_name o pasar 'public' explícitamente."
        )

    # SECURITY: Solo permitir caracteres seguros para nombres de schema PostgreSQL
    # Patrón: letras, números, guión bajo (típico: "100_san_miguel", "public")
    if not re.match(r'^[a-zA-Z0-9_]+$', schema_name):
        raise ValueError(
            f"schema_name inválido: '{schema_name}'. "
            "Solo se permiten letras, números y guión bajo."
        )

    return schema_name


@contextmanager
def get_db_connection(schema_name: str):
    """
    Context manager para obtener conexión de la pool con schema multi-tenant.

    IMPORTANTE: schema_name es REQUERIDO (sin default).
    Esto garantiza que TODAS las operaciones tienen SET LOCAL.

    Cambios para PgBouncer transaction mode:
    - Reutiliza conexión activa en llamadas anidadas (via ContextVar)
    - Usa SET LOCAL en lugar de SET para search_path (se resetea automáticamente)
    - Reset explícito de search_path antes de putconn() para compatibilidad

    Args:
        schema_name: Schema del tenant (REQUERIDO). Ejemplos:
                     - "100_san_miguel" para tenant específico
                     - "public" para operaciones de sistema/DDL
                     Se obtiene de request.state.schema_name en endpoints.

    Raises:
        ValueError: Si schema_name está vacío o es None

    Yields:
        Conexión de PostgreSQL con search_path configurado al schema.
    """
    global connection_pool

    # SECURITY: Validar schema_name contra SQL injection
    # Esto también valida que no esté vacío
    validated_schema = validate_schema_name(schema_name)

    # Si ya hay una conexión activa en este contexto (llamadas anidadas), reutilizarla
    existing_conn = _current_connection.get()
    if existing_conn is not None:
        print(f"[DB_CONNECTION] Reutilizando conexión existente (llamada anidada)")
        # IMPORTANTE: NO hacer yield directamente, necesitamos configurar search_path
        # porque podría ser un schema diferente al de la llamada padre
        set_command = "SET LOCAL" if PGBOUNCER_TRANSACTION_MODE else "SET"
        with existing_conn.cursor() as setup_cursor:
            setup_cursor.execute(f'{set_command} search_path TO "{validated_schema}", public')
        print(f"[DB_CONNECTION] {set_command} search_path TO \"{validated_schema}\", public (nested)")
        yield existing_conn
        return

    # Nueva conexión
    if connection_pool is None:
        init_db_pool()

    if connection_pool is None:
        raise psycopg2.OperationalError("No hay pool de conexiones disponible. La base de datos no está accesible.")

    connection = None
    connection_is_bad = False
    try:
        connection = connection_pool.getconn()

        # Verificar que la conexión esté viva antes de usarla
        try:
            with connection.cursor() as test_cursor:
                test_cursor.execute("SELECT 1")
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            # Conexión cerrada por PgBouncer o timeout - marcar como mala
            connection_is_bad = True
            connection_pool.putconn(connection, close=True)
            # Obtener una nueva conexión
            connection = connection_pool.getconn()

        # Configurar search_path
        # CRITICAL: SET LOCAL en transaction mode para evitar bleed entre requests
        # validated_schema ya pasó validación contra SQL injection
        set_command = "SET LOCAL" if PGBOUNCER_TRANSACTION_MODE else "SET"
        with connection.cursor() as setup_cursor:
            setup_cursor.execute(f'{set_command} search_path TO "{validated_schema}", public')
        print(f"[DB_CONNECTION] {set_command} search_path TO \"{validated_schema}\", public")

        # Registrar conexión en ContextVar para reutilización en llamadas anidadas
        token = _current_connection.set(connection)
        try:
            yield connection
            # Commit exitoso (fuera del except)
            if not connection.closed:
                try:
                    connection.commit()
                except (psycopg2.OperationalError, psycopg2.InterfaceError):
                    connection_is_bad = True
        finally:
            # Limpiar ContextVar
            _current_connection.reset(token)

    except (psycopg2.OperationalError, psycopg2.InterfaceError) as e:
        # Conexión cerrada - marcarla para que no vuelva al pool
        connection_is_bad = True
        raise e
    except Exception as e:
        if connection:
            try:
                connection.rollback()
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                # Conexión ya cerrada, no se puede hacer rollback
                connection_is_bad = True
        raise e
    finally:
        if connection and not connection.closed:
            # CRITICAL: Reset search_path antes de devolver al pool (defense in depth)
            # SET LOCAL se resetea automáticamente, pero hacemos reset explícito por seguridad
            try:
                with connection.cursor() as reset_cursor:
                    reset_cursor.execute("RESET search_path")
                print(f"[DB_CONNECTION] RESET search_path (antes de putconn)")
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                # Conexión cerrada, marcar como mala
                connection_is_bad = True

        if connection:
            connection_pool.putconn(connection, close=connection_is_bad)

@contextmanager
def get_db_cursor(
    *,
    commit: bool = False,
    schema_name: str,
    user_id: Optional[str] = None,
    auth_source: Optional[str] = None
):
    """
    Context manager para obtener cursor con auto-commit opcional.

    Args:
        commit: Si True, hace commit al finalizar
        schema_name: Schema a usar (obligatorio para multi-tenant).
                     Se obtiene de request.state.schema_name en el endpoint.
        user_id: UUID del usuario que realiza la acción (para auditoría).
                 Si se pasa, se inyecta como GUC app.user_id para los triggers.
        auth_source: Origen de la autenticación (jwt, api_key, mcp_oauth, testing, system).
                     Si se pasa, se inyecta como GUC app.auth_source para los triggers.

    Note:
        El schema_name debe pasarse explícitamente desde el endpoint/service.
        Este enfoque es más robusto que ContextVar para contextos async/sync mixtos.

        Para auditoría, pasar user_id y auth_source:
        ```
        with get_db_cursor(
            commit=True,
            schema_name=schema_name,
            user_id=creator_id,
            auth_source="jwt"
        ) as cursor:
            cursor.execute("INSERT INTO ...")
        ```
    """
    # get_db_connection ya maneja el SET search_path
    with get_db_connection(schema_name) as conn:
        cursor = conn.cursor()
        try:
            # Inyectar contexto de auditoría si se proporciona
            if user_id or auth_source:
                from shared.audit_context import set_audit_context
                set_audit_context(cursor, user_id=user_id, auth_source=auth_source)

            yield cursor
            if commit:
                conn.commit()
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            # Conexión cerrada, no intentar rollback
            raise
        except Exception as e:
            try:
                conn.rollback()
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                # Conexión ya cerrada, ignorar error de rollback
                pass
            raise e
        finally:
            try:
                cursor.close()
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                # Conexión cerrada, cursor ya no existe
                pass

def execute_query(query: str, params: tuple = None, fetch: bool = True, fetch_one: bool = False, retry_count: int = 2, *, schema_name: str) -> Optional[list]:
    """
    Ejecutar consulta SQL y retornar resultados con retry automático.

    Args:
        query: Query SQL
        params: Parámetros
        fetch: Si True, hace fetch (ignorado si fetch_one=True)
        fetch_one: Si True, retorna solo 1 registro (dict)
        retry_count: Intentos en caso de error de conexión
        schema_name: Schema del tenant (obligatorio para multi-tenant).
                     Debe pasarse desde request.state.schema_name.

    Returns:
        dict (si fetch_one=True) o list[dict] (si fetch=True) o None
    """
    last_error = None

    for attempt in range(retry_count):
        try:
            with get_db_cursor(schema_name=schema_name) as cursor:
                cursor.execute(query, params)

                if fetch_one:
                    return cursor.fetchone()
                elif fetch:
                    return cursor.fetchall()
                else:
                    return None
        except psycopg2.OperationalError as e:
            last_error = e
            error_msg = str(e).lower()
            # Retry solo si es un error de conexión cerrada o perdida
            if "closed" in error_msg or "lost" in error_msg or "terminated" in error_msg:
                print(f"[WARN] Intento {attempt + 1}/{retry_count} falló (conexión cerrada), reintentando...")
                # NOTA: No cerrar todo el pool (closeall) porque afectaría otros requests en curso.
                # La conexión fallida ya fue cerrada con putconn(close=True) en get_db_connection.
                # El pool creará una nueva conexión automáticamente en el siguiente getconn().
                continue
            else:
                # Si no es error de conexión, no reintentar
                raise
        except Exception as e:
            print(f"[ERROR] Error ejecutando consulta: {e}")
            raise

    # Si llegamos aquí, todos los intentos fallaron
    print(f"[ERROR] Error ejecutando consulta después de {retry_count} intentos: {last_error}")
    raise last_error

def execute_update(
    query: str,
    params: tuple = None,
    *,
    schema_name: str,
    user_id: Optional[str] = None,
    auth_source: Optional[str] = None
) -> bool:
    """Ejecutar consulta de actualización (INSERT, UPDATE, DELETE)

    Args:
        query: Query SQL
        params: Parámetros
        schema_name: Schema del tenant (multi-tenant)
        user_id: UUID del usuario que realiza la acción (para auditoría)
        auth_source: Origen de la autenticación (jwt, api_key, etc.)
    """
    try:
        with get_db_cursor(
            commit=True,
            schema_name=schema_name,
            user_id=user_id,
            auth_source=auth_source
        ) as cursor:
            cursor.execute(query, params)
            return True
    except Exception as e:
        print(f"[ERROR] Error ejecutando actualización: {e}")
        raise

@contextmanager
def execute_transaction(
    *,
    schema_name: str,
    user_id: Optional[str] = None,
    auth_source: Optional[str] = None
):
    """
    Context manager para ejecutar múltiples operaciones en una transacción atómica.
    Usa connection pool.

    Args:
        schema_name: Schema del tenant (multi-tenant). Si se especifica, ejecuta SET search_path.
        user_id: UUID del usuario que realiza la acción (para auditoría).
        auth_source: Origen de la autenticación (jwt, api_key, mcp_oauth, testing, system).

    Uso:
        with execute_transaction(
            schema_name="100_test",
            user_id=creator_id,
            auth_source="jwt"
        ) as (conn, cursor):
            cursor.execute("INSERT INTO ...", params1)
            cursor.execute("UPDATE ...", params2)
            # Auto-commit si no hay excepciones
            # Auto-rollback en caso de error

    Yields:
        tuple: (connection, cursor)
    """
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            try:
                # Inyectar contexto de auditoría si se proporciona
                if user_id or auth_source:
                    from shared.audit_context import set_audit_context
                    set_audit_context(cursor, user_id=user_id, auth_source=auth_source)

                yield (conn, cursor)
                conn.commit()
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                # Conexión cerrada, no intentar rollback
                raise
            except Exception as e:
                try:
                    conn.rollback()
                except (psycopg2.OperationalError, psycopg2.InterfaceError):
                    # Conexión ya cerrada
                    pass
                raise e

def execute_single_update(
    query: str,
    params: tuple = None,
    returning: bool = False,
    *,
    schema_name: str,
    user_id: Optional[str] = None,
    auth_source: Optional[str] = None
):
    """
    Ejecuta una operación de actualización única (INSERT/UPDATE/DELETE).
    Usa connection pool.

    Args:
        query: Query SQL a ejecutar
        params: Parámetros para la query
        returning: Si True, retorna el resultado de la cláusula RETURNING
        schema_name: Schema del tenant (multi-tenant)
        user_id: UUID del usuario que realiza la acción (para auditoría)
        auth_source: Origen de la autenticación (jwt, api_key, etc.)

    Returns:
        dict con status y rows_affected, o resultado si returning=True
    """
    try:
        with get_db_cursor(
            commit=True,
            schema_name=schema_name,
            user_id=user_id,
            auth_source=auth_source
        ) as cursor:
            cursor.execute(query, params)
            rows_affected = cursor.rowcount

            if returning:
                result = cursor.fetchone()
                return {
                    "status": "success",
                    "rows_affected": rows_affected,
                    "result": result
                }

            return {
                "status": "success",
                "rows_affected": rows_affected
            }
    except Exception as e:
        print(f"[ERROR] Error en execute_single_update: {e}")
        raise

def test_connection() -> bool:
    """Probar conexión a la base de datos"""
    try:
        result = execute_query("SELECT 1 as test", schema_name="public")
        return result is not None and len(result) > 0
    except Exception as e:
        print(f"[ERROR] Error probando conexion: {e}")
        return False

# Funciones específicas para expedientes
def get_case_number_format(department_acronym: str, municipality_acronym: str, year: int = None) -> str:
    """
    Generar formato de número de expediente.

    Args:
        department_acronym: Acrónimo del departamento
        municipality_acronym: Acrónimo del municipio (requerido)
        year: Año para el número (opcional, usa año actual por defecto)

    Returns:
        Template string con formato: EE-{año}-{secuencia}-{municipio}-{dept}
    """
    from datetime import datetime
    if year is None:
        year = datetime.now().year
    return f"{EXPEDIENTE_PREFIX}-{year}-{{sequence:06d}}-{municipality_acronym}-{department_acronym}"

def get_next_case_sequence(year: int = None, *, schema_name: str) -> int:
    """
    Obtener siguiente número secuencial GLOBAL para expedientes del año.
    Usa Advisory Lock para prevenir race conditions.

    Args:
        year: Año para buscar secuencia (opcional, usa año actual por defecto)
        schema_name: Schema del tenant (multi-tenant, compatible con PgBouncer transaction mode)

    Returns:
        Siguiente número secuencial (entero)

    Note:
        - La secuencia es GLOBAL por año (no por departamento)
        - Thread-safe usando pg_advisory_xact_lock
        - El lock se libera automáticamente al finalizar la transacción
    """
    from datetime import datetime
    if year is None:
        year = datetime.now().year

    # Usar Advisory Lock para serializar el acceso
    # El ID 999999 es arbitrario, solo para identificar este lock
    # Se libera automáticamente al commit/rollback de la transacción
    with get_db_connection(schema_name) as conn:
        with conn.cursor() as cursor:
            try:
                # Adquirir lock exclusivo para numeración de expedientes
                cursor.execute("SELECT pg_advisory_xact_lock(999999)")

                # Ahora buscar el máximo de forma segura (nadie más puede ejecutar esto simultáneamente)
                query = """
                    SELECT COALESCE(MAX(
                        CAST(SUBSTRING(case_number FROM '\\d{4}-(\\d+)-') AS INTEGER)
                    ), 0) + 1 as next_sequence
                    FROM cases
                    WHERE EXTRACT(YEAR FROM created_at) = %s
                """
                cursor.execute(query, (year,))
                result = cursor.fetchone()

                conn.commit()  # Commit libera el advisory lock automáticamente

                return result['next_sequence'] if result else 1
            except Exception as e:
                conn.rollback()
                print(f"[ERROR] Error obteniendo secuencia de expediente: {e}")
                raise

# Funciones de validación
def check_user_exists(user_id: str, *, schema_name: str) -> bool:
    """
    Verifica si un usuario existe en la base de datos.

    Args:
        user_id: UUID del usuario
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        True si existe, False en caso contrario
    """
    result = execute_query(
        "SELECT id FROM users WHERE id = %s LIMIT 1",
        (user_id,),
        fetch=True,
        schema_name=schema_name
    )
    return result is not None and len(result) > 0

def check_document_exists(document_id: str, *, schema_name: str) -> bool:
    """
    Verifica si un documento existe en la base de datos.

    Args:
        document_id: UUID del documento
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        True si existe, False en caso contrario
    """
    result = execute_query(
        "SELECT id FROM document_draft WHERE id = %s LIMIT 1",
        (document_id,),
        fetch=True,
        schema_name=schema_name
    )
    return result is not None and len(result) > 0

def get_user_basic_info(user_id: str, *, schema_name: str):
    """
    Obtiene información básica de un usuario.

    Args:
        user_id: UUID del usuario
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Dict con información básica del usuario o None si no existe
    """
    return execute_query(
        """
        SELECT
            user_id,
            full_name,
            profile_picture_id
        FROM users
        WHERE user_id = %s
        """,
        (user_id,),
        fetch_one=True,
        schema_name=schema_name
    )

def get_document_basic_info(document_id: str, *, schema_name: str):
    """
    Obtiene información básica de un documento.

    Args:
        document_id: UUID del documento
        schema_name: Schema del tenant (multi-tenant)

    Returns:
        Dict con información básica del documento o None si no existe
    """
    return execute_query(
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
        WHERE dd.id = %s
        """,
        (document_id,),
        fetch_one=True,
        schema_name=schema_name
    )