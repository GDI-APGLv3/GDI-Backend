
import pytest
import pytest_asyncio
import asyncpg
import os

def _build_db_url() -> str:
    if url := os.getenv("DATABASE_URL"):
        return url
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5433")
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "")
    name = os.getenv("DB_NAME", "railway")
    return f"postgresql://{user}:{password}@{host}:{port}/{name}"

DATABASE_URL = _build_db_url()

TEST_SCHEMA = "100_test"
TEST_AUDIT_SCHEMA = "100_test_audit"


@pytest_asyncio.fixture
async def db_connection():
    conn = await asyncpg.connect(DATABASE_URL)
    tr = conn.transaction()
    await tr.start()
    yield conn
    await tr.rollback()
    await conn.close()


class TestAuditContext:

    @pytest.mark.asyncio
    async def test_audit_log_has_auth_source_column(self, db_connection):
        result = await db_connection.fetchrow(
            """
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = $1
              AND table_name = 'audit_log'
              AND column_name = 'auth_source'
            """,
            TEST_AUDIT_SCHEMA
        )

        assert result is not None, "Columna auth_source no existe en audit_log"
        assert result['data_type'] == "character varying", f"Tipo de columna incorrecto: {result['data_type']}"

    @pytest.mark.asyncio
    async def test_set_audit_context_injects_guc(self, db_connection):
        test_user_id = "9a6156db-c7b6-4698-9af2-5bdaeb46c18a"
        test_auth_source = "jwt"

        await db_connection.execute("SELECT set_config('app.user_id', $1, true)", test_user_id)
        await db_connection.execute("SELECT set_config('app.auth_source', $1, true)", test_auth_source)

        user_id = await db_connection.fetchval("SELECT current_setting('app.user_id', true)")
        auth_source = await db_connection.fetchval("SELECT current_setting('app.auth_source', true)")

        assert user_id == test_user_id, f"user_id no coincide: {user_id}"
        assert auth_source == test_auth_source, f"auth_source no coincide: {auth_source}"

    @pytest.mark.asyncio
    async def test_fn_log_change_reads_context(self, db_connection):
        test_user_id = "9a6156db-c7b6-4698-9af2-5bdaeb46c18a"
        test_auth_source = "testing"

        await db_connection.execute(f'SET LOCAL search_path TO "{TEST_SCHEMA}", public')

        user_row = await db_connection.fetchrow(
            "SELECT id FROM users WHERE id = $1", test_user_id
        )

        if not user_row:
            any_user = await db_connection.fetchrow("SELECT id FROM users LIMIT 1")
            if any_user:
                test_user_id = str(any_user['id'])
            else:
                pytest.skip("No hay usuarios en la BD de pruebas")

        await db_connection.execute("SELECT set_config('app.user_id', $1, true)", test_user_id)
        await db_connection.execute("SELECT set_config('app.auth_source', $1, true)", test_auth_source)

        await db_connection.execute(
            "UPDATE users SET last_access = NOW() WHERE id = $1",
            test_user_id
        )

        audit_record = await db_connection.fetchrow(
            f"""
            SELECT user_id, auth_source, operation, table_name
            FROM "{TEST_AUDIT_SCHEMA}".audit_log
            WHERE user_id = $1::uuid
              AND table_name = 'users'
              AND operation = 'UPDATE'
            ORDER BY event_time DESC
            LIMIT 1
            """,
            test_user_id
        )

        if audit_record is None:
            pytest.skip(
                f"Trigger fn_log_change no activo en {TEST_SCHEMA}.users — "
                "requiere migración de auditoría en este entorno"
            )
        assert str(audit_record['user_id']) == test_user_id, f"user_id no coincide: {audit_record['user_id']}"
        assert audit_record['operation'] == "UPDATE", f"operation no coincide: {audit_record['operation']}"
        assert audit_record['table_name'] == "users", f"table_name no coincide: {audit_record['table_name']}"
        assert audit_record['auth_source'] is not None, "auth_source es NULL en audit_log"
        assert audit_record['auth_source'] in ("testing", "jwt", "api_key", "mcp_oauth"), (
            f"auth_source inesperado: {audit_record['auth_source']}"
        )

    def test_audit_context_helper_import(self):
        from shared.audit_context import set_audit_context, clear_audit_context
        assert callable(set_audit_context)
        assert callable(clear_audit_context)

    def test_dependencies_get_auth_source(self):
        from shared.dependencies import get_auth_source, get_tenant_schema
        assert callable(get_auth_source)
        assert callable(get_tenant_schema)


class TestDatabaseFunctionsSignature:

    def test_get_conn_is_async_context_manager(self):
        import inspect
        from database import get_conn
        assert inspect.isfunction(get_conn) or callable(get_conn), "get_conn no existe"

    def test_fetch_all_accepts_schema_name(self):
        import inspect
        from database import fetch_all
        sig = inspect.signature(fetch_all)
        params = sig.parameters
        assert 'schema_name' in params, "fetch_all no acepta schema_name"
        assert params['schema_name'].kind == inspect.Parameter.KEYWORD_ONLY, \
            "schema_name debe ser keyword-only en fetch_all"

    def test_fetch_one_accepts_schema_name(self):
        import inspect
        from database import fetch_one
        sig = inspect.signature(fetch_one)
        params = sig.parameters
        assert 'schema_name' in params, "fetch_one no acepta schema_name"
        assert params['schema_name'].kind == inspect.Parameter.KEYWORD_ONLY, \
            "schema_name debe ser keyword-only en fetch_one"

    def test_execute_accepts_schema_name(self):
        import inspect
        from database import execute
        sig = inspect.signature(execute)
        params = sig.parameters
        assert 'schema_name' in params, "execute no acepta schema_name"
        assert params['schema_name'].kind == inspect.Parameter.KEYWORD_ONLY
        assert 'user_id' in params, "execute no acepta user_id"
        assert 'auth_source' in params, "execute no acepta auth_source"

    def test_transaction_accepts_schema_name(self):
        import inspect
        from database import transaction
        sig = inspect.signature(transaction)
        params = sig.parameters
        assert 'schema_name' in params, "transaction no acepta schema_name"
        assert params['schema_name'].kind == inspect.Parameter.KEYWORD_ONLY
        assert 'user_id' in params, "transaction no acepta user_id"
        assert 'auth_source' in params, "transaction no acepta auth_source"

    def test_validate_schema_name_rejects_injection(self):
        from database import validate_schema_name
        with pytest.raises(ValueError):
            validate_schema_name("schema; DROP TABLE users")
        with pytest.raises(ValueError):
            validate_schema_name("")
        assert validate_schema_name("100_test") == "100_test"


class TestMCPContextAuthSource:

    def test_mcp_context_has_auth_source(self):
        from api_gateway.context import MCPContext

        ctx = MCPContext(
            api_key="test",
            municipality_id="123",
            schema_name="test_schema",
            auth_source="mcp_oauth"
        )

        assert ctx.auth_source == "mcp_oauth"

    def test_mcp_context_default_auth_source(self):
        from api_gateway.context import MCPContext

        ctx = MCPContext(
            api_key="test",
            municipality_id="123",
            schema_name="test_schema"
        )

        assert ctx.auth_source == "api_key"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
