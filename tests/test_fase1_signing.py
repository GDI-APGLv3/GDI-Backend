from unittest.mock import patch, AsyncMock
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
        with patch("services.documents.signing.r2_lock.r2_head", new_callable=AsyncMock, side_effect=R2KeyNotFound("inprocess")), \
             patch("services.documents.signing.r2_lock.r2_copy", new_callable=AsyncMock) as mock_copy, \
             patch("services.documents.signing.r2_lock.r2_delete", new_callable=AsyncMock) as mock_delete:
            result = await acquire_signing_lock_R2(schema_name="test_schema", doc_id="abc-123")
        assert result is True
        mock_copy.assert_called_once()
        mock_delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_acquire_fails_when_inprocess_exists(self):
        with patch("services.documents.signing.r2_lock.r2_head", new_callable=AsyncMock) as mock_head:
            mock_head.return_value = {"ETag": "abc"}
            result = await acquire_signing_lock_R2(schema_name="test_schema", doc_id="abc-123")
        assert result is False

    @pytest.mark.asyncio
    async def test_acquire_fails_when_tosign_not_found(self):
        with patch("services.documents.signing.r2_lock.r2_head", new_callable=AsyncMock, side_effect=R2KeyNotFound("inprocess")), \
             patch("services.documents.signing.r2_lock.r2_copy", new_callable=AsyncMock, side_effect=R2KeyNotFound("tosign")):
            result = await acquire_signing_lock_R2(schema_name="test_schema", doc_id="abc-123")
        assert result is False

    @pytest.mark.asyncio
    async def test_acquire_does_not_call_copy_when_inprocess_exists(self):
        with patch("services.documents.signing.r2_lock.r2_head", new_callable=AsyncMock) as mock_head, \
             patch("services.documents.signing.r2_lock.r2_copy", new_callable=AsyncMock) as mock_copy:
            mock_head.return_value = {"ETag": "abc"}
            await acquire_signing_lock_R2(schema_name="test_schema", doc_id="abc-123")
        mock_copy.assert_not_called()

    @pytest.mark.asyncio
    async def test_release_fail_is_idempotent(self):
        with patch("services.documents.signing.r2_lock.r2_copy", new_callable=AsyncMock, side_effect=R2KeyNotFound("k")):
            await release_signing_lock_R2_fail(schema_name="test_schema", doc_id="abc-123")

    @pytest.mark.asyncio
    async def test_release_success_numerator_only_cleans_inprocess(self):
        with patch("services.documents.signing.r2_lock.r2_put", new_callable=AsyncMock) as mock_put, \
             patch("services.documents.signing.r2_lock.r2_delete", new_callable=AsyncMock) as mock_delete:
            await release_signing_lock_R2_success(
                schema_name="test_schema",
                doc_id="abc-123",
                signed_pdf=b"%PDF-1.4 test",
                is_numerator=True,
                number="42",
            )
        mock_put.assert_not_called()
        mock_delete.assert_called_once()
        deleted_key = mock_delete.call_args[1]["key"]
        assert "inprocess" in deleted_key
        assert "abc123" in deleted_key

    @pytest.mark.asyncio
    async def test_release_success_non_numerator_puts_in_tosign(self):
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
        assert key_used == "abc123.pdf"
        assert mock_put.call_args[1]["bucket"] == "tosign"

    @pytest.mark.asyncio
    async def test_release_success_non_numerator_cleans_inprocess(self):
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
        import importlib
        import services.notary_internal_hmac as mod

        with patch.dict("os.environ", {"NOTARY_INTERNAL_HMAC_SECRET": "testsecret"}, clear=False):
            importlib.reload(mod)
            header = mod.build_internal_hmac_header(method="POST", path="/sign-pdf", body=b"%PDF-1.4 test")
        assert header.startswith("t=")
        assert ",n=" in header
        assert ",v2=" in header
        parts = dict(p.split("=", 1) for p in header.split(","))
        assert parts["t"].isdigit()
        assert len(parts["n"]) > 0
        assert len(parts["v2"]) > 10

    def test_different_paths_produce_different_signatures(self):
        import importlib
        import services.notary_internal_hmac as mod

        with patch.dict("os.environ", {"NOTARY_INTERNAL_HMAC_SECRET": "testsecret"}, clear=False):
            importlib.reload(mod)
            h1 = mod.build_internal_hmac_header(method="POST", path="/sign-pdf", body=b"same-body")
            h2 = mod.build_internal_hmac_header(method="POST", path="/stamp-number", body=b"same-body")
        assert h1.split(",v2=")[1] != h2.split(",v2=")[1]

    def test_different_bodies_produce_different_signatures(self):
        import importlib
        import services.notary_internal_hmac as mod

        with patch.dict("os.environ", {"NOTARY_INTERNAL_HMAC_SECRET": "testsecret"}, clear=False):
            importlib.reload(mod)
            h1 = mod.build_internal_hmac_header(method="POST", path="/sign-pdf", body=b"%PDF-A")
            h2 = mod.build_internal_hmac_header(method="POST", path="/sign-pdf", body=b"%PDF-B")
        assert h1.split(",v2=")[1] != h2.split(",v2=")[1]

    def test_empty_secret_returns_empty_string(self):
        import importlib
        import services.notary_internal_hmac as mod

        with patch.dict("os.environ", {"NOTARY_INTERNAL_HMAC_SECRET": ""}, clear=False):
            importlib.reload(mod)
            result = mod.build_internal_hmac_header(method="POST", path="/sign-pdf")
        assert result == ""

    def test_fly_app_without_secret_logs_error(self):
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
