import pytest
import pytest_asyncio

from tests._home_parity_helpers import leer_estable

SCHEMA = "100_test"
SEARCH_QUERY = "ar"


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
        pytest.skip("Sin conexión a BD (DB_HOST/tunnel no disponible) — test de paridad skip-sin-BD")

    yield

    try:
        await close_pool()
    except Exception:
        pass


async def _search_con_o_sin_conn(con_conn: bool):
    from database import get_conn
    from services.users.search import search_users_for_autocomplete

    if con_conn:
        async with get_conn(schema_name=SCHEMA) as conn:
            return await search_users_for_autocomplete(search_query=SEARCH_QUERY, schema_name=SCHEMA, conn=conn)
    return await search_users_for_autocomplete(search_query=SEARCH_QUERY, schema_name=SCHEMA)


class TestSearchUsersConnParity:

    @pytest.mark.asyncio
    async def test_conn_reused_matches_conn_none(self, db_ready):
        viejo, nuevo = await leer_estable(
            lambda con_conn: _search_con_o_sin_conn(con_conn),
            etiqueta=f"search_users_for_autocomplete query={SEARCH_QUERY!r}",
        )

        assert nuevo["total_found"] == viejo["total_found"]

        nuevo_ids = [u["user_id"] for u in nuevo["users"]]
        viejo_ids = [u["user_id"] for u in viejo["users"]]
        assert nuevo_ids == viejo_ids, "Paridad de ORDEN de usuarios rota"
        assert nuevo["users"] == viejo["users"], "Contenido de usuarios distinto"
