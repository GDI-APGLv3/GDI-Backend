"""
Tests de integracion para endpoints de transferencia y asignacion.
POST /api/v1/cases/{case_id}/transfer
POST /api/v1/cases/{case_id}/assign
Conecta a BD real (dev-test, schema 100_test).

Nota: Solo se testean validaciones y respuestas de error.
No se ejecutan transferencias/asignaciones reales para no alterar datos.
"""
import pytest

REAL_CASE_ID = "f615d581-a73a-48fc-b4e5-363938da3e1c"


class TestTransferEndpoints:
    """Tests de integracion para transferencia y asignacion."""

    @pytest.mark.asyncio
    async def test_transfer_validation_reason_too_short(self, client, test_headers):
        """POST transfer con reason muy corto da 422."""
        transfer_data = {
            "target_sector_id": "51000000-0000-0000-0000-000000000001",
            "reason": "muy",  # Min 5 caracteres
            "transfer_ownership": True,
        }

        response = await client.post(
            f"/api/v1/cases/{REAL_CASE_ID}/transfer",
            headers=test_headers,
            json=transfer_data,
        )

        assert response.status_code == 422
        print(f"[PASS] Transfer con reason corto da 422")

    @pytest.mark.asyncio
    async def test_transfer_validation_missing_target(self, client, test_headers):
        """POST transfer sin target_sector_id da 422."""
        transfer_data = {
            "reason": "Motivo valido de transferencia",
            "transfer_ownership": True,
        }

        response = await client.post(
            f"/api/v1/cases/{REAL_CASE_ID}/transfer",
            headers=test_headers,
            json=transfer_data,
        )

        assert response.status_code == 422
        print(f"[PASS] Transfer sin target da 422")

    @pytest.mark.asyncio
    async def test_assign_validation_missing_reason(self, client, test_headers):
        """POST assign sin reason da 422."""
        assign_data = {
            "target_sector_id": "51000000-0000-0000-0000-000000000001",
        }

        response = await client.post(
            f"/api/v1/cases/{REAL_CASE_ID}/assign",
            headers=test_headers,
            json=assign_data,
        )

        assert response.status_code == 422
        print(f"[PASS] Assign sin reason da 422")

    @pytest.mark.asyncio
    async def test_assign_validation_reason_too_short(self, client, test_headers):
        """POST assign con reason corto da 422."""
        assign_data = {
            "target_sector_id": "51000000-0000-0000-0000-000000000001",
            "reason": "ab",  # Min 5 caracteres
        }

        response = await client.post(
            f"/api/v1/cases/{REAL_CASE_ID}/assign",
            headers=test_headers,
            json=assign_data,
        )

        assert response.status_code == 422
        print(f"[PASS] Assign con reason corto da 422")

    @pytest.mark.asyncio
    async def test_transfer_no_tenant(self, client):
        """POST transfer sin tenant da 400."""
        transfer_data = {
            "target_sector_id": "51000000-0000-0000-0000-000000000001",
            "reason": "Motivo valido para transferencia",
            "transfer_ownership": True,
        }

        response = await client.post(
            f"/api/v1/cases/{REAL_CASE_ID}/transfer",
            json=transfer_data,
        )

        assert response.status_code == 400
        print(f"[PASS] Transfer sin tenant da 400")

    @pytest.mark.asyncio
    async def test_assign_no_tenant(self, client):
        """POST assign sin tenant da 400."""
        assign_data = {
            "target_sector_id": "51000000-0000-0000-0000-000000000001",
            "reason": "Motivo valido para asignacion",
        }

        response = await client.post(
            f"/api/v1/cases/{REAL_CASE_ID}/assign",
            json=assign_data,
        )

        assert response.status_code == 400
        print(f"[PASS] Assign sin tenant da 400")
