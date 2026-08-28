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


def _misma_forma(a: dict, b: dict) -> bool:
    try:
        _assert_same_shape(a, b, "control de estabilidad")
        return True
    except AssertionError:
        return False


def _assert_same_shape(sequential: dict, parallel: dict, label: str):
    assert sequential["scope"] == parallel["scope"], label

    seq_resp_keys = [r["key"] for r in sequential["responsible"]]
    par_resp_keys = [r["key"] for r in parallel["responsible"]]
    assert seq_resp_keys == par_resp_keys, f"{label}: paridad de 'responsible' rota"
    assert sequential["responsible"] == parallel["responsible"], f"{label}: contenido de 'responsible' distinto"

    seq_mention_keys = [m["key"] for m in sequential["mention"]]
    par_mention_keys = [m["key"] for m in parallel["mention"]]
    assert seq_mention_keys == par_mention_keys, f"{label}: paridad de 'mention' rota"
    assert sequential["mention"] == parallel["mention"], f"{label}: contenido de 'mention' distinto"

    seq_mov = sequential["case_movements"]
    par_mov = parallel["case_movements"]
    seq_ids = [i["case_id"] for i in seq_mov["items"]]
    par_ids = [i["case_id"] for i in par_mov["items"]]
    assert seq_ids == par_ids, f"{label}: paridad de ORDEN en case_movements rota"
    assert seq_mov["items"] == par_mov["items"], f"{label}: contenido de case_movements distinto"
    assert seq_mov["next_cursor"] == par_mov["next_cursor"], f"{label}: next_cursor distinto"


class TestHomeCasesSequentialParity:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("user_id", TEST_USER_IDS)
    @pytest.mark.parametrize("scope", ["mine", "all"])
    async def test_first_page_matches(self, db_ready, user_id, scope):
        from services.home.service import get_home_cases

        sequential, parallel = await leer_estable(
            lambda paralelo: get_home_cases(
                user_id, scope, limit=10, cursor=None, schema_name=SCHEMA,
                _force_parallel_fetch=paralelo),
            iguales=_misma_forma,
            etiqueta=f"cases user={user_id} scope={scope} pagina=1",
        )
        _assert_same_shape(sequential, parallel, f"user={user_id} scope={scope} pagina=1")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("user_id", TEST_USER_IDS)
    async def test_second_page_with_cursor_matches(self, db_ready, user_id):
        from services.home.service import get_home_cases

        scope = "all"
        first_page = await get_home_cases(
            user_id, scope, limit=2, cursor=None, schema_name=SCHEMA
        )
        cursor = first_page["case_movements"]["next_cursor"]
        if not cursor:
            pytest.skip(
                f"user={user_id} no tiene suficientes movimientos para una segunda página "
                "(next_cursor vacío) — no hay nada que comparar acá"
            )

        sequential, parallel = await leer_estable(
            lambda paralelo: get_home_cases(
                user_id, scope, limit=2, cursor=cursor, schema_name=SCHEMA,
                _force_parallel_fetch=paralelo),
            iguales=_misma_forma,
            etiqueta=f"cases user={user_id} scope={scope} pagina=2",
        )

        assert sequential["responsible"] == [] == parallel["responsible"]
        assert sequential["mention"] == [] == parallel["mention"]
        _assert_same_shape(sequential, parallel, f"user={user_id} scope={scope} pagina=2 (con cursor)")
