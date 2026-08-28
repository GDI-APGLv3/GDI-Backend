from unittest.mock import AsyncMock, patch

import pytest

from api_gateway.rate_limiter import (
    DEFAULT_MAX_RATE_LIMIT,
    MAX_RATE_LIMIT_BY_KEY_TYPE,
    MAX_RATE_LIMIT_TENANT_CAN_SET,
    cap_rate_limit,
)


@pytest.mark.parametrize("key_type,pide,aplica", [
    ("backup", 60, 60),
    ("backup", 120, 120),
    ("backup", 121, 120),
    ("backup", 10_000, 120),
    ("api", 600, 600),
    ("api", 100_000, 100_000),
    ("tad", 30, 30),
])
def test_gdi380_el_techo_recorta_por_tipo(key_type, pide, aplica):
    assert cap_rate_limit(pide, key_type, key_id="k") == aplica


def test_gdi380_no_capa_las_keys_de_pruebas_de_carga():
    for valor in (10_000, 100_000):
        assert cap_rate_limit(valor, "api", key_id="b1000000-k6") == valor


def test_gdi380_un_key_type_desconocido_igual_tiene_techo():
    assert cap_rate_limit(9_999_999, "todavia-no-existe") == DEFAULT_MAX_RATE_LIMIT
    assert cap_rate_limit(60, "todavia-no-existe") == 60


def test_gdi380_los_topes_no_son_todos_iguales():
    assert MAX_RATE_LIMIT_BY_KEY_TYPE["backup"] < MAX_RATE_LIMIT_BY_KEY_TYPE["api"]
    assert MAX_RATE_LIMIT_TENANT_CAN_SET["backup"] < MAX_RATE_LIMIT_TENANT_CAN_SET["api"]


def test_gdi380_el_municipio_nunca_puede_pedir_mas_que_el_techo_del_sistema():
    for key_type, tope_tenant in MAX_RATE_LIMIT_TENANT_CAN_SET.items():
        techo = MAX_RATE_LIMIT_BY_KEY_TYPE.get(key_type, DEFAULT_MAX_RATE_LIMIT)
        assert tope_tenant <= techo, f"{key_type}: el municipio puede pedir mas de lo que se aplica"


def test_gdi380_no_rompe_lo_que_ya_existe():
    for key_type in ("api", "public", "tad", "backup"):
        assert cap_rate_limit(60, key_type) == 60, f"{key_type} con el default de 60"


@pytest.mark.asyncio
async def test_gdi380_la_backup_key_sale_topeada_de_la_validacion():
    from api_gateway import auth_rest

    fila = {
        "id": "11111111-1111-1111-1111-111111111111",
        "api_key_prefix": "bk-gdi-sync-x",
        "municipality_id": "22222222-2222-2222-2222-222222222222",
        "key_name": "Sync del municipio",
        "key_type": "backup",
        "expires_at": None,
        "key_active": True,
        "allowed_origins": None,
        "rate_limit_per_minute": 10_000,
        "schema_name": "100_test",
        "municipality_name": "Muni",
        "muni_active": True,
    }

    class FakeRequest:
        headers = {"X-API-Key": "bk-gdi-sync-loquesea"}
        client = None

    with patch("database.fetch_one", AsyncMock(return_value=fila)), \
         patch.object(auth_rest, "_update_last_used", AsyncMock()):
        ctx = await auth_rest.validate_backup_api_key(FakeRequest())

    assert ctx["rate_limit_per_minute"] == MAX_RATE_LIMIT_BY_KEY_TYPE["backup"]
    assert ctx["rate_limit_per_minute"] != 10_000
