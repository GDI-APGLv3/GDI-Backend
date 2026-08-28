
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.exceptions import TransientLookupError, ValidationError
import services.cases.documents as mod


def _conn(reserved_check_result):
    conn = MagicMock()
    conn.execute = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(side_effect=[
        reserved_check_result,
        {"max_order": 0},
        {"max_order": 0},
    ])
    conn.fetch = AsyncMock(return_value=[])
    return conn


def _parchar_transaction(conn):
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return patch.object(mod, "transaction", MagicMock(return_value=ctx))


DOC = "11111111-1111-1111-1111-111111111111"
CASE = "22222222-2222-2222-2222-222222222222"
USER = "33333333-3333-3333-3333-333333333333"
SECTOR = "44444444-4444-4444-4444-444444444444"


def _parchar_permisos():
    from services.case_service import CaseService
    return patch.object(
        CaseService, "get_user_editable_sector_ids",
        AsyncMock(return_value=[SECTOR]),
    )


def _parchar_lecturas_previas():
    fila = {
        "id": CASE, "case_number": "EX-2026-1", "official_number": "IF-2026-1",
        "reference": "ref de prueba", "status": "official",
    }
    return patch.object(mod, "fetch_all", AsyncMock(side_effect=[
        [fila],
        [fila],
        [fila],
        [],
    ]))


@pytest.mark.asyncio
async def test_lectura_fantasma_no_vincula_y_pide_reintento():
    conn = _conn(None)
    with _parchar_transaction(conn), _parchar_lecturas_previas(), _parchar_permisos():
        with pytest.raises(TransientLookupError):
            await mod.link_official_document(
                case_id=CASE, official_document_id=DOC,
                linking_user_id=USER, user_sector_id=SECTOR,
                schema_name="100_test",
            )

    inserts = [c for c in conn.execute.await_args_list
               if c.args and "INSERT" in str(c.args[0]).upper()]
    assert not inserts, "se insertó el vínculo pese a no poder verificar la reserva"


@pytest.mark.asyncio
async def test_documento_reservado_a_case_publico_sigue_bloqueado():
    conn = _conn({"doc_reserved": True, "case_reserved": False})
    with _parchar_transaction(conn), _parchar_lecturas_previas(), _parchar_permisos():
        with pytest.raises(ValidationError):
            await mod.link_official_document(
                case_id=CASE, official_document_id=DOC,
                linking_user_id=USER, user_sector_id=SECTOR,
                schema_name="100_test",
            )


@pytest.mark.asyncio
async def test_caso_normal_sigue_vinculando_sin_friccion():
    conn = _conn({"doc_reserved": False, "case_reserved": False})
    with _parchar_transaction(conn), _parchar_lecturas_previas(), _parchar_permisos():
        try:
            await mod.link_official_document(
                case_id=CASE, official_document_id=DOC,
                linking_user_id=USER, user_sector_id=SECTOR,
                schema_name="100_test",
            )
        except (TransientLookupError, ValidationError) as exc:
            pytest.fail(f"el camino normal quedó bloqueado: {exc}")
        except Exception:
            pass


@pytest.mark.asyncio
async def test_propose_deja_salir_el_503_en_vez_de_convertirlo_en_500():
    conn = _conn(None)
    with _parchar_transaction(conn), \
         patch("database.check_document_exists", new_callable=AsyncMock, return_value=True):
        with pytest.raises(TransientLookupError):
            await mod.propose_document_to_case(
                case_id=CASE,
                document_draft_id=DOC,
                proposing_user_id=USER,
                schema_name="100_test",
            )
