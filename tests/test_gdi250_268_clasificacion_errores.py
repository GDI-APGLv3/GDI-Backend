
import pytest
from unittest.mock import AsyncMock, patch

from shared.exceptions import (
    SignerTurnPendingError,
    TransientLookupError,
    DatabaseBusyError,
    exception_to_http_exception,
)
from services.documents.signing import lookup_guard


class TestLookupGuardConfirmaAntesDe404:

    @pytest.mark.asyncio
    async def test_documento_existe_entonces_es_transitorio(self):
        with patch.object(lookup_guard, "fetch_val", AsyncMock(return_value=1)):
            with pytest.raises(TransientLookupError):
                await lookup_guard.confirm_document_missing(
                    "11111111-1111-1111-1111-111111111111",
                    schema_name="100_test",
                    context="test",
                )

    @pytest.mark.asyncio
    async def test_documento_no_existe_entonces_deja_pasar_el_404(self):
        with patch.object(lookup_guard, "fetch_val", AsyncMock(return_value=None)):
            await lookup_guard.confirm_document_missing(
                "11111111-1111-1111-1111-111111111111",
                schema_name="100_test",
                context="test",
            )

    @pytest.mark.asyncio
    async def test_si_no_se_puede_confirmar_es_transitorio(self):
        with patch.object(lookup_guard, "fetch_val", AsyncMock(side_effect=Exception("pool timeout"))):
            with pytest.raises(TransientLookupError):
                await lookup_guard.confirm_document_missing(
                    "11111111-1111-1111-1111-111111111111",
                    schema_name="100_test",
                    context="test",
                )

    @pytest.mark.asyncio
    async def test_mismo_criterio_para_usuarios(self):
        with patch.object(lookup_guard, "fetch_val", AsyncMock(return_value=1)):
            with pytest.raises(TransientLookupError):
                await lookup_guard.confirm_user_missing(
                    "22222222-2222-2222-2222-222222222222",
                    schema_name="100_test",
                    context="test",
                )

    @pytest.mark.asyncio
    async def test_usuario_confirmado_inexistente_deja_pasar(self):
        with patch.object(lookup_guard, "fetch_val", AsyncMock(return_value=None)):
            await lookup_guard.confirm_user_missing(
                "22222222-2222-2222-2222-222222222222",
                schema_name="100_test",
                context="test",
            )


class TestMapeoHttp:

    def test_transient_lookup_es_503(self):
        exc = exception_to_http_exception(TransientLookupError("no disponible"))
        assert exc.status_code == 503

    def test_transient_lookup_trae_retry_after(self):
        exc = exception_to_http_exception(TransientLookupError("no disponible"))
        assert "Retry-After" in (exc.headers or {})

    def test_transient_lookup_hereda_de_database_busy(self):
        assert issubclass(TransientLookupError, DatabaseBusyError)

    def test_turno_pendiente_es_409(self):
        exc = exception_to_http_exception(SignerTurnPendingError(1))
        assert exc.status_code == 409

    def test_turno_pendiente_no_es_500(self):
        exc = exception_to_http_exception(SignerTurnPendingError(2))
        assert exc.status_code != 500

    def test_mensaje_de_turno_dice_que_reintente(self):
        exc = SignerTurnPendingError(3)
        assert "3" in exc.message
        assert "reintent" in exc.message.lower()

    def test_expone_cuantas_firmas_faltan(self):
        assert SignerTurnPendingError(2).pending_common_signers == 2


class TestHandlersLoTraducen:

    def test_super_sign_maneja_ambas(self):
        import inspect
        from endpoints.documents import super_sign

        source = inspect.getsource(super_sign)
        assert "except SignerTurnPendingError" in source
        assert "except TransientLookupError" in source

    def test_gateway_rest_maneja_ambas(self):
        import inspect
        from api_gateway import rest_api

        source = inspect.getsource(rest_api.api_sign_document)
        assert "SignerTurnPendingError" in source
        assert "TransientLookupError" in source

    def test_los_dos_chequeos_de_turno_usan_la_excepcion_nueva(self):
        import inspect
        from services.documents.signing import unified_signing, numerator

        for mod in (unified_signing, numerator):
            source = inspect.getsource(mod)
            assert "SignerTurnPendingError" in source, mod.__name__
            assert "El numerador debe firmar al final" not in source, mod.__name__

    def test_los_lookups_de_firma_pasan_por_el_guard(self):
        import inspect
        from services.documents.signing import unified_signing, numerator
        from endpoints.documents import super_sign

        assert inspect.getsource(unified_signing).count("confirm_document_missing") >= 1
        assert inspect.getsource(numerator).count("confirm_document_missing") >= 2
        assert "confirm_user_missing" in inspect.getsource(super_sign)
