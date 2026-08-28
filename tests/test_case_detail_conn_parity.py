import pytest
import pytest_asyncio

from tests._home_parity_helpers import leer_estable

SCHEMA = "100_test"
TEST_USER_ID = "a1000000-0000-0000-0000-000000000001"
REAL_CASE_ID = "5130f93f-28c1-4ea3-8830-19e6822ea630"


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


async def _get_case_detail_con_o_sin_conn(con_conn: bool):
    from database import get_conn
    from services.cases.queries import get_case_detail

    if con_conn:
        async with get_conn(schema_name=SCHEMA) as conn:
            return await get_case_detail(REAL_CASE_ID, TEST_USER_ID, schema_name=SCHEMA, conn=conn)
    return await get_case_detail(REAL_CASE_ID, TEST_USER_ID, schema_name=SCHEMA)


class TestCaseDetailConnParity:

    @pytest.mark.asyncio
    async def test_conn_reused_matches_conn_none(self, db_ready):
        viejo, nuevo = await leer_estable(
            lambda con_conn: _get_case_detail_con_o_sin_conn(con_conn),
            etiqueta=f"get_case_detail case={REAL_CASE_ID} user={TEST_USER_ID}",
        )

        assert nuevo is not None, "get_case_detail devolvió None con conn reusado (¿REAL_CASE_ID ya no es visible para TEST_USER_ID?)"
        assert viejo is not None, "get_case_detail devolvió None en el camino viejo"

        assert nuevo["id"] == viejo["id"]
        assert nuevo["case_number"] == viejo["case_number"]
        assert nuevo["reference"] == viejo["reference"]
        assert nuevo["ai_summary"] == viejo["ai_summary"]
        assert nuevo["template"] == viejo["template"]
        assert nuevo["access_reason"] == viejo["access_reason"]
        assert nuevo["admin_sector"] == viejo["admin_sector"]
        assert nuevo["assigned_sectors"] == viejo["assigned_sectors"]
        assert nuevo["is_favorite"] == viejo["is_favorite"]
