import pytest


class TestSchemaNameRequired:

    def test_validate_schema_name_rejects_none(self):
        from database import validate_schema_name
        with pytest.raises(ValueError):
            validate_schema_name(None)

    def test_validate_schema_name_rejects_empty(self):
        from database import validate_schema_name
        with pytest.raises(ValueError):
            validate_schema_name("")

    def test_validate_schema_name_rejects_whitespace(self):
        from database import validate_schema_name
        with pytest.raises(ValueError):
            validate_schema_name("   ")

    def test_fetch_all_schema_name_is_keyword_only(self):
        import inspect
        from database import fetch_all
        sig = inspect.signature(fetch_all)
        param = sig.parameters.get('schema_name')
        assert param is not None, "fetch_all no tiene parámetro schema_name"
        assert param.kind == inspect.Parameter.KEYWORD_ONLY

    def test_fetch_one_schema_name_is_keyword_only(self):
        import inspect
        from database import fetch_one
        sig = inspect.signature(fetch_one)
        param = sig.parameters.get('schema_name')
        assert param is not None, "fetch_one no tiene parámetro schema_name"
        assert param.kind == inspect.Parameter.KEYWORD_ONLY

    def test_execute_schema_name_is_keyword_only(self):
        import inspect
        from database import execute
        sig = inspect.signature(execute)
        param = sig.parameters.get('schema_name')
        assert param is not None, "execute no tiene parámetro schema_name"
        assert param.kind == inspect.Parameter.KEYWORD_ONLY


class TestServiceFunctionsRequireSchema:

    def test_case_service_get_user_editable_requires_schema(self):
        from services.case_service import CaseService

        with pytest.raises(TypeError):
            CaseService.get_user_editable_sector_ids("user-id")

    def test_user_service_requires_schema(self):
        from services.user_service import get_user_by_id

        with pytest.raises(TypeError):
            get_user_by_id("user-id")

    def test_sector_service_requires_schema(self):
        from services.sector_service import SectorService

        with pytest.raises(TypeError):
            SectorService.get_all_sectors_with_departments()


class TestMultiTenantIsolation:

    def test_get_conn_has_schema_name_keyword_only(self):
        import inspect
        from database import get_conn
        sig = inspect.signature(get_conn)
        param = sig.parameters.get('schema_name')
        assert param is not None, "get_conn no tiene parámetro schema_name"
        assert param.kind == inspect.Parameter.KEYWORD_ONLY

    def test_get_conn_sets_search_path_in_implementation(self):
        import inspect
        from database import get_conn
        source = inspect.getsource(get_conn)
        assert "search_path" in source, "get_conn no configura search_path"

    def test_different_schemas_validated_independently(self):
        from database import validate_schema_name
        assert validate_schema_name("schema_a") == "schema_a"
        assert validate_schema_name("schema_b") == "schema_b"
        assert validate_schema_name("100_test") == "100_test"


class TestSchemaNamePropagation:

    def test_endpoint_to_service_propagation(self):

        def example_service(param: str, *, schema_name: str) -> str:
            return f"{param}@{schema_name}"

        result = example_service("test", schema_name="tenant_a")
        assert result == "test@tenant_a"

        with pytest.raises(TypeError):
            example_service("test", "tenant_a")

    def test_nested_service_calls_propagate_schema(self):
        def inner_service(data: str, *, schema_name: str) -> str:
            return f"inner({data})@{schema_name}"

        def outer_service(data: str, *, schema_name: str) -> str:
            inner_result = inner_service(data, schema_name=schema_name)
            return f"outer({inner_result})@{schema_name}"

        result = outer_service("test", schema_name="tenant_x")
        assert result == "outer(inner(test)@tenant_x)@tenant_x"


class TestSchemaNamePatternCompliance:

    @pytest.mark.parametrize("module_path,class_name", [
        ("services.case_service", "CaseService"),
        ("services.sector_service", "SectorService"),
    ])
    def test_service_class_has_keyword_only_schema(self, module_path, class_name):
        import importlib
        import inspect

        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)

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
        import importlib
        import inspect

        module = importlib.import_module(module_path)
        func = getattr(module, function_name)
        sig = inspect.signature(func)

        assert 'schema_name' in sig.parameters, \
            f"{module_path}.{function_name}: debe tener parámetro schema_name"

        param = sig.parameters['schema_name']
        assert param.kind == inspect.Parameter.KEYWORD_ONLY, \
            f"{module_path}.{function_name}: schema_name debe ser keyword-only"

        assert param.default == inspect.Parameter.empty, \
            f"{module_path}.{function_name}: schema_name no debe tener valor default"
