
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.exceptions import TransientLookupError


def _fila(is_reserved):
    return {
        "document_id": "11111111-1111-1111-1111-111111111111",
        "official_number": "IF-2026-00000001-MDEV-INNO",
        "document_type_is_reserved": is_reserved,
        "document_type_acronym": "IF",
        "document_type_name": "Informe",
        "creator_name": "Testing Automatizado",
        "numerator_name": "Testing Automatizado",
        "updated_at": None,
    }


@pytest.mark.asyncio
async def test_reservado_sin_determinar_no_se_devuelve():
    from services.documents.retrieval import official_search as mod

    with patch.object(mod, "fetch_one", new_callable=AsyncMock, return_value=_fila(None)):
        res = await mod.search_official_document_by_number(
            "IF-2026-00000001-MDEV-INNO",
            exclude_reserved=True,
            schema_name="100_test",
        )

    assert res["found"] is False, (
        "REGRESIÓN GDI-302: no se pudo determinar si el documento es reservado "
        "y se devolvió igual — 'no pude verificar' no puede significar 'no aplica'"
    )
    assert res["document"] is None


@pytest.mark.asyncio
async def test_reservado_explicito_sigue_sin_devolverse():
    from services.documents.retrieval import official_search as mod

    with patch.object(mod, "fetch_one", new_callable=AsyncMock, return_value=_fila(True)):
        res = await mod.search_official_document_by_number(
            "IF-2026-00000001-MDEV-INNO",
            exclude_reserved=True,
            schema_name="100_test",
        )

    assert res["found"] is False


@pytest.mark.asyncio
async def test_documento_normal_se_sigue_devolviendo():
    from services.documents.retrieval import official_search as mod

    with patch.object(mod, "fetch_one", new_callable=AsyncMock, return_value=_fila(False)):
        res = await mod.search_official_document_by_number(
            "IF-2026-00000001-MDEV-INNO",
            exclude_reserved=True,
            schema_name="100_test",
        )

    assert res["found"] is True, "el camino normal quedó bloqueado"


@pytest.mark.asyncio
async def test_ciudad_no_se_inventa_si_la_lectura_falla():
    from services.shared import settings_utils as mod

    conn = MagicMock()
    conn.fetchrow = AsyncMock(side_effect=Exception("pool timeout"))

    with pytest.raises(TransientLookupError):
        await mod.get_city_from_settings(conn=conn, schema_name="100_test")


@pytest.mark.asyncio
async def test_ciudad_no_se_inventa_si_la_lectura_vuelve_fantasma():
    from services.shared import settings_utils as mod

    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=None)

    with pytest.raises(TransientLookupError):
        await mod.get_city_from_settings(conn=conn, schema_name="100_test")


@pytest.mark.asyncio
async def test_ciudad_ausente_si_usa_el_default():
    from services.shared import settings_utils as mod
    from config.constants import DEFAULT_CITY

    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"city": None})

    assert await mod.get_city_from_settings(conn=conn, schema_name="100_test") == DEFAULT_CITY


@pytest.mark.asyncio
async def test_ciudad_configurada_se_devuelve_tal_cual():
    from services.shared import settings_utils as mod

    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value={"city": "Ciudad Ejemplo"})

    assert await mod.get_city_from_settings(conn=conn, schema_name="100_test") == "Ciudad Ejemplo"


@pytest.mark.asyncio
async def test_settings_no_cachea_un_fallback_inventado():
    from services.shared import settings_utils as mod

    mod.invalidate_settings_cache()
    with patch("database.fetch_one", new_callable=AsyncMock, side_effect=Exception("pool timeout")):
        with pytest.raises(TransientLookupError):
            await mod.get_tenant_settings("100_test")

    assert "100_test" not in mod._settings_cache, (
        "se cacheó una configuración que nunca se pudo leer"
    )
