"""
Tests locales de memos - usan ASGITransport (sin red, sin Fly.io).
Prueban comportamiento del codigo local.

Para que FastAPI retorne 422 por UUID invalido en path params, el dependency
get_current_user debe resolver exitosamente (sin HTTPException). En FastAPI 0.115+,
si el dependency falla con HTTPException, esa respuesta tiene precedencia sobre
la validacion de path params.

El mock de get_current_user usa app.dependency_overrides (no monkeypatch de nombre)
porque FastAPI captura la referencia a la funcion en Depends() al importar.
"""
import pytest
from unittest.mock import AsyncMock, patch


def _make_mock_auth_user():
    from models.schemas import AuthenticatedUser, SectorPermission
    return AuthenticatedUser(
        user_id="a1000000-0000-0000-0000-000000000001",
        auth_id="local_test_user",
        email="test.user@municipalidad.test",
        full_name="Usuario Test",
        permissions=[
            SectorPermission(
                sector_id="51000000-0000-0000-0000-000000000001",
                sector_acronym="PRIV",
                department_id="d1000000-0000-0000-0000-000000000001",
                department_name="Intendencia",
                department_acronym="INTE",
                can_view=True,
                can_edit=True,
                is_primary=True,
            )
        ],
    )


@pytest.fixture(autouse=True)
def mock_tenant_and_auth():
    """Mockea middleware y dependency de auth para tests locales sin BD."""
    from main import app
    from auth import get_current_user

    mock_user_db = {
        "id": "a1000000-0000-0000-0000-000000000001",
        "email": "test.user@municipalidad.test",
        "sector_id": "51000000-0000-0000-0000-000000000001",
        "estado": 1,
        "auth_id": "local_test_user",
    }
    mock_auth_user = _make_mock_auth_user()

    # Override FastAPI dependency (la unica forma de mockear Depends())
    app.dependency_overrides[get_current_user] = lambda: mock_auth_user

    with (
        patch(
            "middleware.tenant_middleware.find_user_by_any_identifier",
            new_callable=AsyncMock,
            return_value=mock_user_db,
        ),
        patch(
            "middleware.tenant_middleware._try_autocomplete_auth_id",
            new_callable=AsyncMock,
        ),
    ):
        yield

    # Limpiar override despues del test
    app.dependency_overrides.pop(get_current_user, None)


class TestMemosUUIDValidation:
    """Validacion de UUID en endpoints de memos (tests locales, sin BD)."""

    async def test_memo_id_malformado_devuelve_422(self, client, test_headers):
        """GET /memos/{id} con UUID malformado debe dar 422, no 500."""
        r = await client.get("/memos/no-es-un-uuid-valido", headers=test_headers)
        assert r.status_code == 422, (
            f"GET /memos/{{uuid_malformado}} devuelve {r.status_code} en vez de 422. "
            f"Respuesta: {r.text[:200]}"
        )

    async def test_archive_uuid_malformado_devuelve_422(self, client, test_headers):
        """PATCH /memos/{id}/archive con UUID malformado debe dar 422, no 500."""
        r = await client.patch(
            "/memos/no-es-un-uuid-valido/archive",
            json={"archived": True},
            headers=test_headers,
        )
        assert r.status_code == 422, (
            f"PATCH /memos/{{uuid_malformado}}/archive devuelve {r.status_code} en vez de 422. "
            f"Respuesta: {r.text[:200]}"
        )
