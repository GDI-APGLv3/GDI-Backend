import pytest
import pytest_asyncio

from tests._home_parity_helpers import leer_estable

SCHEMA = "100_test"
TEST_USER_IDS = [
    "a1000000-0000-0000-0000-00000000000a",
    "a1000000-0000-0000-0000-000000000002",
]


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


class TestHomeUnassignedSequentialParity:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("user_id", TEST_USER_IDS)
    async def test_mismo_unowned_y_tasks(self, db_ready, user_id):
        from services.home.service import get_home_unassigned

        sequential, parallel = await leer_estable(
            lambda paralelo: get_home_unassigned(
                user_id, limit=10, schema_name=SCHEMA, _force_parallel_fetch=paralelo),
            etiqueta=f"unassigned user={user_id}",
        )

        assert sequential == parallel, (
            f"Paridad rota para user={user_id}:\n"
            f"  secuencial={sequential}\n"
            f"  paralelo=  {parallel}"
        )
