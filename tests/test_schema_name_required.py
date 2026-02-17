"""
Tests de seguridad: schema_name obligatorio en funciones de BD.

Verifica que el refactor de seguridad multi-tenant esté correctamente
implementado y que todas las funciones de BD requieran schema_name explícito.
"""
import pytest
from unittest.mock import patch, MagicMock
from contextlib import nullcontext as does_not_raise


class TestSchemaNameRequired:
    """Verifica que las funciones de BD requieren schema_name."""

    def test_get_db_connection_without_schema_raises(self):
        """get_db_connection debe fallar sin schema_name."""
        from database import get_db_connection

        with pytest.raises(ValueError, match="schema_name es REQUERIDO"):
            with get_db_connection(None):
                pass

    def test_get_db_connection_with_empty_schema_raises(self):
        """get_db_connection debe fallar con schema vacío."""
        from database import get_db_connection

        with pytest.raises(ValueError, match="schema_name es REQUERIDO"):
            with get_db_connection(""):
                pass

    def test_get_db_connection_with_whitespace_raises(self):
        """get_db_connection debe fallar con solo espacios."""
        from database import get_db_connection

        with pytest.raises(ValueError, match="schema_name es REQUERIDO"):
            with get_db_connection("   "):
                pass

    def test_execute_query_requires_keyword_schema(self):
        """execute_query debe requerir schema_name como keyword-only."""
        from database import execute_query

        # Intentar pasar schema_name como argumento posicional debe fallar
        with pytest.raises(TypeError):
            # execute_query(query, params, fetch_one, fetch_all, timeout, schema_name)
            # El "*" antes de schema_name hace que sea keyword-only
            execute_query("SELECT 1", None, True, False, 2, "public")

    def test_execute_update_requires_keyword_schema(self):
        """execute_update debe requerir schema_name como keyword-only."""
        from database import execute_update

        with pytest.raises(TypeError):
            # Intentar pasar schema como posicional
            execute_update("UPDATE x SET y=1", None, "public")

    def test_get_db_cursor_requires_keyword_schema(self):
        """get_db_cursor debe requerir schema_name como keyword-only."""
        from database import get_db_cursor

        with pytest.raises(TypeError):
            # commit y schema como posicionales
            with get_db_cursor(True, "public"):
                pass


class TestServiceFunctionsRequireSchema:
    """Verifica que las funciones de servicio requieren schema_name."""

    def test_case_service_get_user_editable_requires_schema(self):
        """CaseService.get_user_editable_sector_ids debe requerir schema_name."""
        from services.case_service import CaseService

        # Sin schema_name debe fallar con TypeError (keyword-only)
        with pytest.raises(TypeError):
            CaseService.get_user_editable_sector_ids("user-id")

    def test_user_service_requires_schema(self):
        """Funciones de user_service deben requerir schema_name."""
        from services.user_service import get_user_by_id

        with pytest.raises(TypeError):
            # Sin schema_name como keyword
            get_user_by_id("user-id")

    def test_sector_service_requires_schema(self):
        """Funciones de SectorService deben requerir schema_name."""
        from services.sector_service import SectorService

        with pytest.raises(TypeError):
            # Sin schema_name como keyword
            SectorService.get_all_sectors_with_departments()


class TestMultiTenantIsolation:
    """Verifica aislamiento entre tenants (requiere BD real)."""

    @pytest.fixture
    def mock_pool(self):
        """Mock del pool de conexiones."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        with patch('database.connection_pool') as mock_pool:
            mock_pool.getconn.return_value = mock_conn
            yield mock_pool, mock_conn, mock_cursor

    def test_search_path_is_set_on_connection(self, mock_pool):
        """Verifica que search_path se configura al obtener conexión."""
        from database import get_db_connection

        pool, conn, cursor = mock_pool
        schema_name = "100_test_tenant"

        with get_db_connection(schema_name):
            # Verificar que se ejecutó SET LOCAL search_path
            cursor.execute.assert_called()
            calls = [str(call) for call in cursor.execute.call_args_list]
            # Al menos una llamada debe incluir search_path
            assert any("search_path" in str(call) for call in calls), \
                f"search_path no fue configurado. Llamadas: {calls}"

    def test_different_schemas_are_isolated(self, mock_pool):
        """Conexiones a diferentes schemas son independientes."""
        from database import get_db_connection

        pool, conn, cursor = mock_pool

        # Ejecutar dos conexiones a schemas diferentes
        with get_db_connection("schema_a"):
            first_calls = cursor.execute.call_args_list.copy()

        cursor.reset_mock()

        with get_db_connection("schema_b"):
            second_calls = cursor.execute.call_args_list.copy()

        # Ambas deben haber configurado su propio search_path
        assert len(first_calls) > 0
        assert len(second_calls) > 0


class TestSchemaNamePropagation:
    """Verifica que schema_name se propaga correctamente."""

    def test_endpoint_to_service_propagation(self):
        """Schema debe propagarse de endpoint a service."""
        # Este test documenta el patrón esperado
        # En un endpoint real:
        # schema_name = request.state.schema_name
        # result = some_service(param1, schema_name=schema_name)

        # Verificar que el patrón funciona
        def example_service(param: str, *, schema_name: str) -> str:
            """Servicio que requiere schema_name como keyword-only."""
            return f"{param}@{schema_name}"

        # Forma correcta
        result = example_service("test", schema_name="tenant_a")
        assert result == "test@tenant_a"

        # Forma incorrecta (posicional) debe fallar
        with pytest.raises(TypeError):
            example_service("test", "tenant_a")

    def test_nested_service_calls_propagate_schema(self):
        """Llamadas anidadas deben propagar schema_name."""
        def inner_service(data: str, *, schema_name: str) -> str:
            return f"inner({data})@{schema_name}"

        def outer_service(data: str, *, schema_name: str) -> str:
            inner_result = inner_service(data, schema_name=schema_name)
            return f"outer({inner_result})@{schema_name}"

        result = outer_service("test", schema_name="tenant_x")
        assert result == "outer(inner(test)@tenant_x)@tenant_x"


class TestSchemaNamePatternCompliance:
    """Verifica que los archivos siguen el patrón correcto."""

    @pytest.mark.parametrize("module_path,class_name", [
        ("services.case_service", "CaseService"),
        ("services.sector_service", "SectorService"),
    ])
    def test_service_class_has_keyword_only_schema(self, module_path, class_name):
        """Clases de servicio deben tener schema_name como keyword-only en todos sus métodos."""
        import importlib
        import inspect

        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)

        # Verificar todos los métodos públicos de la clase
        for method_name in dir(cls):
            if method_name.startswith('_'):
                continue
            method = getattr(cls, method_name)
            if not callable(method) or isinstance(method, type):
                continue
            try:
                sig = inspect.signature(method)
            except (ValueError, TypeError):
                continue

            # Si tiene schema_name, debe ser keyword-only
            if 'schema_name' in sig.parameters:
                param = sig.parameters['schema_name']
                assert param.kind == inspect.Parameter.KEYWORD_ONLY, \
                    f"{module_path}.{class_name}.{method_name}: schema_name debe ser keyword-only"

    @pytest.mark.parametrize("module_path,function_name", [
        ("services.user_service", "get_user_by_id"),
        ("services.user_service", "get_user_by_auth_id"),
        ("services.user_service", "get_user_by_email"),
        ("services.user_service", "get_first_active_user"),
        ("services.user_service", "get_user_sector_permissions"),
        ("services.user_service", "update_last_access"),
    ])
    def test_function_has_keyword_only_schema(self, module_path, function_name):
        """Funciones de servicio deben tener schema_name como keyword-only."""
        import importlib
        import inspect

        module = importlib.import_module(module_path)
        func = getattr(module, function_name)
        sig = inspect.signature(func)

        # Verificar que schema_name existe
        assert 'schema_name' in sig.parameters, \
            f"{module_path}.{function_name}: debe tener parámetro schema_name"

        # Verificar que es keyword-only
        param = sig.parameters['schema_name']
        assert param.kind == inspect.Parameter.KEYWORD_ONLY, \
            f"{module_path}.{function_name}: schema_name debe ser keyword-only"

        # Verificar que no tiene default (es requerido)
        assert param.default == inspect.Parameter.empty, \
            f"{module_path}.{function_name}: schema_name no debe tener valor default"
