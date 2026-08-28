import pytest
import pytest_asyncio

SCHEMA = "100_test"
TEST_USER_ID = "a1000000-0000-0000-0000-00000000000a"


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


class TestDocNumberScope:
    @pytest.mark.asyncio
    async def test_doc_number_no_usa_el_cte_pesado(self, db_ready):
        import contextlib
        import services.document_service as dsvc

        vistas = []

        class Spy:
            def __init__(self, c):
                self._c = c

            async def fetch(self, q, *a, **k):
                vistas.append(q)
                return await self._c.fetch(q, *a, **k)

            async def fetchval(self, q, *a, **k):
                vistas.append(q)
                return await self._c.fetchval(q, *a, **k)

            def __getattr__(self, n):
                return getattr(self._c, n)

        original = dsvc.get_conn

        @contextlib.asynccontextmanager
        async def spy(*a, **k):
            async with original(*a, **k) as c:
                yield Spy(c)

        dsvc.get_conn = spy
        try:
            await dsvc.get_user_documents(
                TEST_USER_ID, schema_name=SCHEMA, page=1, page_size=20,
                doc_number="IF-2026",
            )
        finally:
            dsvc.get_conn = original

        filtrado = [
            q for q in vistas
            if q.strip().startswith("SELECT document_id") or q.strip().startswith("SELECT COUNT")
        ]
        assert filtrado, "no se capturo la query de filtrado"
        for q in filtrado:
            assert "content_html" not in q, (
                "con doc_number el FILTRADO no debe tocar content_html: es el HTML "
                "completo de cada documento visible y no se puede indexar"
            )
            assert "signers_names) LIKE" not in q, (
                "con doc_number el filtrado no debe buscar en signers_names: es un "
                "string_agg calculado al vuelo, no hay nada que indexar"
            )
        assert any("immutable_unaccent" in q for q in filtrado), (
            "el predicado tiene que usar immutable_unaccent para que apliquen los "
            "indices trigram de la migracion 114"
        )

    @pytest.mark.asyncio
    async def test_search_sigue_mirando_el_contenido(self, db_ready):
        import contextlib
        import services.document_service as dsvc

        vistas = []

        class Spy:
            def __init__(self, c):
                self._c = c

            async def fetch(self, q, *a, **k):
                vistas.append(q)
                return await self._c.fetch(q, *a, **k)

            async def fetchval(self, q, *a, **k):
                vistas.append(q)
                return await self._c.fetchval(q, *a, **k)

            def __getattr__(self, n):
                return getattr(self._c, n)

        original = dsvc.get_conn

        @contextlib.asynccontextmanager
        async def spy(*a, **k):
            async with original(*a, **k) as c:
                yield Spy(c)

        dsvc.get_conn = spy
        try:
            await dsvc.get_user_documents(
                TEST_USER_ID, schema_name=SCHEMA, page=1, page_size=20,
                search="informe",
            )
        finally:
            dsvc.get_conn = original

        queries = "\n".join(vistas)
        assert "content_html" in queries, (
            "`search` es la busqueda amplia de la API: si deja de mirar el contenido, "
            "las integraciones REST pierden resultados sin aviso"
        )

    @pytest.mark.asyncio
    async def test_vista_sector_ordena_solo_por_fecha(self, db_ready):
        import contextlib
        import services.document_service as dsvc

        vistas = []

        class Spy:
            def __init__(self, c):
                self._c = c

            async def fetch(self, q, *a, **k):
                vistas.append(q)
                return await self._c.fetch(q, *a, **k)

            async def fetchval(self, q, *a, **k):
                vistas.append(q)
                return await self._c.fetchval(q, *a, **k)

            def __getattr__(self, n):
                return getattr(self._c, n)

        original = dsvc.get_conn

        @contextlib.asynccontextmanager
        async def spy(*a, **k):
            async with original(*a, **k) as c:
                yield Spy(c)

        dsvc.get_conn = spy
        try:
            await dsvc.get_user_documents(
                TEST_USER_ID, schema_name=SCHEMA, page=1, page_size=20,
                sector_filter="sector",
            )
        finally:
            dsvc.get_conn = original

        ids_query = [q for q in vistas if q.strip().startswith("SELECT document_id")]
        assert ids_query, "no se capturo la query de ids"
        assert "usuario_es_firmante = true" not in ids_query[0], (
            "la vista de sector no debe evaluar la prioridad de firma: es imposible ahi"
        )
