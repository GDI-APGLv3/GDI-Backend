import pytest
import pytest_asyncio

SCHEMA = "100_test"
TEST_USER_ID = "a1000000-0000-0000-0000-000000000001"


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


@pytest.mark.asyncio
async def test_total_y_total_pages_son_none_por_defecto(db_ready):
    from services.document_service import get_user_documents

    result = await get_user_documents(
        user_id=TEST_USER_ID,
        page=1,
        page_size=20,
        schema_name=SCHEMA,
    )
    assert result["total"] is None, "GDI-369: `total` debe ser None por defecto"
    assert result["total_pages"] is None, (
        "GDI-369: `total_pages` debe ser None por defecto"
    )
    assert isinstance(result["has_next"], bool)
    assert isinstance(result["has_previous"], bool)


@pytest.mark.asyncio
async def test_solapa_a_mi_firma_si_devuelve_total_numerico(db_ready):
    from services.document_service import get_user_documents

    result = await get_user_documents(
        user_id=TEST_USER_ID,
        status_filter="A mi firma",
        page=1,
        page_size=20,
        schema_name=SCHEMA,
    )
    assert isinstance(result["total"], int), (
        "GDI-369: `A mi firma` es la unica solapa que cuenta el universo. "
        f"`total` debe ser int, vino {type(result['total']).__name__}"
    )
    assert result["total"] >= 0
    if result["total"] == 0:
        assert result["total_pages"] == 0
    else:
        expected_pages = (result["total"] + 19) // 20
        assert result["total_pages"] == expected_pages


@pytest.mark.asyncio
async def test_otras_solapas_siguen_con_total_none(db_ready):
    from services.document_service import get_user_documents

    for status in ("Firmado", "En proceso de firma", "En edición"):
        result = await get_user_documents(
            user_id=TEST_USER_ID,
            status_filter=status,
            page=1,
            page_size=20,
            schema_name=SCHEMA,
        )
        assert result["total"] is None, (
            f"solapa {status!r} no deberia contar el universo "
            f"(total={result['total']!r})"
        )
        assert result["total_pages"] is None


@pytest.mark.asyncio
async def test_has_previous_from_page_number(db_ready):
    from services.document_service import get_user_documents

    p1 = await get_user_documents(
        user_id=TEST_USER_ID, page=1, page_size=5, schema_name=SCHEMA
    )
    assert p1["has_previous"] is False

    p2 = await get_user_documents(
        user_id=TEST_USER_ID, page=2, page_size=5, schema_name=SCHEMA
    )
    assert p2["has_previous"] is True


@pytest.mark.asyncio
async def test_offset_beyond_data_returns_empty_and_no_next(db_ready):
    from services.document_service import get_user_documents

    result = await get_user_documents(
        user_id=TEST_USER_ID,
        page=99999,
        page_size=20,
        schema_name=SCHEMA,
    )
    assert result["documents"] == []
    assert result["has_next"] is False
    assert result["has_previous"] is True
    assert result["total"] is None
    assert result["total_pages"] is None


@pytest.mark.asyncio
async def test_page_size_bounds_respected(db_ready):
    from services.document_service import get_user_documents

    page_size = 5
    result = await get_user_documents(
        user_id=TEST_USER_ID,
        page=1,
        page_size=page_size,
        schema_name=SCHEMA,
    )
    assert len(result["documents"]) <= page_size, (
        "la fila extra del LIMIT+1 no puede filtrarse al cliente"
    )


@pytest.mark.asyncio
async def test_has_next_consistent_with_following_page(db_ready):
    from services.document_service import get_user_documents

    page_size = 3

    for page in range(1, 6):
        current = await get_user_documents(
            user_id=TEST_USER_ID,
            page=page,
            page_size=page_size,
            schema_name=SCHEMA,
        )
        following = await get_user_documents(
            user_id=TEST_USER_ID,
            page=page + 1,
            page_size=page_size,
            schema_name=SCHEMA,
        )

        if current["has_next"]:
            assert len(following["documents"]) >= 1, (
                f"page {page} dice has_next=True pero page {page + 1} vino vacia "
                f"-- el LIMIT+1 esta contando mal o la fila extra no se descarta"
            )
        else:
            assert following["documents"] == [], (
                f"page {page} dice has_next=False pero page {page + 1} trajo "
                f"{len(following['documents'])} docs"
            )

        if not current["documents"]:
            break
