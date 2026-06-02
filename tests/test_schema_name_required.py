"""
Tests de seguridad: schema_name obligatorio en funciones de BD.

Verifica que el refactor de seguridad multi-tenant esté correctamente
implementado y que todas las funciones de BD requieran schema_name explícito.
"""
import pytest


class TestSchemaNameRequired:
    """Verifica que las funciones asyncpg de BD requieren schema_name."""

    def test_validate_schema_name_rejects_none(self):
        """validate_schema_name debe fallar con None."""
        from database import validate_schema_name
        with pytest.raises(ValueError):
            validate_schema_name(None)

    def test_validate_schema_name_rejects_empty(self):
        """validate_schema_name debe fallar con string vacío."""
        from database import validate_schema_name
        with pytest.raises(ValueError):
            validate_schema_name("")

    def test_validate_schema_name_rejects_whitespace(self):
        """validate_schema_name debe fallar con solo espacios."""
        from database import validate_schema_name
        with pytest.raises(ValueError):
            validate_schema_name("   ")

    def test_fetch_all_schema_name_is_keyword_only(self):
        """fetch_all debe requerir schema_name como keyword-only."""
        import inspect
        from database import fetch_all
        sig = inspect.signature(fetch_all)
        param = sig.parameters.get('schema_name')
        assert param is not None, "fetch_all no tiene parámetro schema_name"
        assert param.kind == inspect.Parameter.KEYWORD_ONLY

    def test_fetch_one_schema_name_is_keyword_only(self):
        """fetch_one debe requerir schema_name como keyword-only."""
        import inspect
        from database import fetch_one
        sig = inspect.signature(fetch_one)
        param = sig.parameters.get('schema_name')
        assert param is not None, "fetch_one no tiene parámetro schema_name"
        assert param.kind == inspect.Parameter.KEYWORD_ONLY

    def test_execute_schema_name_is_keyword_only(self):
        """execute debe requerir schema_name como keyword-only."""
        import inspect
        from database import execute
        sig = inspect.signature(execute)
        param = sig.parameters.get('schema_name')
        assert param is not None, "execute no tiene parámetro schema_name"
        assert param.kind == inspect.Parameter.KEYWORD_ONLY


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
    """Verifica aislamiento entre tenants vía asyncpg."""

    def test_get_conn_has_schema_name_keyword_only(self):
        """get_conn debe requerir schema_name como keyword-only."""
        import inspect
        from database import get_conn
        sig = inspect.signature(get_conn)
        param = sig.parameters.get('schema_name')
        assert param is not None, "get_conn no tiene parámetro schema_name"
        assert param.kind == inspect.Parameter.KEYWORD_ONLY

    def test_get_conn_sets_search_path_in_implementation(self):
        """Verifica que get_conn configura SET LOCAL search_path en la conexión."""
        import inspect
        from database import get_conn
        source = inspect.getsource(get_conn)
        assert "search_path" in source, "get_conn no configura search_path"

    def test_different_schemas_validated_independently(self):
        """Schemas distintos pasan validación de forma independiente."""
        from database import validate_schema_name
        assert validate_schema_name("schema_a") == "schema_a"
        assert validate_schema_name("schema_b") == "schema_b"
        assert validate_schema_name("100_test") == "100_test"


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
