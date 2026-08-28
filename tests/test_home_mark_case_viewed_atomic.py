import pytest
from unittest.mock import AsyncMock, MagicMock, patch


CASE_ID = "11111111-1111-1111-1111-111111111111"
USER_ID = "22222222-2222-2222-2222-222222222222"
SCHEMA = "100_test"


class _FakeTransaction:

    def __init__(self, conn, fail_on_call: int | None = None):
        self.conn = conn
        self.entered = False
        self.exited_with_error = False

    async def __aenter__(self):
        self.entered = True
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        self.exited_with_error = exc_type is not None
        return False


@pytest.mark.asyncio
async def test_los_dos_escritos_van_en_la_misma_transaccion():
    from services.home import service

    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")
    tx = _FakeTransaction(conn)

    with patch.object(service, "fetch_one", new=AsyncMock(return_value={"?column?": 1})), \
         patch.object(service, "transaction", return_value=tx) as tx_factory, \
         patch.object(service, "execute", new=AsyncMock()) as execute_suelto:
        await service.mark_case_viewed(USER_ID, CASE_ID, schema_name=SCHEMA)

    assert tx.entered, "no se abrió la transacción"
    assert conn.execute.await_count == 2, (
        f"se esperaban 2 escritos dentro de la TX, hubo {conn.execute.await_count}"
    )
    execute_suelto.assert_not_awaited()

    kwargs = tx_factory.call_args.kwargs
    assert kwargs["schema_name"] == SCHEMA
    assert kwargs["user_id"] == USER_ID
    assert kwargs["auth_source"] == "jwt"


@pytest.mark.asyncio
async def test_si_falla_el_segundo_escrito_la_transaccion_no_commitea():
    from services.home import service

    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=["INSERT 0 1", RuntimeError("boom")])
    tx = _FakeTransaction(conn)

    with patch.object(service, "fetch_one", new=AsyncMock(return_value={"?column?": 1})), \
         patch.object(service, "transaction", return_value=tx):
        with pytest.raises(RuntimeError):
            await service.mark_case_viewed(USER_ID, CASE_ID, schema_name=SCHEMA)

    assert tx.exited_with_error, (
        "la excepción tiene que salir por el __aexit__ del contexto para que "
        "asyncpg haga ROLLBACK; si se tragara, el 1er INSERT quedaría commiteado"
    )


@pytest.mark.asyncio
async def test_expediente_inexistente_da_404_y_no_abre_transaccion():
    from services.home import service
    from shared.exceptions import NotFoundError

    with patch.object(service, "fetch_one", new=AsyncMock(return_value=None)), \
         patch.object(service, "transaction") as tx_factory:
        with pytest.raises(NotFoundError):
            await service.mark_case_viewed(USER_ID, CASE_ID, schema_name=SCHEMA)

    tx_factory.assert_not_called()
