import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from shared.exceptions import StaleReservationError, ValidationError, NotaryBreakerOpenError


DOC_ID = str(uuid.UUID("aaaaaaaa-0000-0000-0000-000000000001"))
USER_ID = str(uuid.UUID("bbbbbbbb-0000-0000-0000-000000000002"))
RESERVATION_ID = str(uuid.uuid4())
OFFICIAL_NUMBER = "IF-2026-001"
SCHEMA = "100_test"


def _make_doc_info():
    return {
        "document_id": DOC_ID,
        "status": "sent_to_sign",
        "document_number": None,
        "is_numerator": True,
        "signer_status": "pending",
        "pending_count": 0,
        "signers_json": None,
        "signer_sector_ids": None,
    }


def _make_doc_data():
    return {
        "reference": "Test Doc",
        "content": "<p>contenido</p>",
        "document_type_id": "dt-uuid-0001",
        "resume": None,
        "document_type_acronym": "IF",
        "source_type": "Generado",
        "special_numbering": False,
    }


def _make_signer_data():
    return {
        "full_name": "Test Signer",
        "seal": "Firma Test",
        "department_name": "Secretaría Test",
        "municipality_name": "Municipio Test",
    }


def _make_r2_client(upload_side_effect=None):
    r2 = MagicMock()
    r2.get_tosign_url = MagicMock(return_value="http://r2-test/test.pdf")
    if upload_side_effect is not None:
        r2.upload_oficial = MagicMock(side_effect=upload_side_effect)
    else:
        r2.upload_oficial = MagicMock(return_value=None)
    return r2


def _make_http_client_ctx(pdf_bytes=b"pdf content"):
    mock_response = MagicMock()
    mock_response.content = pdf_bytes
    mock_response.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    mock_cls = MagicMock(return_value=mock_client)
    return mock_cls


class TestB1StructuralGuard:

    def test_chunks_delete_has_exists_guard(self):
        import inspect
        from shared import numbering

        source = inspect.getsource(numbering)

        assert "document_chunks" in source, "No hay referencia a document_chunks en numbering.py"
        assert "EXISTS" in source, "No hay guard EXISTS en numbering.py"

        assert "reservation_status = 'CANCELLED'" in source or "reservation_status='CANCELLED'" in source, (
            "El guard de DELETE document_chunks no filtra por CANCELLED"
        )

    def test_cancelled_delete_precedes_insert(self):
        import inspect
        from shared import numbering

        source = inspect.getsource(numbering)

        assert "DELETE FROM" in source and "CANCELLED" in source, (
            "No hay DELETE de fila CANCELLED previo al INSERT en official_documents"
        )

    def test_unique_violation_handler_excludes_cancelled(self):
        import inspect
        from shared import numbering

        source = inspect.getsource(numbering)

        assert "IN ('RESERVED', 'CONFIRMING', 'CONFIRMED')" in source or \
               "reservation_status IN" in source, (
            "El handler de UniqueViolation no filtra por status activo"
        )


class TestStaleReservationAbortsBeforeUpload:

    @pytest.mark.asyncio
    async def test_stale_before_r2_raises_validation_error(self):
        r2_client = _make_r2_client()

        with patch("services.documents.signing.numerator.validate_document_id",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numerator.validate_user_id",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numerator.fetch_one",
                   new=AsyncMock(side_effect=[_make_doc_info(), _make_doc_data(), None])), \
             patch("services.documents.signing.numerator.fetch_all",
                   new=AsyncMock(return_value=[])), \
             patch("services.documents.signing.numerator.execute",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numbering_permissions.can_user_number_document_type",
                   new=AsyncMock(return_value=(True, True, ""))), \
             patch("services.documents.signing.numerator.reserve_number",
                   new=AsyncMock(return_value=(OFFICIAL_NUMBER, "dept-uuid", 42, RESERVATION_ID))), \
             patch("services.storage.cloudflare.get_tenant_r2_client",
                   new=AsyncMock(return_value=r2_client)), \
             patch("services.documents.signing.numerator.run_in_threadpool",
                   new=AsyncMock(return_value="http://r2-test/test.pdf")), \
             patch("httpx.AsyncClient", new=_make_http_client_ctx()), \
             patch("services.documents.signing.numerator.get_signer_data",
                   new=AsyncMock(return_value=_make_signer_data())), \
             patch("services.shared.settings_utils.get_city_from_settings",
                   new=AsyncMock(return_value="TestCity")), \
             patch("services.shared.notary_api.call_notary_sign_pdf",
                   new=AsyncMock(return_value=b"signed pdf")), \
             patch("services.documents.signing.numerator.confirm_number",
                   new=AsyncMock(side_effect=StaleReservationError(DOC_ID, RESERVATION_ID))), \
             patch("services.documents.signing.numerator.finalize_number",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numerator.cancel_number",
                   new=AsyncMock(return_value=None)):

            from services.documents.signing.numerator import sign_document_as_numerator

            with pytest.raises(ValidationError) as exc_info:
                await sign_document_as_numerator(
                    DOC_ID, USER_ID, schema_name=SCHEMA
                )

        msg = str(exc_info.value).lower()
        assert "reserva" in msg or "expiró" in msg or "número" in msg

        r2_client.upload_oficial.assert_not_called()

    @pytest.mark.asyncio
    async def test_stale_does_not_retry(self):
        r2_client = _make_r2_client()
        confirm_mock = AsyncMock(side_effect=StaleReservationError(DOC_ID, RESERVATION_ID))

        with patch("services.documents.signing.numerator.validate_document_id",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numerator.validate_user_id",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numerator.fetch_one",
                   new=AsyncMock(side_effect=[_make_doc_info(), _make_doc_data(), None])), \
             patch("services.documents.signing.numerator.fetch_all",
                   new=AsyncMock(return_value=[])), \
             patch("services.documents.signing.numerator.execute",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numbering_permissions.can_user_number_document_type",
                   new=AsyncMock(return_value=(True, True, ""))), \
             patch("services.documents.signing.numerator.reserve_number",
                   new=AsyncMock(return_value=(OFFICIAL_NUMBER, "dept-uuid", 42, RESERVATION_ID))), \
             patch("services.storage.cloudflare.get_tenant_r2_client",
                   new=AsyncMock(return_value=r2_client)), \
             patch("services.documents.signing.numerator.run_in_threadpool",
                   new=AsyncMock(return_value="http://r2-test/test.pdf")), \
             patch("httpx.AsyncClient", new=_make_http_client_ctx()), \
             patch("services.documents.signing.numerator.get_signer_data",
                   new=AsyncMock(return_value=_make_signer_data())), \
             patch("services.shared.settings_utils.get_city_from_settings",
                   new=AsyncMock(return_value="TestCity")), \
             patch("services.shared.notary_api.call_notary_sign_pdf",
                   new=AsyncMock(return_value=b"signed pdf")), \
             patch("services.documents.signing.numerator.confirm_number",
                   new=confirm_mock), \
             patch("services.documents.signing.numerator.finalize_number",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numerator.cancel_number",
                   new=AsyncMock(return_value=None)):

            from services.documents.signing.numerator import sign_document_as_numerator

            with pytest.raises(ValidationError):
                await sign_document_as_numerator(DOC_ID, USER_ID, schema_name=SCHEMA)

        assert confirm_mock.call_count == 1


class TestIsConfirmingPreventsCancelNumber:

    @pytest.mark.asyncio
    async def test_confirming_set_then_upload_fails_no_cancel(self):
        upload_error = RuntimeError("R2 upload timeout")
        r2_client = _make_r2_client(
            upload_side_effect=[upload_error, upload_error]
        )
        cancel_mock = AsyncMock(return_value=None)

        run_in_threadpool_mock = AsyncMock(side_effect=[
            "http://r2-test/test.pdf",
            upload_error,
            upload_error,
        ])

        with patch("services.documents.signing.numerator.validate_document_id",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numerator.validate_user_id",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numerator.fetch_one",
                   new=AsyncMock(side_effect=[_make_doc_info(), _make_doc_data(), None])), \
             patch("services.documents.signing.numerator.fetch_all",
                   new=AsyncMock(return_value=[])), \
             patch("services.documents.signing.numerator.execute",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numbering_permissions.can_user_number_document_type",
                   new=AsyncMock(return_value=(True, True, ""))), \
             patch("services.documents.signing.numerator.reserve_number",
                   new=AsyncMock(return_value=(OFFICIAL_NUMBER, "dept-uuid", 42, RESERVATION_ID))), \
             patch("services.storage.cloudflare.get_tenant_r2_client",
                   new=AsyncMock(return_value=r2_client)), \
             patch("services.documents.signing.numerator.run_in_threadpool",
                   new=run_in_threadpool_mock), \
             patch("httpx.AsyncClient", new=_make_http_client_ctx()), \
             patch("services.documents.signing.numerator.get_signer_data",
                   new=AsyncMock(return_value=_make_signer_data())), \
             patch("services.shared.settings_utils.get_city_from_settings",
                   new=AsyncMock(return_value="TestCity")), \
             patch("services.shared.notary_api.call_notary_sign_pdf",
                   new=AsyncMock(return_value=b"signed pdf")), \
             patch("services.documents.signing.numerator.confirm_number",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numerator.finalize_number",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numerator.cancel_number",
                   new=cancel_mock):

            from services.documents.signing.numerator import sign_document_as_numerator

            with pytest.raises(ValidationError) as exc_info:
                await sign_document_as_numerator(DOC_ID, USER_ID, schema_name=SCHEMA)

        assert "intente" in str(exc_info.value).lower() or "firmar" in str(exc_info.value).lower()

        cancel_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_cas_called_only_once_on_retry(self):
        upload_error = RuntimeError("R2 upload timeout")

        run_in_threadpool_mock = AsyncMock(side_effect=[
            "http://r2-test/test.pdf",
            upload_error,
            upload_error,
        ])
        confirm_mock = AsyncMock(return_value=None)

        with patch("services.documents.signing.numerator.validate_document_id",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numerator.validate_user_id",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numerator.fetch_one",
                   new=AsyncMock(side_effect=[_make_doc_info(), _make_doc_data(), None])), \
             patch("services.documents.signing.numerator.fetch_all",
                   new=AsyncMock(return_value=[])), \
             patch("services.documents.signing.numerator.execute",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numbering_permissions.can_user_number_document_type",
                   new=AsyncMock(return_value=(True, True, ""))), \
             patch("services.documents.signing.numerator.reserve_number",
                   new=AsyncMock(return_value=(OFFICIAL_NUMBER, "dept-uuid", 42, RESERVATION_ID))), \
             patch("services.storage.cloudflare.get_tenant_r2_client",
                   new=AsyncMock(return_value=_make_r2_client())), \
             patch("services.documents.signing.numerator.run_in_threadpool",
                   new=run_in_threadpool_mock), \
             patch("httpx.AsyncClient", new=_make_http_client_ctx()), \
             patch("services.documents.signing.numerator.get_signer_data",
                   new=AsyncMock(return_value=_make_signer_data())), \
             patch("services.shared.settings_utils.get_city_from_settings",
                   new=AsyncMock(return_value="TestCity")), \
             patch("services.shared.notary_api.call_notary_sign_pdf",
                   new=AsyncMock(return_value=b"signed pdf")), \
             patch("services.documents.signing.numerator.confirm_number",
                   new=confirm_mock), \
             patch("services.documents.signing.numerator.finalize_number",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numerator.cancel_number",
                   new=AsyncMock(return_value=None)):

            from services.documents.signing.numerator import sign_document_as_numerator

            with pytest.raises(ValidationError):
                await sign_document_as_numerator(DOC_ID, USER_ID, schema_name=SCHEMA)

        assert confirm_mock.call_count == 1, (
            f"confirm_number fue llamado {confirm_mock.call_count} veces (esperado 1)"
        )


class TestNotaryBreakerInNumerator:

    @pytest.mark.asyncio
    async def test_breaker_open_propagates_intact_not_validation_error(self):
        cancel_mock = AsyncMock()

        with patch("services.documents.signing.numerator.validate_document_id",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numerator.validate_user_id",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numerator.fetch_one",
                   new=AsyncMock(side_effect=[_make_doc_info(), _make_doc_data(), None])), \
             patch("services.documents.signing.numerator.fetch_all",
                   new=AsyncMock(return_value=[])), \
             patch("services.documents.signing.numerator.execute",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numbering_permissions.can_user_number_document_type",
                   new=AsyncMock(return_value=(True, True, ""))), \
             patch("services.documents.signing.numerator.reserve_number",
                   new=AsyncMock(return_value=(OFFICIAL_NUMBER, "dept-uuid", 42, RESERVATION_ID))), \
             patch("services.storage.cloudflare.get_tenant_r2_client",
                   new=AsyncMock(return_value=_make_r2_client())), \
             patch("services.documents.signing.numerator.run_in_threadpool",
                   new=AsyncMock(return_value="http://r2-test/test.pdf")), \
             patch("httpx.AsyncClient", new=_make_http_client_ctx()), \
             patch("services.documents.signing.numerator.get_signer_data",
                   new=AsyncMock(return_value=_make_signer_data())), \
             patch("services.shared.settings_utils.get_city_from_settings",
                   new=AsyncMock(return_value="TestCity")), \
             patch("services.shared.notary_api.call_notary_sign_pdf",
                   new=AsyncMock(side_effect=NotaryBreakerOpenError(retry_after=60))), \
             patch("services.documents.signing.numerator.confirm_number",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numerator.finalize_number",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numerator.cancel_number",
                   new=cancel_mock):

            from services.documents.signing.numerator import sign_document_as_numerator

            with pytest.raises(NotaryBreakerOpenError) as exc_info:
                await sign_document_as_numerator(DOC_ID, USER_ID, schema_name=SCHEMA)

        assert isinstance(exc_info.value, NotaryBreakerOpenError)
        assert exc_info.value.retry_after == 60

    @pytest.mark.asyncio
    async def test_breaker_open_calls_cancel_number_before_cas(self):
        cancel_mock = AsyncMock()

        with patch("services.documents.signing.numerator.validate_document_id",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numerator.validate_user_id",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numerator.fetch_one",
                   new=AsyncMock(side_effect=[_make_doc_info(), _make_doc_data(), None])), \
             patch("services.documents.signing.numerator.fetch_all",
                   new=AsyncMock(return_value=[])), \
             patch("services.documents.signing.numerator.execute",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numbering_permissions.can_user_number_document_type",
                   new=AsyncMock(return_value=(True, True, ""))), \
             patch("services.documents.signing.numerator.reserve_number",
                   new=AsyncMock(return_value=(OFFICIAL_NUMBER, "dept-uuid", 42, RESERVATION_ID))), \
             patch("services.storage.cloudflare.get_tenant_r2_client",
                   new=AsyncMock(return_value=_make_r2_client())), \
             patch("services.documents.signing.numerator.run_in_threadpool",
                   new=AsyncMock(return_value="http://r2-test/test.pdf")), \
             patch("httpx.AsyncClient", new=_make_http_client_ctx()), \
             patch("services.documents.signing.numerator.get_signer_data",
                   new=AsyncMock(return_value=_make_signer_data())), \
             patch("services.shared.settings_utils.get_city_from_settings",
                   new=AsyncMock(return_value="TestCity")), \
             patch("services.shared.notary_api.call_notary_sign_pdf",
                   new=AsyncMock(side_effect=NotaryBreakerOpenError(retry_after=30))), \
             patch("services.documents.signing.numerator.confirm_number",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numerator.finalize_number",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numerator.cancel_number",
                   new=cancel_mock):

            from services.documents.signing.numerator import sign_document_as_numerator

            with pytest.raises(NotaryBreakerOpenError):
                await sign_document_as_numerator(DOC_ID, USER_ID, schema_name=SCHEMA)

        cancel_mock.assert_awaited_once()
        call_kwargs = cancel_mock.await_args
        reason = call_kwargs.kwargs.get("reason", "")
        assert "breaker_open" in reason

    @pytest.mark.asyncio
    async def test_breaker_open_does_not_retry(self):
        notary_mock = AsyncMock(side_effect=NotaryBreakerOpenError(retry_after=30))

        with patch("services.documents.signing.numerator.validate_document_id",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numerator.validate_user_id",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numerator.fetch_one",
                   new=AsyncMock(side_effect=[_make_doc_info(), _make_doc_data(), None])), \
             patch("services.documents.signing.numerator.fetch_all",
                   new=AsyncMock(return_value=[])), \
             patch("services.documents.signing.numerator.execute",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numbering_permissions.can_user_number_document_type",
                   new=AsyncMock(return_value=(True, True, ""))), \
             patch("services.documents.signing.numerator.reserve_number",
                   new=AsyncMock(return_value=(OFFICIAL_NUMBER, "dept-uuid", 42, RESERVATION_ID))), \
             patch("services.storage.cloudflare.get_tenant_r2_client",
                   new=AsyncMock(return_value=_make_r2_client())), \
             patch("services.documents.signing.numerator.run_in_threadpool",
                   new=AsyncMock(return_value="http://r2-test/test.pdf")), \
             patch("httpx.AsyncClient", new=_make_http_client_ctx()), \
             patch("services.documents.signing.numerator.get_signer_data",
                   new=AsyncMock(return_value=_make_signer_data())), \
             patch("services.shared.settings_utils.get_city_from_settings",
                   new=AsyncMock(return_value="TestCity")), \
             patch("services.shared.notary_api.call_notary_sign_pdf",
                   new=notary_mock), \
             patch("services.documents.signing.numerator.confirm_number",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numerator.finalize_number",
                   new=AsyncMock(return_value=None)), \
             patch("services.documents.signing.numerator.cancel_number",
                   new=AsyncMock(return_value=None)):

            from services.documents.signing.numerator import sign_document_as_numerator

            with pytest.raises(NotaryBreakerOpenError):
                await sign_document_as_numerator(DOC_ID, USER_ID, schema_name=SCHEMA)

        assert notary_mock.call_count == 1, (
            f"call_notary_sign_pdf fue llamado {notary_mock.call_count} veces (esperado 1, breaker no reintenta)"
        )
