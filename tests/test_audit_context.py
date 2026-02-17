"""
Tests para la funcionalidad de contexto de auditoría (auth_source).

Verifica que:
1. set_audit_context inyecta correctamente las variables GUC
2. Los triggers de auditoría leen las variables
3. Las funciones de BD aceptan los nuevos parámetros
"""

import pytest
import psycopg2
import os
from datetime import datetime

# Configuración de la BD de pruebas (caboose)
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:password@localhost:5432/railway"
)

TEST_SCHEMA = "100_test"
TEST_AUDIT_SCHEMA = "100_test_audit"


@pytest.fixture
def db_connection():
    """Conexión a la BD de pruebas."""
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    yield conn
    conn.rollback()
    conn.close()


class TestAuditContext:
    """Tests para el contexto de auditoría."""

    def test_audit_log_has_auth_source_column(self, db_connection):
        """Verifica que la tabla audit_log tiene la columna auth_source."""
        with db_connection.cursor() as cursor:
            cursor.execute("""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = %s
                AND table_name = 'audit_log'
                AND column_name = 'auth_source'
            """, (TEST_AUDIT_SCHEMA,))
            result = cursor.fetchone()

        assert result is not None, "Columna auth_source no existe en audit_log"
        assert result[1] == "character varying", f"Tipo de columna incorrecto: {result[1]}"

    def test_set_audit_context_injects_guc(self, db_connection):
        """Verifica que set_config inyecta las variables GUC correctamente."""
        test_user_id = "9a6156db-c7b6-4698-9af2-5bdaeb46c18a"
        test_auth_source = "jwt"

        with db_connection.cursor() as cursor:
            # Setear contexto
            cursor.execute("SELECT set_config('app.user_id', %s, true)", (test_user_id,))
            cursor.execute("SELECT set_config('app.auth_source', %s, true)", (test_auth_source,))

            # Leer contexto
            cursor.execute("SELECT current_setting('app.user_id', true)")
            user_id = cursor.fetchone()[0]

            cursor.execute("SELECT current_setting('app.auth_source', true)")
            auth_source = cursor.fetchone()[0]

        assert user_id == test_user_id, f"user_id no coincide: {user_id}"
        assert auth_source == test_auth_source, f"auth_source no coincide: {auth_source}"

    def test_fn_log_change_reads_context(self, db_connection):
        """Verifica que fn_log_change lee el contexto GUC."""
        test_user_id = "9a6156db-c7b6-4698-9af2-5bdaeb46c18a"
        test_auth_source = "testing"

        with db_connection.cursor() as cursor:
            # Configurar search_path
            cursor.execute(f'SET search_path TO "{TEST_SCHEMA}", public')

            # Primero verificar si el usuario existe
            cursor.execute("SELECT id FROM users WHERE id = %s", (test_user_id,))
            user_exists = cursor.fetchone()

            if not user_exists:
                # Buscar cualquier usuario existente
                cursor.execute("SELECT id FROM users LIMIT 1")
                any_user = cursor.fetchone()
                if any_user:
                    test_user_id = str(any_user[0])
                else:
                    pytest.skip("No hay usuarios en la BD de pruebas")

            # Inyectar contexto de auditoría
            cursor.execute("SELECT set_config('app.user_id', %s, true)", (test_user_id,))
            cursor.execute("SELECT set_config('app.auth_source', %s, true)", (test_auth_source,))

            # Hacer un UPDATE en una tabla auditada para disparar el trigger
            cursor.execute("""
                UPDATE users
                SET last_access = NOW()
                WHERE id = %s
            """, (test_user_id,))

            # Verificar que se creó un registro de auditoría con nuestro contexto
            cursor.execute(f"""
                SELECT user_id, auth_source, operation, table_name
                FROM "{TEST_AUDIT_SCHEMA}".audit_log
                WHERE user_id = %s::uuid
                ORDER BY event_time DESC
                LIMIT 1
            """, (test_user_id,))
            audit_record = cursor.fetchone()

        assert audit_record is not None, "No se creó registro de auditoría"
        assert str(audit_record[0]) == test_user_id, f"user_id no coincide: {audit_record[0]}"
        assert audit_record[1] == test_auth_source, f"auth_source no coincide: {audit_record[1]}"
        assert audit_record[2] == "UPDATE", f"operation no coincide: {audit_record[2]}"
        assert audit_record[3] == "users", f"table_name no coincide: {audit_record[3]}"

    def test_audit_context_helper_import(self):
        """Verifica que el helper audit_context.py se puede importar."""
        from shared.audit_context import set_audit_context, clear_audit_context
        assert callable(set_audit_context)
        assert callable(clear_audit_context)

    def test_dependencies_get_auth_source(self):
        """Verifica que la dependency get_auth_source existe."""
        from shared.dependencies import get_auth_source, get_tenant_schema
        assert callable(get_auth_source)
        assert callable(get_tenant_schema)


class TestDatabaseFunctionsSignature:
    """Tests para verificar que las funciones de BD aceptan los nuevos parámetros."""

    def test_get_db_cursor_accepts_audit_params(self):
        """Verifica que get_db_cursor acepta user_id y auth_source."""
        from database import get_db_cursor
        import inspect

        sig = inspect.signature(get_db_cursor)
        params = list(sig.parameters.keys())

        assert "user_id" in params, "get_db_cursor no acepta user_id"
        assert "auth_source" in params, "get_db_cursor no acepta auth_source"

    def test_execute_transaction_accepts_audit_params(self):
        """Verifica que execute_transaction acepta user_id y auth_source."""
        from database import execute_transaction
        import inspect

        sig = inspect.signature(execute_transaction)
        params = list(sig.parameters.keys())

        assert "user_id" in params, "execute_transaction no acepta user_id"
        assert "auth_source" in params, "execute_transaction no acepta auth_source"

    def test_execute_update_accepts_audit_params(self):
        """Verifica que execute_update acepta user_id y auth_source."""
        from database import execute_update
        import inspect

        sig = inspect.signature(execute_update)
        params = list(sig.parameters.keys())

        assert "user_id" in params, "execute_update no acepta user_id"
        assert "auth_source" in params, "execute_update no acepta auth_source"


class TestMCPContextAuthSource:
    """Tests para MCPContext con auth_source."""

    def test_mcp_context_has_auth_source(self):
        """Verifica que MCPContext tiene el campo auth_source."""
        from api_gateway.context import MCPContext

        ctx = MCPContext(
            api_key="test",
            municipality_id="123",
            schema_name="test_schema",
            auth_source="mcp_oauth"
        )

        assert ctx.auth_source == "mcp_oauth"

    def test_mcp_context_default_auth_source(self):
        """Verifica que MCPContext tiene default auth_source=api_key."""
        from api_gateway.context import MCPContext

        ctx = MCPContext(
            api_key="test",
            municipality_id="123",
            schema_name="test_schema"
        )

        assert ctx.auth_source == "api_key"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
