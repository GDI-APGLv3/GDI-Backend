import re
from unittest.mock import AsyncMock, patch

import pytest

from api_gateway.auth_rest import check_and_log_sync_access
from api_gateway.tools.sync import OFFICIAL_DOCUMENTS_COLUMNS, get_sync_catalog, get_sync_data


@pytest.mark.asyncio
@pytest.mark.parametrize("rate,esperado", [(60, 1.0), (120, 0.5), (600, 0.1), (1, 60.0)])
async def test_gdi299_intervalo_no_se_trunca_a_cero(rate, esperado):
    fetch_one = AsyncMock(return_value={"id": "x"})
    with patch("database.fetch_one", fetch_one):
        assert await check_and_log_sync_access(
            api_key_id="k", schema_name="100_test", action="sync_data",
            ip="1.2.3.4", user_agent="ua", rate_limit_per_minute=rate,
        ) is None

    intervalo = fetch_one.await_args.args[8]
    assert intervalo == pytest.approx(esperado)
    assert intervalo > 0, "ventana en 0 seg => el WHERE NOT EXISTS nunca frena nada"


@pytest.mark.asyncio
async def test_gdi299_retry_after_nunca_es_cero():
    fetch_one = AsyncMock(side_effect=[None, Exception("boom")])
    with patch("database.fetch_one", fetch_one):
        retry_after = await check_and_log_sync_access(
            api_key_id="k", schema_name="100_test", action="sync_data",
            ip="1.2.3.4", user_agent="ua", rate_limit_per_minute=600,
        )
    assert retry_after >= 1


@pytest.mark.asyncio
async def test_gdi300_el_catalogo_cuenta_lo_mismo_que_se_entrega():
    capturadas = {}

    async def fake_fetch_all(query, *args, **kwargs):
        capturadas.setdefault("sqls", []).append(query)
        return []

    with patch("database.fetch_all", fake_fetch_all):
        await get_sync_catalog(schema_name="100_test")
        await get_sync_data("official_documents", "2026-01-01T00:00:00Z", 1, 50, schema_name="100_test")

    catalogo, datos = capturadas["sqls"]
    od = [p for p in catalogo.split(" UNION ALL ") if '"official_documents"' in p]
    assert len(od) == 1
    assert "WHERE" not in od[0], "el COUNT filtra y los datos no -> el catalogo volveria a mentir"
    where = datos[datos.index("WHERE"):]
    assert "reservation_status" not in where, "los datos filtran y el COUNT no -> el catalogo miente"


@pytest.mark.asyncio
async def test_gdi300_los_documentos_anulados_viajan_con_su_estado():
    assert "reservation_status" in OFFICIAL_DOCUMENTS_COLUMNS, (
        "sin reservation_status, un RESERVED/CANCELLED llega indistinguible de un documento valido"
    )
