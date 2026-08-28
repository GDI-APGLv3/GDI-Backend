import pytest
import pytest_asyncio


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
        pytest.skip("Sin conexión a BD (DB_HOST/tunnel no disponible) — test GDI-209 skip-sin-BD")

    yield

    try:
        await close_pool()
    except Exception:
        pass


class TestEspaciosDeClaveDistintos:

    @pytest.mark.asyncio
    async def test_forma_vieja_y_forma_nueva_no_se_excluyen(self, db_ready):
        import asyncpg
        from database import DATABASE_URL

        conn_vieja = await asyncpg.connect(DATABASE_URL)
        conn_nueva = await asyncpg.connect(DATABASE_URL)
        try:
            tr_vieja = conn_vieja.transaction()
            await tr_vieja.start()
            await conn_vieja.execute("SELECT pg_advisory_xact_lock(888888)")

            tr_nueva = conn_nueva.transaction()
            await tr_nueva.start()
            got_new = await conn_nueva.fetchval(
                "SELECT pg_try_advisory_xact_lock(888888, hashtext($1))",
                "100_test",
            )

            assert got_new is True, (
                "La forma nueva (2 enteros) se bloqueó contra la forma vieja "
                "(1 entero) — si esto pasara, el corte completo no sería "
                "necesario. Como Postgres las trata como candados DISTINTOS, "
                "debe poder tomarlo igual: eso es justamente el fallo que "
                "obliga a la ventana de mantenimiento sin rolling deploy."
            )

            await tr_nueva.rollback()
            await tr_vieja.rollback()
        finally:
            await conn_vieja.close()
            await conn_nueva.close()

    @pytest.mark.asyncio
    async def test_forma_nueva_mismo_municipio_si_se_excluye(self, db_ready):
        import asyncpg
        from database import DATABASE_URL

        conn_a = await asyncpg.connect(DATABASE_URL)
        conn_b = await asyncpg.connect(DATABASE_URL)
        try:
            tr_a = conn_a.transaction()
            await tr_a.start()
            await conn_a.execute(
                "SELECT pg_advisory_xact_lock(888888, hashtext($1))",
                "100_test",
            )

            tr_b = conn_b.transaction()
            await tr_b.start()
            got_b = await conn_b.fetchval(
                "SELECT pg_try_advisory_xact_lock(888888, hashtext($1))",
                "100_test",
            )

            assert got_b is False, (
                "Dos conexiones con la forma nueva sobre el MISMO municipio "
                "deberían excluirse entre sí — si no, la serialización por "
                "municipio no está funcionando."
            )

            await tr_b.rollback()
            await tr_a.rollback()
        finally:
            await conn_a.close()
            await conn_b.close()

    @pytest.mark.asyncio
    async def test_forma_nueva_distinto_municipio_no_se_excluye(self, db_ready):
        import asyncpg
        from database import DATABASE_URL

        conn_a = await asyncpg.connect(DATABASE_URL)
        conn_b = await asyncpg.connect(DATABASE_URL)
        try:
            tr_a = conn_a.transaction()
            await tr_a.start()
            await conn_a.execute(
                "SELECT pg_advisory_xact_lock(888888, hashtext($1))",
                "100_test",
            )

            tr_b = conn_b.transaction()
            await tr_b.start()
            got_b = await conn_b.fetchval(
                "SELECT pg_try_advisory_xact_lock(888888, hashtext($1))",
                "200_otro_municipio",
            )

            assert got_b is True, (
                "Dos municipios distintos se siguen serializando entre sí — "
                "el cambio de GDI-209 no está teniendo efecto."
            )

            await tr_b.rollback()
            await tr_a.rollback()
        finally:
            await conn_a.close()
            await conn_b.close()
