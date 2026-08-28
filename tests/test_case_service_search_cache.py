import pytest
import pytest_asyncio

SCHEMA = "100_test"
TEST_USER_ID = "a1000000-0000-0000-0000-000000000001"


class FakeRedis:

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value
        return True

    def delete(self, *keys):
        deleted = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                deleted += 1
        return deleted

    def ping(self):
        return True


@pytest.fixture
def fake_redis(monkeypatch):
    import services.cache as cache_module

    fr = FakeRedis()
    monkeypatch.setattr(cache_module, "redis_client", fr)
    monkeypatch.setattr(cache_module, "get_redis", lambda: fr)
    return fr


@pytest_asyncio.fixture
async def db_ready():
    import database as db_module
    from database import init_pool, close_pool, test_connection

    if db_module.pool is not None:
        try:
            await close_pool()
        except Exception:
            pass

    try:
        await init_pool()
        ok = await test_connection()
    except Exception:
        ok = False

    if not ok:
        pytest.skip("Sin conexión a BD (DB_HOST/tunnel no disponible)")

    yield

    try:
        await close_pool()
    except Exception:
        pass


class TestCasesCountCacheKeyUnit:

    def test_cache_key_deterministic_same_input(self):
        from services.cases.retrieval import _build_cases_count_cache_key

        params = (["sec-a", "sec-b"], "user-1", "%foo%")
        k1 = _build_cases_count_cache_key("100_test", None, params)
        k2 = _build_cases_count_cache_key("100_test", None, params)
        assert k1 == k2

    def test_cache_key_sensitive_to_user_id(self):
        from services.cases.retrieval import _build_cases_count_cache_key

        k_user_a = _build_cases_count_cache_key(
            "100_test", None, (["sec-a"], "user-A", "%foo%")
        )
        k_user_b = _build_cases_count_cache_key(
            "100_test", None, (["sec-a"], "user-B", "%foo%")
        )
        assert k_user_a != k_user_b

    def test_cache_key_sensitive_to_sector_ids(self):
        from services.cases.retrieval import _build_cases_count_cache_key

        k_sectors_a = _build_cases_count_cache_key(
            "100_test", None, (["sec-a"], "user-1", "%foo%")
        )
        k_sectors_b = _build_cases_count_cache_key(
            "100_test", None, (["sec-b"], "user-1", "%foo%")
        )
        assert k_sectors_a != k_sectors_b

    def test_cache_key_sensitive_to_view(self):
        from services.cases.retrieval import _build_cases_count_cache_key

        params = (["sec-a"], "user-1", "%foo%")
        k_none = _build_cases_count_cache_key("100_test", None, params)
        k_asignado = _build_cases_count_cache_key("100_test", "asignado", params)
        k_favoritos = _build_cases_count_cache_key("100_test", "favoritos", params)
        assert len({k_none, k_asignado, k_favoritos}) == 3

    def test_cache_key_sensitive_to_schema(self):
        from services.cases.retrieval import _build_cases_count_cache_key

        params = (["sec-a"], "user-1", "%foo%")
        k1 = _build_cases_count_cache_key("100_test", None, params)
        k2 = _build_cases_count_cache_key("200_other", None, params)
        assert k1 != k2

    def test_cache_key_sensitive_to_where_params(self):
        from services.cases.retrieval import _build_cases_count_cache_key

        k1 = _build_cases_count_cache_key(
            "100_test", None, (["sec-a"], "user-1", "%foo%")
        )
        k2 = _build_cases_count_cache_key(
            "100_test", None, (["sec-a"], "user-1", "%bar%")
        )
        assert k1 != k2

    def test_cache_ttl_reads_env_dynamically(self, monkeypatch):
        from services.cases.retrieval import _cases_count_cache_ttl

        monkeypatch.setenv("CASE_SEARCH_COUNT_CACHE_TTL", "45")
        assert _cases_count_cache_ttl() == 45

    def test_cache_ttl_zero_disables_via_env(self, monkeypatch):
        from services.cases.retrieval import _cases_count_cache_ttl

        monkeypatch.setenv("CASE_SEARCH_COUNT_CACHE_TTL", "0")
        assert _cases_count_cache_ttl() == 0

    def test_cache_ttl_default_when_unset(self, monkeypatch):
        from services.cases.retrieval import _cases_count_cache_ttl
        from config.constants import CACHE_TTL_COUNTS

        monkeypatch.delenv("CASE_SEARCH_COUNT_CACHE_TTL", raising=False)
        assert _cases_count_cache_ttl() == CACHE_TTL_COUNTS

    def test_cache_ttl_falls_back_on_garbage_value(self, monkeypatch):
        from services.cases.retrieval import _cases_count_cache_ttl
        from config.constants import CACHE_TTL_COUNTS

        monkeypatch.setenv("CASE_SEARCH_COUNT_CACHE_TTL", "no-es-un-numero")
        assert _cases_count_cache_ttl() == CACHE_TTL_COUNTS


class TestCasesCountCacheBehavior:

    @pytest.mark.asyncio
    async def test_second_call_within_ttl_hits_cache_and_matches_total(
        self, db_ready, fake_redis, monkeypatch
    ):
        from services.cases.retrieval import get_cases_by_user

        monkeypatch.setenv("CASE_SEARCH_COUNT_CACHE_TTL", "30")

        first = await get_cases_by_user(
            TEST_USER_ID, schema_name=SCHEMA, page=1, page_size=5,
        )
        assert len(fake_redis.store) == 1, "la primera llamada debe poblar el cache"

        second = await get_cases_by_user(
            TEST_USER_ID, schema_name=SCHEMA, page=1, page_size=5,
        )

        assert second["total"] == first["total"]
        assert [c["id"] for c in second["cases"]] == [c["id"] for c in first["cases"]]

    @pytest.mark.asyncio
    async def test_ttl_zero_never_populates_cache(self, db_ready, fake_redis, monkeypatch):
        from services.cases.retrieval import get_cases_by_user

        monkeypatch.setenv("CASE_SEARCH_COUNT_CACHE_TTL", "0")

        await get_cases_by_user(TEST_USER_ID, schema_name=SCHEMA, page=1, page_size=5)
        assert fake_redis.store == {}, "TTL=0 debe deshabilitar el cache por completo"

    @pytest.mark.asyncio
    async def test_stale_cached_total_falls_back_to_real_count(
        self, db_ready, fake_redis, monkeypatch
    ):
        import json
        from services.cases.retrieval import get_cases_by_user

        monkeypatch.setenv("CASE_SEARCH_COUNT_CACHE_TTL", "30")
        page_size = 5

        real = await get_cases_by_user(
            TEST_USER_ID, schema_name=SCHEMA, page=1, page_size=page_size,
        )
        real_total = real["total"]
        assert len(fake_redis.store) == 1
        cache_key = next(iter(fake_redis.store.keys()))

        poisoned_total = real_total + 1000
        fake_redis.store[cache_key] = json.dumps(poisoned_total)

        far_page = (poisoned_total // page_size) + 5
        result = await get_cases_by_user(
            TEST_USER_ID, schema_name=SCHEMA, page=far_page, page_size=page_size,
        )

        assert result["total"] == real_total, (
            "debe haber detectado la pagina vacia y recalculado el total real, "
            "no devolver el total envenenado"
        )
        assert result["cases"] == []

    @pytest.mark.asyncio
    async def test_different_views_do_not_share_cache_entry(
        self, db_ready, fake_redis, monkeypatch
    ):
        from services.cases.retrieval import get_cases_by_user

        monkeypatch.setenv("CASE_SEARCH_COUNT_CACHE_TTL", "30")

        await get_cases_by_user(
            TEST_USER_ID, schema_name=SCHEMA, page=1, page_size=5,
        )
        claves_tras_vista_default = set(fake_redis.store)

        await get_cases_by_user(
            TEST_USER_ID, schema_name=SCHEMA, page=1, page_size=5, view="favoritos",
        )
        claves_finales = set(fake_redis.store)

        assert claves_tras_vista_default <= claves_finales, (
            "la vista 'favoritos' piso la entrada de cache de la vista default"
        )
        assert len(claves_finales) == len(set(claves_finales))
        assert len(claves_finales) <= 2
