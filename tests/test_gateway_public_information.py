import asyncio
import inspect
from datetime import datetime, timedelta, timezone
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

TEST_SCHEMA = "100_test"
TEST_MUNI = "mt"


class TestRestApiPublicExports:
    def test_handlers_exported(self):
        from api_gateway.rest_api_public import (
            api_public_search,
            api_public_registries,
            api_public_list_records,
            api_public_get_record,
        )
        for fn in (api_public_search, api_public_registries, api_public_list_records, api_public_get_record):
            assert callable(fn)
            assert inspect.iscoroutinefunction(fn)


class TestGatewayRoutesRegistered:
    def _get_routes(self):
        from api_gateway.http_server import routes as gateway_routes
        result = {}
        for route in gateway_routes:
            if hasattr(route, "path") and hasattr(route, "methods"):
                for method in (route.methods or []):
                    result[(route.path, method)] = getattr(route, "endpoint", None)
        return result

    def test_search_route_registered(self):
        routes = self._get_routes()
        assert ("/api/v1/public/{muni}/search", "GET") in routes

    def test_registries_route_registered(self):
        routes = self._get_routes()
        assert ("/api/v1/public/{muni}/registries", "GET") in routes

    def test_list_records_route_registered(self):
        routes = self._get_routes()
        assert ("/api/v1/public/{muni}/registries/{code}/records", "GET") in routes

    def test_get_record_route_registered(self):
        routes = self._get_routes()
        assert ("/api/v1/public/{muni}/records/{record_number}", "GET") in routes

    def test_private_routes_unaffected(self):
        routes = self._get_routes()
        assert ("/api/v1/documents/search", "GET") in routes
        assert ("/api/v1/records/search", "GET") in routes


def _build_test_app(handler, path):
    return Starlette(routes=[Route(path, handler, methods=["GET"])])


def _api_key_row(**overrides):
    row = {
        "id": "key-1",
        "key_active": True,
        "expires_at": None,
        "rate_limit_per_minute": 60,
        "key_type": "api",
        "schema_name": TEST_SCHEMA,
        "acronym": TEST_MUNI.upper(),
        "muni_active": True,
    }
    row.update(overrides)
    return row


class TestValidatePublicApiKey:

    async def _run(self, api_key, muni, row):
        with patch("api_gateway.auth_rest.validate_schema_name"), \
             patch("api_gateway.auth_rest._update_last_used", new_callable=AsyncMock), \
             patch("api_gateway.rate_limiter.rate_limiter.check"), \
             patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = row
            from api_gateway.auth_rest import validate_public_api_key
            return await validate_public_api_key(api_key, muni)

    @pytest.mark.asyncio
    async def test_sin_api_key_401(self):
        from api_gateway.auth_rest import validate_public_api_key, PublicAuthError
        with pytest.raises(PublicAuthError) as exc:
            await validate_public_api_key(None, TEST_MUNI)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_key_inexistente_401(self):
        from api_gateway.auth_rest import PublicAuthError
        with pytest.raises(PublicAuthError) as exc:
            await self._run("sk-gdi-xxx", TEST_MUNI, None)
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_key_inactiva_401(self):
        from api_gateway.auth_rest import PublicAuthError
        with pytest.raises(PublicAuthError) as exc:
            await self._run("sk-gdi-xxx", TEST_MUNI, _api_key_row(key_active=False))
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_muni_inactivo_401(self):
        from api_gateway.auth_rest import PublicAuthError
        with pytest.raises(PublicAuthError) as exc:
            await self._run("sk-gdi-xxx", TEST_MUNI, _api_key_row(muni_active=False))
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_key_expirada_401(self):
        from api_gateway.auth_rest import PublicAuthError
        expired = datetime.now(timezone.utc) - timedelta(days=1)
        with pytest.raises(PublicAuthError) as exc:
            await self._run("sk-gdi-xxx", TEST_MUNI, _api_key_row(expires_at=expired))
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_key_tipo_backup_401(self):
        from api_gateway.auth_rest import PublicAuthError
        with pytest.raises(PublicAuthError) as exc:
            await self._run("sk-gdi-xxx", TEST_MUNI, _api_key_row(key_type="backup"))
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_key_type_whitelist_cerrada_por_defecto(self):
        from api_gateway.auth_rest import PublicAuthError
        for bad_type in ("admin", "service", "", None):
            with pytest.raises(PublicAuthError) as exc:
                await self._run("sk-gdi-xxx", TEST_MUNI, _api_key_row(key_type=bad_type))
            assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_key_de_otro_muni_403(self):
        from api_gateway.auth_rest import PublicAuthError
        with pytest.raises(PublicAuthError) as exc:
            await self._run("sk-gdi-xxx", TEST_MUNI, _api_key_row(acronym="OT", schema_name="200_ot"))
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_key_valida_devuelve_schema(self):
        schema = await self._run("sk-gdi-xxx", TEST_MUNI, _api_key_row())
        assert schema == TEST_SCHEMA

    @pytest.mark.asyncio
    async def test_key_valida_aplica_rate_limit_per_key(self):
        with patch("api_gateway.auth_rest.validate_schema_name"), \
             patch("api_gateway.auth_rest._update_last_used", new_callable=AsyncMock), \
             patch("api_gateway.rate_limiter.rate_limiter.check") as mock_check, \
             patch("database.fetch_one", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = _api_key_row(id="key-42", rate_limit_per_minute=120)
            from api_gateway.auth_rest import validate_public_api_key
            await validate_public_api_key("sk-gdi-xxx", TEST_MUNI)
            mock_check.assert_called_once()
            args = mock_check.call_args[0]
            assert args[0] == "public_key:key-42"
            assert args[1] == 120


class TestHandlerAuthWiring:

    @pytest.mark.asyncio
    @patch("api_gateway.rest_api_public.check_public_rate_limit")
    @patch("api_gateway.rest_api_public.validate_public_api_key", new_callable=AsyncMock)
    async def test_sin_api_key_devuelve_401(self, mock_validate, mock_rate_limit):
        from api_gateway.rest_api_public import api_public_search
        from api_gateway.auth_rest import PublicAuthError

        mock_validate.side_effect = PublicAuthError("X-API-Key requerido", status_code=401)
        app = _build_test_app(api_public_search, "/public/{muni}/search")
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get(f"/public/{TEST_MUNI}/search?q=ordenanza")
        assert resp.status_code == 401
        assert "schema" not in str(resp.json()).lower()

    @pytest.mark.asyncio
    @patch("api_gateway.rest_api_public.check_public_rate_limit")
    @patch("api_gateway.rest_api_public.validate_public_api_key", new_callable=AsyncMock)
    async def test_key_de_otro_muni_devuelve_403(self, mock_validate, mock_rate_limit):
        from api_gateway.rest_api_public import api_public_registries
        from api_gateway.auth_rest import PublicAuthError

        mock_validate.side_effect = PublicAuthError("API Key no valida para este municipio", status_code=403)
        app = _build_test_app(api_public_registries, "/public/{muni}/registries")
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get(f"/public/{TEST_MUNI}/registries", headers={"X-API-Key": "sk-gdi-otro"})
        assert resp.status_code == 403

    @pytest.mark.asyncio
    @patch("api_gateway.rest_api_public.check_public_rate_limit")
    @patch("api_gateway.rest_api_public.public_search", new_callable=AsyncMock)
    @patch("api_gateway.rest_api_public.validate_public_api_key", new_callable=AsyncMock)
    async def test_con_api_key_valida_responde_200(self, mock_validate, mock_search, mock_rate_limit):
        from api_gateway.rest_api_public import api_public_search

        mock_validate.return_value = TEST_SCHEMA
        mock_search.return_value = {"success": True, "query": "ordenanza 100", "intent": "lookup", "results": [], "total": 0}

        app = _build_test_app(api_public_search, "/public/{muni}/search")
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get(f"/public/{TEST_MUNI}/search?q=ordenanza+100", headers={"X-API-Key": "sk-gdi-ok"})
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        mock_search.assert_awaited_once()
        mock_validate.assert_awaited_once()
        assert mock_validate.call_args[0][1] == TEST_MUNI
        assert resp.headers["cache-control"] == "private, max-age=60"


class TestRateLimitTier:
    @pytest.mark.asyncio
    @patch("api_gateway.rest_api_public.check_public_rate_limit")
    @patch("api_gateway.rest_api_public.public_search", new_callable=AsyncMock)
    @patch("api_gateway.rest_api_public.validate_public_api_key", new_callable=AsyncMock)
    async def test_search_usa_limite_de_search(self, mock_validate, mock_search, mock_check_rl):
        from api_gateway.rest_api_public import api_public_search, PUBLIC_IP_SEARCH_LIMIT

        mock_validate.return_value = TEST_SCHEMA
        mock_search.return_value = {"success": True, "results": [], "total": 0}

        app = _build_test_app(api_public_search, "/public/{muni}/search")
        client = TestClient(app, raise_server_exceptions=False)
        client.get(f"/public/{TEST_MUNI}/search?q=ordenanza")

        assert mock_check_rl.called
        args = mock_check_rl.call_args[0]
        assert args[1] == PUBLIC_IP_SEARCH_LIMIT

    @pytest.mark.asyncio
    @patch("api_gateway.rest_api_public.check_public_rate_limit")
    async def test_rate_limit_exceeded_propaga_para_middleware(self, mock_check_rl):
        from api_gateway.rest_api_public import api_public_search
        from api_gateway.rate_limiter import RateLimitExceeded

        mock_check_rl.side_effect = RateLimitExceeded(retry_after=5)

        from starlette.requests import Request

        scope = {
            "type": "http", "method": "GET",
            "path": f"/public/{TEST_MUNI}/search", "query_string": b"q=ordenanza",
            "path_params": {"muni": TEST_MUNI}, "headers": [],
        }
        request = Request(scope)

        with pytest.raises(RateLimitExceeded):
            await api_public_search(request)


class TestPublicRateLimitRedisFailClosed:

    def test_sin_redis_bloquea_la_request(self):
        from api_gateway.public_info.rate_limit import check_public_rate_limit
        from api_gateway.rate_limiter import RateLimitExceeded

        with patch("api_gateway.public_info.rate_limit.get_redis", return_value=None):
            with pytest.raises(RateLimitExceeded):
                check_public_rate_limit("1.2.3.4", limit=30, window_seconds=60)

    def test_redis_caido_en_runtime_bloquea_la_request(self):
        from api_gateway.public_info.rate_limit import check_public_rate_limit
        from api_gateway.rate_limiter import RateLimitExceeded

        broken_client = MagicMock()
        broken_client.incr.side_effect = Exception("Connection refused")
        with patch("api_gateway.public_info.rate_limit.get_redis", return_value=broken_client):
            with pytest.raises(RateLimitExceeded):
                check_public_rate_limit("1.2.3.4", limit=30, window_seconds=60)

    def test_debajo_del_limite_no_lanza(self):
        from api_gateway.public_info.rate_limit import check_public_rate_limit

        client = MagicMock()
        client.incr.return_value = 5
        with patch("api_gateway.public_info.rate_limit.get_redis", return_value=client):
            check_public_rate_limit("1.2.3.4", limit=30, window_seconds=60)

    def test_por_encima_del_limite_lanza(self):
        from api_gateway.public_info.rate_limit import check_public_rate_limit
        from api_gateway.rate_limiter import RateLimitExceeded

        client = MagicMock()
        client.incr.return_value = 31
        with patch("api_gateway.public_info.rate_limit.get_redis", return_value=client):
            with pytest.raises(RateLimitExceeded):
                check_public_rate_limit("1.2.3.4", limit=30, window_seconds=60)


class TestPublicClientIpNoTrustXForwardedFor:

    def test_usa_fly_client_ip_si_esta_presente(self):
        from api_gateway.public_info.rate_limit import get_public_client_ip

        request = MagicMock()
        request.headers = {"fly-client-ip": "9.9.9.9", "x-forwarded-for": "1.1.1.1"}
        assert get_public_client_ip(request) == "9.9.9.9"

    def test_no_confia_en_x_forwarded_for_si_falta_fly_client_ip(self):
        from api_gateway.public_info.rate_limit import get_public_client_ip

        request = MagicMock()
        request.headers = {"x-forwarded-for": "1.1.1.1, 2.2.2.2"}
        request.client.host = "10.0.0.5"
        ip = get_public_client_ip(request)
        assert ip != "1.1.1.1"
        assert ip == "10.0.0.5"


class TestCorsPublicRoutesClosed:

    @pytest.mark.asyncio
    async def test_options_publico_pasa_a_call_next(self):
        from api_gateway.gateway_middleware import GatewayMiddleware
        from starlette.requests import Request
        from starlette.responses import Response as StarletteResponse

        middleware = GatewayMiddleware(app=None)
        scope = {
            "type": "http", "method": "OPTIONS",
            "path": "/api/v1/public/mt/search", "headers": [],
        }
        request = Request(scope)

        called = {"v": False}

        async def _call_next(_req):
            called["v"] = True
            return StarletteResponse(status_code=200)

        resp = await middleware.dispatch(request, _call_next)
        assert called["v"] is True
        assert resp.headers.get("Access-Control-Allow-Origin") != "*"

    @pytest.mark.asyncio
    async def test_response_get_publica_no_abre_cors(self):
        from api_gateway.gateway_middleware import GatewayMiddleware
        from starlette.requests import Request
        from starlette.responses import JSONResponse

        middleware = GatewayMiddleware(app=None)
        scope = {
            "type": "http", "method": "GET",
            "path": "/api/v1/public/mt/registries", "headers": [],
        }
        request = Request(scope)

        async def _call_next(_req):
            return JSONResponse({"success": True})

        with patch("api_gateway.gateway_middleware.log_rest_request"):
            resp = await middleware.dispatch(request, _call_next)

        assert "Access-Control-Allow-Origin" not in resp.headers

    def test_prefijo_publico_ya_no_se_exporta(self):
        import api_gateway.gateway_middleware as gm
        assert not hasattr(gm, "PUBLIC_INFO_PATH_PREFIX")
        assert not hasattr(gm, "_PUBLIC_CORS_HEADERS")


class TestSanitizeWhitelist:
    def test_whitelist_solo_deja_campos_permitidos(self):
        from api_gateway.public_info.sanitize import whitelist_fields

        data = {"dni": "12345678", "razon_social": "Acme SA", "cbu": "0000003100..."}
        result = whitelist_fields(data, ["dni", "razon_social"])
        assert result == {"dni": "12345678", "razon_social": "Acme SA"}
        assert "cbu" not in result

    def test_whitelist_vacio_si_sin_fields(self):
        from api_gateway.public_info.sanitize import whitelist_fields
        assert whitelist_fields({"dni": "1"}, []) == {}

    def test_whitelist_ignora_campos_inexistentes_en_data(self):
        from api_gateway.public_info.sanitize import whitelist_fields
        result = whitelist_fields({"dni": "1"}, ["dni", "campo_que_no_existe"])
        assert result == {"dni": "1"}

    def test_pdf_url_formato(self):
        from api_gateway.public_info.sanitize import build_public_pdf_url
        uuid = "1f2e3d4c-5b6a-7089-90ab-cdef01234567"
        url = build_public_pdf_url("MT", uuid)
        assert url == f"https://public.your-domain.com/mt/{uuid}.pdf"


class TestSqlInjectionOnFieldNames:

    def test_field_malicioso_se_descarta(self):
        from api_gateway.public_info.sanitize import sanitize_field_names

        fields = ["dni", "x' OR '1'='1", "razon_social'; DROP TABLE records;--"]
        result = sanitize_field_names(fields)
        assert result == ["dni"]

    def test_field_con_mayusculas_o_simbolos_se_descarta(self):
        from api_gateway.public_info.sanitize import sanitize_field_names
        assert sanitize_field_names(["DNI", "razon-social", "a b", "cbu.numero"]) == []

    def test_field_valido_pasa(self):
        from api_gateway.public_info.sanitize import sanitize_field_names
        assert sanitize_field_names(["dni", "razon_social", "campo_123"]) == ["dni", "razon_social", "campo_123"]

    def test_whitelist_fields_tambien_sanitiza(self):
        from api_gateway.public_info.sanitize import whitelist_fields

        data = {"dni": "1", "x' OR '1'='1": "deberia ser inalcanzable"}
        result = whitelist_fields(data, ["dni", "x' OR '1'='1"])
        assert result == {"dni": "1"}

    @pytest.mark.asyncio
    @patch("api_gateway.public_info.records.get_public_families", new_callable=AsyncMock)
    @patch("api_gateway.public_info.records.fetch_all", new_callable=AsyncMock)
    @patch("api_gateway.public_info.records.fetch_one", new_callable=AsyncMock)
    async def test_familia_con_field_malicioso_no_llega_al_sql(self, mock_fetch_one, mock_fetch_all, mock_families):
        from api_gateway.public_info.records import list_records_public

        mock_families.return_value = [
            {"id": "fam-1", "code": "ARQ", "name": "Archivo", "description": None,
             "public_config": {
                 "fields": ["dni", "x' OR '1'='1"],
                 "visible_states": ["Activo"],
             }},
        ]
        mock_fetch_one.return_value = {"total": 0}
        mock_fetch_all.return_value = []

        await list_records_public(schema_name=TEST_SCHEMA, search="12345678", page=1, page_size=20)

        count_sql = mock_fetch_one.call_args[0][0]
        list_sql = mock_fetch_all.call_args[0][0]
        assert "OR '1'='1'" not in count_sql
        assert "OR '1'='1'" not in list_sql
        assert "r.data->>'dni'" in count_sql


class TestReservedNeverInPublicSql:
    def test_semantic_search_public_filtra_visibility_publico(self):
        from api_gateway.public_info.queries import SEMANTIC_SEARCH_PUBLIC_SQL
        assert "visibility = 'publico'" in SEMANTIC_SEARCH_PUBLIC_SQL
        assert "NOT dt_filter.is_reserved" in SEMANTIC_SEARCH_PUBLIC_SQL

    def test_lookup_public_filtra_visibility_publico(self):
        from api_gateway.public_info.queries import LOOKUP_DOCUMENT_PUBLIC_SQL
        assert "visibility = 'publico'" in LOOKUP_DOCUMENT_PUBLIC_SQL
        assert "NOT dt.is_reserved" in LOOKUP_DOCUMENT_PUBLIC_SQL

    def test_ningun_sql_publico_depende_de_user_id(self):
        from api_gateway.public_info.queries import SEMANTIC_SEARCH_PUBLIC_SQL, LOOKUP_DOCUMENT_PUBLIC_SQL
        for sql in (SEMANTIC_SEARCH_PUBLIC_SQL, LOOKUP_DOCUMENT_PUBLIC_SQL):
            assert "user_sectors" not in sql
            assert "case_movements" not in sql
            assert "case_responsibles" not in sql

    def test_search_public_no_expone_expedientes_vinculados(self):
        from api_gateway.public_info.queries import SEMANTIC_SEARCH_PUBLIC_SQL, LOOKUP_DOCUMENT_PUBLIC_SQL
        for sql in (SEMANTIC_SEARCH_PUBLIC_SQL, LOOKUP_DOCUMENT_PUBLIC_SQL):
            assert "cases_agg" not in sql
            assert "linked_cases" not in sql

    def test_linked_records_filtra_familia_publica(self):
        from api_gateway.public_info.queries import SEMANTIC_SEARCH_PUBLIC_SQL, LOOKUP_DOCUMENT_PUBLIC_SQL
        for sql in (SEMANTIC_SEARCH_PUBLIC_SQL, LOOKUP_DOCUMENT_PUBLIC_SQL):
            assert "rf.is_public = true" in sql


class TestReservedDocumentExcludedFromRecordDetail:
    @pytest.mark.asyncio
    @patch("api_gateway.public_info.records.get_bucket_publico", new_callable=AsyncMock)
    @patch("api_gateway.public_info.records.fetch_all", new_callable=AsyncMock)
    async def test_linked_documents_query_excluye_reservados(self, mock_fetch_all, mock_bucket):
        from api_gateway.public_info.records import _get_linked_documents_public

        mock_bucket.return_value = None
        mock_fetch_all.return_value = []
        await _get_linked_documents_public(record_id="rec-1", schema_name=TEST_SCHEMA, muni="mt")

        sql_used = mock_fetch_all.call_args[0][0]
        assert "dt.visibility != 'reservado'" in sql_used

    @pytest.mark.asyncio
    @patch("api_gateway.public_info.records.get_bucket_publico", new_callable=AsyncMock)
    @patch("api_gateway.public_info.records.fetch_all", new_callable=AsyncMock)
    async def test_doc_publico_tiene_link_doc_normal_sin_link(self, mock_fetch_all, mock_bucket):
        from api_gateway.public_info.records import _get_linked_documents_public

        mock_bucket.return_value = "tenant-mt-publico"
        mock_fetch_all.return_value = [
            {"official_number": "PLORD-2026-001-MT-HCD", "reference": "Ordenanza publica", "resume": "Resumen ordenanza", "visibility": "publico", "document_id": "11111111-1111-1111-1111-111111111111"},
            {"official_number": "MEMO-2026-002-MT-HCD", "reference": "Memo interno", "resume": "Resumen memo", "visibility": "interno", "document_id": None},
        ]

        docs = await _get_linked_documents_public(record_id="rec-1", schema_name=TEST_SCHEMA, muni="mt")

        pub_doc = next(d for d in docs if d["official_number"] == "PLORD-2026-001-MT-HCD")
        normal_doc = next(d for d in docs if d["official_number"] == "MEMO-2026-002-MT-HCD")
        assert pub_doc["pdf_url"] is not None
        assert pub_doc["pdf_url"] == "https://public.your-domain.com/mt/11111111-1111-1111-1111-111111111111.pdf"
        assert normal_doc["pdf_url"] is None
        assert pub_doc["document_id"] == "11111111-1111-1111-1111-111111111111"
        assert normal_doc["document_id"] is None
        assert pub_doc["resume"] == "Resumen ordenanza"
        assert normal_doc["resume"] is None


class TestReservedCasesExcludedFromRecordDetail:

    @pytest.mark.asyncio
    @patch("api_gateway.public_info.records.fetch_all", new_callable=AsyncMock)
    async def test_linked_cases_query_excluye_reservados(self, mock_fetch_all):
        from api_gateway.public_info.records import _get_linked_cases_public

        mock_fetch_all.return_value = []
        await _get_linked_cases_public(record_id="rec-1", schema_name=TEST_SCHEMA)

        sql_used = mock_fetch_all.call_args[0][0]
        assert "case_templates" in sql_used
        assert "NOT ct.is_reserved" in sql_used


class TestFamilyAndFieldFiltering:
    @pytest.mark.asyncio
    @patch("api_gateway.rest_api_public.check_public_rate_limit")
    @patch("api_gateway.rest_api_public.get_public_families", new_callable=AsyncMock)
    @patch("api_gateway.rest_api_public.validate_public_api_key", new_callable=AsyncMock)
    async def test_familia_no_publica_devuelve_404(self, mock_validate, mock_families, mock_rate_limit):
        from api_gateway.rest_api_public import api_public_list_records

        mock_validate.return_value = TEST_SCHEMA
        mock_families.return_value = []

        app = _build_test_app(api_public_list_records, "/public/{muni}/registries/{code}/records")
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.get(f"/public/{TEST_MUNI}/registries/PRIVADO/records", headers={"X-API-Key": "sk-gdi-ok"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @patch("api_gateway.public_info.records.fetch_one", new_callable=AsyncMock)
    async def test_get_record_public_familia_no_publica_devuelve_none(self, mock_fetch_one):
        from api_gateway.public_info.records import get_record_public

        mock_fetch_one.return_value = {
            "id": "rec-1", "record_number": "RLM-2026-001-MT-ARQ", "display_name": "x",
            "state": "Activo", "data": {"dni": "1"},
            "family_id": "fam-1", "registry_code": "ARQ", "registry_name": "Archivo",
            "family_is_public": False, "public_config": {"fields": ["dni"]},
        }

        result = await get_record_public(schema_name=TEST_SCHEMA, record_number="RLM-2026-001-MT-ARQ", muni="mt")
        assert result is None

    @pytest.mark.asyncio
    @patch("api_gateway.public_info.records.fetch_one", new_callable=AsyncMock)
    async def test_get_record_public_estado_fuera_de_visible_states_devuelve_none(self, mock_fetch_one):
        from api_gateway.public_info.records import get_record_public

        mock_fetch_one.return_value = {
            "id": "rec-1", "record_number": "RLM-2026-001-MT-ARQ", "display_name": "x",
            "state": "Archivado", "data": {"dni": "1"},
            "family_id": "fam-1", "registry_code": "ARQ", "registry_name": "Archivo",
            "family_is_public": True, "public_config": {"fields": ["dni"], "visible_states": ["Activo"]},
        }

        result = await get_record_public(schema_name=TEST_SCHEMA, record_number="RLM-2026-001-MT-ARQ", muni="mt")
        assert result is None

    @pytest.mark.asyncio
    @patch("api_gateway.public_info.records.fetch_one", new_callable=AsyncMock)
    async def test_get_record_public_solo_expone_campos_whitelist(self, mock_fetch_one):
        from api_gateway.public_info.records import get_record_public

        mock_fetch_one.return_value = {
            "id": "rec-1", "record_number": "RLM-2026-001-MT-ARQ", "display_name": "x",
            "state": "Activo", "data": {"dni": "12345678", "cbu": "SECRETO", "razon_social": "Acme"},
            "family_id": "fam-1", "registry_code": "ARQ", "registry_name": "Archivo",
            "family_is_public": True,
            "public_config": {"fields": ["dni", "razon_social"], "visible_states": ["Activo"]},
        }

        result = await get_record_public(schema_name=TEST_SCHEMA, record_number="RLM-2026-001-MT-ARQ", muni="mt")
        assert result is not None
        assert result["fields"] == {"dni": "12345678", "razon_social": "Acme"}
        assert "cbu" not in result["fields"]
        assert "data" not in result

    @pytest.mark.asyncio
    @patch("api_gateway.public_info.records.fetch_one", new_callable=AsyncMock)
    async def test_get_record_public_no_expone_documentos_expedientes_relacionados_si_config_apagada(self, mock_fetch_one):
        from api_gateway.public_info.records import get_record_public

        mock_fetch_one.return_value = {
            "id": "rec-1", "record_number": "RLM-2026-001-MT-ARQ", "display_name": "x",
            "state": "Activo", "data": {"dni": "1"},
            "family_id": "fam-1", "registry_code": "ARQ", "registry_name": "Archivo",
            "family_is_public": True,
            "public_config": {
                "fields": ["dni"], "visible_states": ["Activo"],
                "show_documents": False, "show_cases": False, "show_related_records": False,
            },
        }

        result = await get_record_public(schema_name=TEST_SCHEMA, record_number="RLM-2026-001-MT-ARQ", muni="mt")
        assert "documents" not in result
        assert "cases" not in result
        assert "related_records" not in result


class TestRelatedRecordsFamilyGate:
    @pytest.mark.asyncio
    @patch("api_gateway.public_info.records.fetch_all", new_callable=AsyncMock)
    async def test_relacionado_de_familia_privada_no_lleva_numero(self, mock_fetch_all):
        from api_gateway.public_info.records import _get_related_records_public

        mock_fetch_all.return_value = [
            {"record_number": "RLM-2026-002-MT-LUM", "display_name": "Luminaria X", "state": "Activo",
             "resume": "Resumen luminaria", "target_is_public": False, "target_visible_states": ["Activo"]},
            {"record_number": "RLM-2026-003-MT-ARQ", "display_name": "Archivo Y", "state": "Activo",
             "resume": "Resumen archivo", "target_is_public": True, "target_visible_states": ["Activo"]},
        ]

        result = await _get_related_records_public(record_id="rec-1", schema_name=TEST_SCHEMA)

        privado = next(r for r in result if r["display_name"] == "Luminaria X")
        publico = next(r for r in result if r["display_name"] == "Archivo Y")
        assert privado["record_number"] is None
        assert privado["linked"] is False
        assert publico["record_number"] == "RLM-2026-003-MT-ARQ"
        assert publico["linked"] is True
        assert privado["resume"] is None
        assert publico["resume"] == "Resumen archivo"

    @pytest.mark.asyncio
    @patch("api_gateway.public_info.records.fetch_all", new_callable=AsyncMock)
    async def test_relacionado_publico_pero_en_estado_no_visible_no_lleva_numero(self, mock_fetch_all):
        from api_gateway.public_info.records import _get_related_records_public

        mock_fetch_all.return_value = [
            {"record_number": "RLM-2026-004-MT-ARQ", "display_name": "Archivo Archivado", "state": "Archivado",
             "resume": "Resumen archivado", "target_is_public": True, "target_visible_states": ["Activo"]},
        ]

        result = await _get_related_records_public(record_id="rec-1", schema_name=TEST_SCHEMA)

        archivado = result[0]
        assert archivado["record_number"] is None
        assert archivado["linked"] is False
        assert archivado["resume"] is None

    def test_queries_publicas_filtran_visible_states_en_records_agg(self):
        from api_gateway.public_info.queries import SEMANTIC_SEARCH_PUBLIC_SQL, LOOKUP_DOCUMENT_PUBLIC_SQL
        for sql in (SEMANTIC_SEARCH_PUBLIC_SQL, LOOKUP_DOCUMENT_PUBLIC_SQL):
            assert "visible_states" in sql
            assert "rf.public_config -> 'visible_states'" in sql or "rf.public_config->'visible_states'" in sql


class TestListRecordsPublicNoFullJsonSearch:
    @pytest.mark.asyncio
    @patch("api_gateway.public_info.records.get_public_families", new_callable=AsyncMock)
    @patch("api_gateway.public_info.records.fetch_all", new_callable=AsyncMock)
    @patch("api_gateway.public_info.records.fetch_one", new_callable=AsyncMock)
    async def test_search_no_usa_data_text_ilike(self, mock_fetch_one, mock_fetch_all, mock_families):
        from api_gateway.public_info.records import list_records_public

        mock_families.return_value = [
            {"id": "fam-1", "code": "ARQ", "name": "Archivo", "description": None,
             "public_config": {"fields": ["dni"], "visible_states": ["Activo"]}},
        ]
        mock_fetch_one.return_value = {"total": 0}
        mock_fetch_all.return_value = []

        await list_records_public(schema_name=TEST_SCHEMA, search="12345678", page=1, page_size=20)

        count_sql = mock_fetch_one.call_args[0][0]
        list_sql = mock_fetch_all.call_args[0][0]
        assert "data::text" not in count_sql
        assert "data::text" not in list_sql
        assert "r.data->>'dni'" in count_sql

    @pytest.mark.asyncio
    @patch("api_gateway.public_info.records.get_public_families", new_callable=AsyncMock)
    async def test_page_size_topeado_a_25(self, mock_families):
        from api_gateway.public_info.records import list_records_public, MAX_PAGE_SIZE_PUBLIC

        mock_families.return_value = []
        result = await list_records_public(schema_name=TEST_SCHEMA, page_size=1000)
        assert result["page_size"] == MAX_PAGE_SIZE_PUBLIC == 25


class TestVisibleStatesEmptyVsAbsent:
    def test_resolve_visible_states_ausente_usa_default(self):
        from api_gateway.public_info.records import _resolve_visible_states, DEFAULT_VISIBLE_STATES

        assert _resolve_visible_states({}) == DEFAULT_VISIBLE_STATES

    def test_resolve_visible_states_null_explicito_usa_default(self):
        from api_gateway.public_info.records import _resolve_visible_states, DEFAULT_VISIBLE_STATES

        assert _resolve_visible_states({"visible_states": None}) == DEFAULT_VISIBLE_STATES

    def test_resolve_visible_states_lista_vacia_NO_cae_al_default(self):
        from api_gateway.public_info.records import _resolve_visible_states

        assert _resolve_visible_states({"visible_states": []}) == []

    def test_resolve_visible_states_con_valores_se_respeta(self):
        from api_gateway.public_info.records import _resolve_visible_states

        assert _resolve_visible_states({"visible_states": ["Activo", "Cerrado"]}) == ["Activo", "Cerrado"]

    @pytest.mark.asyncio
    @patch("api_gateway.public_info.records.get_public_families", new_callable=AsyncMock)
    @patch("api_gateway.public_info.records.fetch_all", new_callable=AsyncMock)
    @patch("api_gateway.public_info.records.fetch_one", new_callable=AsyncMock)
    async def test_list_records_public_visible_states_vacio_arma_any_vacio(
        self, mock_fetch_one, mock_fetch_all, mock_families
    ):
        from api_gateway.public_info.records import list_records_public

        mock_families.return_value = [
            {"id": "fam-1", "code": "ARQ", "name": "Archivo", "description": None,
             "public_config": {"fields": ["dni"], "visible_states": []}},
        ]
        mock_fetch_one.return_value = {"total": 0}
        mock_fetch_all.return_value = []

        await list_records_public(schema_name=TEST_SCHEMA, page=1, page_size=20)

        count_params = mock_fetch_one.call_args[0][1:]
        assert [] in count_params
        assert ["Activo"] not in count_params

    @pytest.mark.asyncio
    @patch("api_gateway.public_info.records.fetch_one", new_callable=AsyncMock)
    async def test_get_record_public_visible_states_vacio_no_muestra_ni_activos(self, mock_fetch_one):
        from api_gateway.public_info.records import get_record_public

        mock_fetch_one.return_value = {
            "id": "rec-1", "record_number": "RLM-2026-001-MT-ARQ", "display_name": "x",
            "state": "Activo", "data": {"dni": "1"},
            "family_id": "fam-1", "registry_code": "ARQ", "registry_name": "Archivo",
            "family_is_public": True, "public_config": {"fields": ["dni"], "visible_states": []},
        }

        result = await get_record_public(schema_name=TEST_SCHEMA, record_number="RLM-2026-001-MT-ARQ", muni="mt")
        assert result is None

    @pytest.mark.asyncio
    @patch("api_gateway.public_info.records.fetch_one", new_callable=AsyncMock)
    async def test_get_record_public_visible_states_ausente_usa_default_activo(self, mock_fetch_one):
        from api_gateway.public_info.records import get_record_public

        mock_fetch_one.return_value = {
            "id": "rec-1", "record_number": "RLM-2026-001-MT-ARQ", "display_name": "x",
            "state": "Activo", "data": {"dni": "1"},
            "family_id": "fam-1", "registry_code": "ARQ", "registry_name": "Archivo",
            "family_is_public": True, "public_config": {"fields": ["dni"]},
        }

        result = await get_record_public(schema_name=TEST_SCHEMA, record_number="RLM-2026-001-MT-ARQ", muni="mt")
        assert result is not None
        assert result["state"] == "Activo"

    @pytest.mark.asyncio
    @patch("api_gateway.public_info.records.fetch_all", new_callable=AsyncMock)
    async def test_relacionado_con_target_visible_states_vacio_no_linkea(self, mock_fetch_all):
        from api_gateway.public_info.records import _get_related_records_public

        mock_fetch_all.return_value = [
            {"record_number": "RLM-2026-005-MT-ARQ", "display_name": "Archivo Z", "state": "Activo",
             "resume": "Resumen z", "target_is_public": True, "target_visible_states": []},
        ]

        result = await _get_related_records_public(record_id="rec-1", schema_name=TEST_SCHEMA)

        item = result[0]
        assert item["record_number"] is None
        assert item["linked"] is False
        assert item["resume"] is None


class TestAiQuotaFailModes:
    def test_sin_redis_no_hay_cupo(self):
        from api_gateway.public_info import quota as ai_quota

        with patch("api_gateway.public_info.quota.get_redis", return_value=None):
            assert ai_quota.check_and_consume_quota() is False

    def test_sin_redis_cache_devuelve_none_no_rompe(self):
        from api_gateway.public_info import quota as ai_quota

        with patch("api_gateway.public_info.quota.get_redis", return_value=None):
            assert ai_quota.get_cached_embedding("ordenanza 100", schema_name=TEST_SCHEMA) is None
            ai_quota.set_cached_embedding("ordenanza 100", [0.1, 0.2], schema_name=TEST_SCHEMA)

    def test_redis_caido_en_runtime_degrada_sin_excepcion(self):
        from api_gateway.public_info import quota as ai_quota

        broken_client = MagicMock()
        broken_client.incr.side_effect = Exception("Connection refused")
        with patch("api_gateway.public_info.quota.get_redis", return_value=broken_client):
            assert ai_quota.check_and_consume_quota() is False

    def test_cupo_agotado_devuelve_false(self):
        from api_gateway.public_info import quota as ai_quota

        client = MagicMock()
        client.incr.return_value = ai_quota.PUBLIC_AI_DAILY_QUOTA + 1
        with patch("api_gateway.public_info.quota.get_redis", return_value=client):
            assert ai_quota.check_and_consume_quota() is False


class TestEmbeddingCacheCrossTenantIsolation:

    def test_misma_query_distinto_tenant_usa_keys_distintas(self):
        from api_gateway.public_info import quota as ai_quota

        client = MagicMock()
        client.get.return_value = None
        with patch("api_gateway.public_info.quota.get_redis", return_value=client):
            ai_quota.get_cached_embedding("ordenanza 100", schema_name="100_mt")
            ai_quota.get_cached_embedding("ordenanza 100", schema_name="200_test")

        keys_used = [call.args[0] for call in client.get.call_args_list]
        assert keys_used[0] != keys_used[1]
        assert "100_mt" in keys_used[0]
        assert "200_test" in keys_used[1]

    def test_set_embedding_incluye_schema_en_la_key(self):
        from api_gateway.public_info import quota as ai_quota

        client = MagicMock()
        with patch("api_gateway.public_info.quota.get_redis", return_value=client):
            ai_quota.set_cached_embedding("ordenanza 100", [0.1, 0.2], schema_name="100_mt")

        key_used = client.setex.call_args[0][0]
        assert "100_mt" in key_used


class TestSearchLookupEscaping:

    def test_percent_y_underscore_se_escapan(self):
        from api_gateway.public_info.search import _build_lookup_params_public

        params = _build_lookup_params_public("100%_test", limit=10)
        assert any("\\%" in p for p in params[:3])
        assert any("\\_" in p for p in params[:3])

    def test_backslash_se_escapa(self):
        from api_gateway.public_info.search import _build_lookup_params_public

        params = _build_lookup_params_public("a\\b", limit=10)
        assert any("\\\\" in p for p in params[:3])


class TestSearchDoesNotLogRawQuery:

    @pytest.mark.asyncio
    @patch("api_gateway.public_info.search._search_records", new_callable=AsyncMock)
    @patch("api_gateway.public_info.search._search_documents_lookup", new_callable=AsyncMock)
    @patch("api_gateway.public_info.search.get_bucket_publico", new_callable=AsyncMock)
    @patch("api_gateway.public_info.search.classify_intent")
    @patch("api_gateway.public_info.search.logger")
    async def test_log_no_incluye_dni_de_la_query(
        self, mock_logger, mock_classify, mock_bucket, mock_lookup, mock_records
    ):
        from api_gateway.public_info.search import public_search

        mock_classify.return_value = "lookup"
        mock_bucket.return_value = None
        mock_lookup.return_value = []
        mock_records.return_value = []

        dni_query = "38294712-9 datos personales secretos"
        await public_search(dni_query, schema_name=TEST_SCHEMA, muni="mt")

        logged_text = " ".join(str(call) for call in mock_logger.info.call_args_list)
        assert dni_query not in logged_text
        assert "38294712" not in logged_text


class TestEmbedConcurrencyGuard:

    def test_semaforo_existe_con_limite_razonable(self):
        from api_gateway.public_info.search import _embed_semaphore, _EMBED_CONCURRENCY_LIMIT
        assert isinstance(_embed_semaphore, asyncio.Semaphore)
        assert 1 <= _EMBED_CONCURRENCY_LIMIT <= 20


class TestRegistriesPublicShape:
    @pytest.mark.asyncio
    @patch("api_gateway.public_info.records.get_public_families", new_callable=AsyncMock)
    async def test_list_registries_shape(self, mock_families):
        from api_gateway.public_info.records import list_registries_public

        mock_families.return_value = [
            {"id": "fam-1", "code": "ARQ", "name": "Archivo", "description": "desc",
             "public_config": {"fields": ["dni", "razon_social"]}},
        ]

        result = await list_registries_public(schema_name=TEST_SCHEMA)
        assert result["total"] == 1
        reg = result["registries"][0]
        assert set(reg.keys()) == {"code", "name", "description", "fields"}
        assert reg["fields"] == ["dni", "razon_social"]


VALID_UUID = "11111111-1111-1111-1111-111111111111"


class TestDocumentContentRouteAndExport:
    def test_handler_exportado(self):
        from api_gateway.rest_api_public import api_public_get_document_content
        assert callable(api_public_get_document_content)
        assert inspect.iscoroutinefunction(api_public_get_document_content)

    def test_ruta_registrada(self):
        from api_gateway.http_server import routes as gateway_routes
        paths = {getattr(r, "path", None) for r in gateway_routes}
        assert "/api/v1/public/{muni}/documents/{document_id}/content" in paths


class TestDocumentContentPublicSqlGate:

    def test_sql_tiene_gate_completo(self):
        from api_gateway.public_info.queries import DOCUMENT_CONTENT_PUBLIC_SQL
        sql = DOCUMENT_CONTENT_PUBLIC_SQL
        assert "od.signed_at IS NOT NULL" in sql
        assert "dt.visibility = 'publico'" in sql
        assert "NOT dt.is_reserved" in sql
        assert "dt.acronym = ANY($2::text[])" in sql

    def test_sql_no_depende_de_user_id(self):
        from api_gateway.public_info.queries import DOCUMENT_CONTENT_PUBLIC_SQL
        for needle in ("user_sectors", "case_movements", "case_responsibles"):
            assert needle not in DOCUMENT_CONTENT_PUBLIC_SQL


class TestGetDocumentContentPublic:
    @pytest.mark.asyncio
    @patch("api_gateway.public_info.documents.fetch_one", new_callable=AsyncMock)
    async def test_doc_publico_devuelve_contenido(self, mock_fetch_one):
        from api_gateway.public_info.documents import get_document_content_public

        mock_fetch_one.return_value = {
            "id": VALID_UUID, "official_number": "PLORD-2026-001-MT-HCD",
            "reference": "Ordenanza publica", "content": {"html": "<p>Texto completo</p>"},
            "signed_at": datetime(2026, 7, 20, tzinfo=timezone.utc),
            "document_type_name": "Ordenanza", "document_type_acronym": "ORD",
        }

        result = await get_document_content_public(schema_name=TEST_SCHEMA, document_id=VALID_UUID)
        assert result["document_id"] == VALID_UUID
        assert result["official_number"] == "PLORD-2026-001-MT-HCD"
        assert result["content"] == {"html": "<p>Texto completo</p>", "format": "html"}
        assert result["document_type"] == {"name": "Ordenanza", "acronym": "ORD"}
        assert result["signed_at"] == "2026-07-20T00:00:00+00:00"

    @pytest.mark.asyncio
    @patch("api_gateway.public_info.documents.fetch_one", new_callable=AsyncMock)
    async def test_no_encontrado_o_no_publico_devuelve_none(self, mock_fetch_one):
        from api_gateway.public_info.documents import get_document_content_public

        mock_fetch_one.return_value = None
        result = await get_document_content_public(schema_name=TEST_SCHEMA, document_id=VALID_UUID)
        assert result is None

    @pytest.mark.asyncio
    @patch("api_gateway.public_info.documents.fetch_one", new_callable=AsyncMock)
    async def test_content_null_devuelve_html_vacio(self, mock_fetch_one):
        from api_gateway.public_info.documents import get_document_content_public

        mock_fetch_one.return_value = {
            "id": VALID_UUID, "official_number": "PLORD-2026-001-MT-HCD",
            "reference": "x", "content": None,
            "signed_at": datetime(2026, 7, 20, tzinfo=timezone.utc),
            "document_type_name": "Ordenanza", "document_type_acronym": "ORD",
        }
        result = await get_document_content_public(schema_name=TEST_SCHEMA, document_id=VALID_UUID)
        assert result["content"]["html"] == ""

    @pytest.mark.asyncio
    @patch("api_gateway.public_info.documents.fetch_one", new_callable=AsyncMock)
    async def test_scopea_por_schema_name_del_auth(self, mock_fetch_one):
        from api_gateway.public_info.documents import get_document_content_public

        mock_fetch_one.return_value = None
        await get_document_content_public(schema_name=TEST_SCHEMA, document_id=VALID_UUID)
        assert mock_fetch_one.call_args.kwargs["schema_name"] == TEST_SCHEMA
        assert mock_fetch_one.call_args[0][1] == VALID_UUID

    @pytest.mark.asyncio
    @patch("api_gateway.public_info.documents.fetch_one", new_callable=AsyncMock)
    async def test_doc_ffcc_renderiza_tabla(self, mock_fetch_one):
        from api_gateway.public_info.documents import get_document_content_public

        mock_fetch_one.return_value = {
            "id": VALID_UUID, "official_number": "PLFFCC-2026-001-MT-HCD",
            "reference": "FFCC", "content": {"schema": [], "data": {}},
            "signed_at": datetime(2026, 7, 20, tzinfo=timezone.utc),
            "document_type_name": "FFCC", "document_type_acronym": "FFCC",
        }
        with patch("services.documents.ffcc_renderer.ffcc_to_html", return_value="<table>ok</table>") as mock_ffcc:
            result = await get_document_content_public(schema_name=TEST_SCHEMA, document_id=VALID_UUID)
        mock_ffcc.assert_called_once()
        assert result["content"]["html"] == "<table>ok</table>"


class TestDocumentContentHandler:
    @pytest.mark.asyncio
    @patch("api_gateway.rest_api_public.check_public_rate_limit")
    @patch("api_gateway.rest_api_public.get_document_content_public", new_callable=AsyncMock)
    @patch("api_gateway.rest_api_public.validate_public_api_key", new_callable=AsyncMock)
    async def test_doc_publico_responde_200_con_cache_y_vary(self, mock_validate, mock_get, mock_rl):
        from api_gateway.rest_api_public import api_public_get_document_content

        mock_validate.return_value = TEST_SCHEMA
        mock_get.return_value = {"document_id": VALID_UUID, "content": {"html": "<p>x</p>", "format": "html"}}

        app = _build_test_app(api_public_get_document_content, "/public/{muni}/documents/{document_id}/content")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(f"/public/{TEST_MUNI}/documents/{VALID_UUID}/content", headers={"X-API-Key": "sk-gdi-ok"})

        assert resp.status_code == 200
        assert resp.json()["content"]["html"] == "<p>x</p>"
        assert resp.headers["cache-control"] == "private, max-age=60"
        assert resp.headers["vary"] == "X-API-Key"

    @pytest.mark.asyncio
    @patch("api_gateway.rest_api_public.check_public_rate_limit")
    @patch("api_gateway.rest_api_public.get_document_content_public", new_callable=AsyncMock)
    @patch("api_gateway.rest_api_public.validate_public_api_key", new_callable=AsyncMock)
    async def test_doc_no_publico_responde_404(self, mock_validate, mock_get, mock_rl):
        from api_gateway.rest_api_public import api_public_get_document_content

        mock_validate.return_value = TEST_SCHEMA
        mock_get.return_value = None

        app = _build_test_app(api_public_get_document_content, "/public/{muni}/documents/{document_id}/content")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(f"/public/{TEST_MUNI}/documents/{VALID_UUID}/content", headers={"X-API-Key": "sk-gdi-ok"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    @patch("api_gateway.rest_api_public.check_public_rate_limit")
    @patch("api_gateway.rest_api_public.get_document_content_public", new_callable=AsyncMock)
    @patch("api_gateway.rest_api_public.validate_public_api_key", new_callable=AsyncMock)
    async def test_uuid_mal_formado_responde_404_no_500(self, mock_validate, mock_get, mock_rl):
        from api_gateway.rest_api_public import api_public_get_document_content

        mock_validate.return_value = TEST_SCHEMA

        app = _build_test_app(api_public_get_document_content, "/public/{muni}/documents/{document_id}/content")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(f"/public/{TEST_MUNI}/documents/no-es-un-uuid/content", headers={"X-API-Key": "sk-gdi-ok"})

        assert resp.status_code == 404
        mock_get.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("api_gateway.rest_api_public.check_public_rate_limit")
    @patch("api_gateway.rest_api_public.validate_public_api_key", new_callable=AsyncMock)
    async def test_sin_api_key_responde_401(self, mock_validate, mock_rl):
        from api_gateway.rest_api_public import api_public_get_document_content
        from api_gateway.auth_rest import PublicAuthError

        mock_validate.side_effect = PublicAuthError("X-API-Key requerido", status_code=401)
        app = _build_test_app(api_public_get_document_content, "/public/{muni}/documents/{document_id}/content")
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(f"/public/{TEST_MUNI}/documents/{VALID_UUID}/content")
        assert resp.status_code == 401


class TestDocumentUuidExposedOnlyForPublicDocs:

    def test_linked_documents_sql_usa_case_when_para_uuid(self):
        import inspect as _inspect
        from api_gateway.public_info import records
        src = _inspect.getsource(records._get_linked_documents_public)
        assert "CASE WHEN dt.visibility = 'publico' THEN od.id::text ELSE NULL END" in src

    @pytest.mark.asyncio
    @patch("api_gateway.public_info.records.get_bucket_publico", new_callable=AsyncMock)
    @patch("api_gateway.public_info.records.fetch_all", new_callable=AsyncMock)
    async def test_doc_publico_trae_uuid_doc_normal_no(self, mock_fetch_all, mock_bucket):
        from api_gateway.public_info.records import _get_linked_documents_public

        mock_bucket.return_value = "tenant-mt-publico"
        mock_fetch_all.return_value = [
            {"official_number": "PLORD-2026-001-MT-HCD", "reference": "Pub", "resume": "r",
             "visibility": "publico", "document_id": VALID_UUID},
            {"official_number": "MEMO-2026-002-MT-HCD", "reference": "Interno", "resume": "r",
             "visibility": "interno", "document_id": None},
        ]

        docs = await _get_linked_documents_public(record_id="rec-1", schema_name=TEST_SCHEMA, muni="mt")
        pub = next(d for d in docs if d["official_number"] == "PLORD-2026-001-MT-HCD")
        normal = next(d for d in docs if d["official_number"] == "MEMO-2026-002-MT-HCD")
        assert pub["document_id"] == VALID_UUID
        assert normal["document_id"] is None


class TestContentGateNotBypassed:

    def test_public_info_no_importa_la_funcion_privada_sin_gate(self):
        import api_gateway.public_info.documents as pub_docs
        import inspect as _inspect
        src = _inspect.getsource(pub_docs)
        assert "import get_official_document_content" not in src
        assert "get_official_document_content(" not in src
        assert "extract_html_from_content" in src

    def test_no_esta_importada_en_runtime(self):
        import api_gateway.public_info.documents as pub_docs
        assert not hasattr(pub_docs, "get_official_document_content")

    def test_search_no_expone_document_id(self):
        from api_gateway.public_info import search
        row = {
            "official_number": "PLORD-2026-001-MT-HCD",
            "reference": "Ordenanza",
            "document_type": "Ordenanza",
            "document_id": "11111111-1111-1111-1111-111111111111",
        }
        out = search._format_doc_row(row, bucket_publico="tenant-mt-publico", muni="mt")
        assert "document_id" not in out
        assert out["pdf_url"] == "https://public.your-domain.com/mt/11111111-1111-1111-1111-111111111111.pdf"


class TestExtractHtmlFromContent:
    def test_html_clasico(self):
        from services.documents.retrieval.content import extract_html_from_content
        assert extract_html_from_content({"html": "<p>a</p>"}) == "<p>a</p>"

    def test_detalle_compat(self):
        from services.documents.retrieval.content import extract_html_from_content
        assert extract_html_from_content({"detalle": "<p>b</p>"}) == "<p>b</p>"

    def test_none_o_no_dict_devuelve_vacio(self):
        from services.documents.retrieval.content import extract_html_from_content
        assert extract_html_from_content(None) == ""
        assert extract_html_from_content("string") == ""

    def test_ffcc_usa_renderer(self):
        from services.documents.retrieval.content import extract_html_from_content
        with patch("services.documents.ffcc_renderer.ffcc_to_html", return_value="<table></table>") as mock_ffcc:
            assert extract_html_from_content({"schema": [], "data": {}}) == "<table></table>"
        mock_ffcc.assert_called_once()
