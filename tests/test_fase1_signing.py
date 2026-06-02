"""
Tests unitarios para Fase 1 NuevaFIRMAfull: r2_lock, audit_logger, notary_hmac.

Notas de implementación:
- release_signing_lock_R2_success con is_numerator=True NO hace r2_put a oficial/.
  El upload lo hace numerator.py directamente. El lock solo limpia inprocess/.
- release_signing_lock_R2_success con is_numerator=False hace r2_put a tosign/{uuid}.pdf.
"""
from unittest.mock import patch, MagicMock, AsyncMock, call
import pytest

from services.documents.signing.r2_lock import (
    acquire_signing_lock_R2,
    release_signing_lock_R2_success,
    release_signing_lock_R2_fail,
)
from services.r2_client import R2KeyNotFound


class TestR2Lock:
    @pytest.mark.asyncio
    async def test_acquire_success(self):
        """Adquiere lock cuando tosign/ existe e inprocess/ no existe."""
        with patch("services.documents.signing.r2_lock.r2_head", new_callable=AsyncMock, side_effect=R2KeyNotFound("inprocess")), \
             patch("services.documents.signing.r2_lock.r2_copy", new_callable=AsyncMock) as mock_copy, \
             patch("services.documents.signing.r2_lock.r2_delete", new_callable=AsyncMock) as mock_delete:
            result = await acquire_signing_lock_R2(schema_name="test_schema", doc_id="abc-123")
        assert result is True
        mock_copy.assert_called_once()
        mock_delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_acquire_fails_when_inprocess_exists(self):
        """Retorna False si inprocess/ ya existe (alguien tiene el lock)."""
        with patch("services.documents.signing.r2_lock.r2_head", new_callable=AsyncMock) as mock_head:
            # r2_head no lanza → objeto existe → lock ya tomado
            mock_head.return_value = {"ETag": "abc"}
            result = await acquire_signing_lock_R2(schema_name="test_schema", doc_id="abc-123")
        assert result is False

    @pytest.mark.asyncio
    async def test_acquire_fails_when_tosign_not_found(self):
        """Retorna False si tosign/ no existe (PDF no listo para firmar)."""
        with patch("services.documents.signing.r2_lock.r2_head", new_callable=AsyncMock, side_effect=R2KeyNotFound("inprocess")), \
             patch("services.documents.signing.r2_lock.r2_copy", new_callable=AsyncMock, side_effect=R2KeyNotFound("tosign")):
            result = await acquire_signing_lock_R2(schema_name="test_schema", doc_id="abc-123")
        assert result is False

    @pytest.mark.asyncio
    async def test_acquire_does_not_call_copy_when_inprocess_exists(self):
        """Si inprocess/ existe, no se intenta copiar (evita sobrescritura)."""
        with patch("services.documents.signing.r2_lock.r2_head", new_callable=AsyncMock) as mock_head, \
             patch("services.documents.signing.r2_lock.r2_copy", new_callable=AsyncMock) as mock_copy:
            mock_head.return_value = {"ETag": "abc"}
            await acquire_signing_lock_R2(schema_name="test_schema", doc_id="abc-123")
        mock_copy.assert_not_called()

    @pytest.mark.asyncio
    async def test_release_fail_is_idempotent(self):
        """release_fail no lanza si inprocess no existe (R2KeyNotFound en copy)."""
        with patch("services.documents.signing.r2_lock.r2_copy", new_callable=AsyncMock, side_effect=R2KeyNotFound("k")):
            # No debe lanzar excepción
            await release_signing_lock_R2_fail(schema_name="test_schema", doc_id="abc-123")

    @pytest.mark.asyncio
    async def test_release_success_numerator_only_cleans_inprocess(self):
        """
        Numerador: release_success NO hace r2_put a oficial/.
        El upload a oficial/ lo hace numerator.py directamente.
        Solo limpia inprocess/ via r2_delete.
        """
        with patch("services.documents.signing.r2_lock.r2_put", new_callable=AsyncMock) as mock_put, \
             patch("services.documents.signing.r2_lock.r2_delete", new_callable=AsyncMock) as mock_delete:
            await release_signing_lock_R2_success(
                schema_name="test_schema",
                doc_id="abc-123",
                signed_pdf=b"%PDF-1.4 test",
                is_numerator=True,
                number="42",
            )
        # No debe llamar a r2_put (upload a oficial lo hace numerator.py)
        mock_put.assert_not_called()
        # Debe limpiar inprocess/
        mock_delete.assert_called_once()
        deleted_key = mock_delete.call_args[1]["key"]
        assert "inprocess" in deleted_key
        assert "abc123" in deleted_key  # uuid sin guiones

    @pytest.mark.asyncio
    async def test_release_success_non_numerator_puts_in_tosign(self):
        """No numerador: PDF firmado vuelve a tosign/{uuid_sin_guiones}.pdf."""
        with patch("services.documents.signing.r2_lock.r2_put", new_callable=AsyncMock) as mock_put, \
             patch("services.documents.signing.r2_lock.r2_delete", new_callable=AsyncMock):
            await release_signing_lock_R2_success(
                schema_name="test_schema",
                doc_id="abc-123",
                signed_pdf=b"%PDF-1.4 test",
                is_numerator=False,
                number=None,
            )
        mock_put.assert_called_once()
        key_used = mock_put.call_args[1]["key"]
        assert key_used == "abc123.pdf"  # _tosign_key("abc-123") = "abc123.pdf"
        assert mock_put.call_args[1]["bucket"] == "tosign"

    @pytest.mark.asyncio
    async def test_release_success_non_numerator_cleans_inprocess(self):
        """No numerador: también limpia inprocess/ después del put."""
        with patch("services.documents.signing.r2_lock.r2_put", new_callable=AsyncMock), \
             patch("services.documents.signing.r2_lock.r2_delete", new_callable=AsyncMock) as mock_delete:
            await release_signing_lock_R2_success(
                schema_name="test_schema",
                doc_id="abc-123",
                signed_pdf=b"%PDF-1.4 test",
                is_numerator=False,
                number=None,
            )
        mock_delete.assert_called_once()
        deleted_key = mock_delete.call_args[1]["key"]
        assert "inprocess" in deleted_key


class TestNotaryHmac:
    def test_header_format(self):
        """build_internal_hmac_header produce header con formato correcto."""
        import importlib
        import services.notary_internal_hmac as mod

        with patch.dict("os.environ", {"NOTARY_INTERNAL_HMAC_SECRET": "testsecret"}, clear=False):
            importlib.reload(mod)
            header = mod.build_internal_hmac_header(
                method="POST", path="/sign-pdf", body_bytes=b"test body"
            )
        assert header.startswith("t=")
        assert ",v1=" in header
        parts = dict(p.split("=", 1) for p in header.split(","))
        assert parts["t"].isdigit()
        assert len(parts["v1"]) > 10  # base64 no vacío

    def test_different_bodies_produce_different_signatures(self):
        """Cuerpos distintos producen firmas distintas."""
        import importlib
        import services.notary_internal_hmac as mod

        with patch.dict("os.environ", {"NOTARY_INTERNAL_HMAC_SECRET": "testsecret"}, clear=False):
            importlib.reload(mod)
            h1 = mod.build_internal_hmac_header(method="POST", path="/sign-pdf", body_bytes=b"body1")
            h2 = mod.build_internal_hmac_header(method="POST", path="/sign-pdf", body_bytes=b"body2")
        assert h1.split(",v1=")[1] != h2.split(",v1=")[1]

    def test_empty_secret_returns_empty_string(self):
        """Sin secret configurado, retorna cadena vacía (backward compat)."""
        import importlib
        import services.notary_internal_hmac as mod

        with patch.dict("os.environ", {"NOTARY_INTERNAL_HMAC_SECRET": ""}, clear=False):
            importlib.reload(mod)
            result = mod.build_internal_hmac_header(
                method="POST", path="/sign-pdf", body_bytes=b"body"
            )
        assert result == ""

    def test_fly_app_without_secret_logs_error(self):
        """En Fly.io sin secret, se loggea error al importar el módulo."""
        import importlib
        import services.notary_internal_hmac as mod

        env = {"NOTARY_INTERNAL_HMAC_SECRET": "", "FLY_APP_NAME": "<your-backend-app>"}
        with patch.dict("os.environ", env, clear=False), \
             patch("logging.Logger.error") as mock_error:
            importlib.reload(mod)
        mock_error.assert_called_once()
        call_args = mock_error.call_args[0][0]
        assert "NOTARY_INTERNAL_HMAC_SECRET" in call_args
        assert "Fly.io" in call_args
