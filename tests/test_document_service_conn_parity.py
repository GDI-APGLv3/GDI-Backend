import pytest
import pytest_asyncio

from tests._home_parity_helpers import leer_estable

SCHEMA = "100_test"
TEST_USER_ID = "a1000000-0000-0000-0000-000000000001"
DOC_TYPE_ACRONYM = "IF"

FILTER_MATRIX = [
    {},
    {"status_filter": "Firmado"},
    {"sector_filter": "mine"},
    {"sector_filter": "sector"},
    {"document_type": DOC_TYPE_ACRONYM, "sector_filter": "sector"},
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


class TestDocumentServiceConnParity:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("filters", FILTER_MATRIX, ids=[repr(f) for f in FILTER_MATRIX])
    async def test_one_acquire_matches_two_acquires(self, db_ready, filters):
        from services.document_service import get_user_documents

        nuevo, viejo = await leer_estable(
            lambda dos_acquires: get_user_documents(
                TEST_USER_ID,
                schema_name=SCHEMA,
                page=1,
                page_size=100,
                _force_two_acquires=dos_acquires,
                **filters,
            ),
            etiqueta=f"get_user_documents filtros={filters}",
        )

        assert nuevo["total"] == viejo["total"], (
            f"Paridad de total rota para filtros {filters}: "
            f"1-acquire={nuevo['total']} 2-acquires={viejo['total']}"
        )

        nuevo_ids = [d["document_id"] for d in nuevo["documents"]]
        viejo_ids = [d["document_id"] for d in viejo["documents"]]
        assert nuevo_ids == viejo_ids, (
            f"Paridad de ORDEN/contenido rota para filtros {filters}:\n"
            f"  1-acquire={nuevo_ids}\n"
            f"  2-acquires={viejo_ids}"
        )
        assert nuevo["documents"] == viejo["documents"], (
            f"Contenido de documentos distinto para filtros {filters}"
        )
