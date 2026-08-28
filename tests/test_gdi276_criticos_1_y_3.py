
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi import HTTPException

from shared.exceptions import TransientLookupError
from services.documents.signing import lookup_guard


class TestConfirmSignaturePolicyMissing:

    DOC = "11111111-1111-1111-1111-111111111111"
    USER = "22222222-2222-2222-2222-222222222222"

    @pytest.mark.asyncio
    async def test_fila_existe_pero_lectura_previa_mintio_es_503(self):
        with patch.object(lookup_guard, "fetch_val", AsyncMock(return_value=1)):
            with pytest.raises(TransientLookupError):
                await lookup_guard.confirm_signature_policy_missing(
                    self.DOC, self.USER,
                    schema_name="100_test",
                    context="test",
                )

    @pytest.mark.asyncio
    async def test_fila_confirmada_inexistente_deja_pasar(self):
        with patch.object(lookup_guard, "fetch_val", AsyncMock(return_value=None)):
            await lookup_guard.confirm_signature_policy_missing(
                self.DOC, self.USER,
                schema_name="100_test",
                context="test",
            )

    @pytest.mark.asyncio
    async def test_si_la_bd_no_responde_es_transitorio(self):
        with patch.object(
            lookup_guard, "fetch_val",
            AsyncMock(side_effect=Exception("pool timeout")),
        ):
            with pytest.raises(TransientLookupError):
                await lookup_guard.confirm_signature_policy_missing(
                    self.DOC, self.USER,
                    schema_name="100_test",
                    context="test",
                )


class TestResolveSignaturePolicy:

    DOC = "11111111-1111-1111-1111-111111111111"
    USER = "22222222-2222-2222-2222-222222222222"

    @pytest.mark.asyncio
    async def test_devuelve_tupla_para_config_valida(self):
        row = {"signature_policy": "digital_all", "is_numerator": True}
        with patch.object(lookup_guard, "fetch_one", AsyncMock(return_value=row)):
            policy, is_num = await lookup_guard.resolve_signature_policy(
                self.DOC, self.USER,
                schema_name="100_test", context="t",
            )
        assert policy == "digital_all"
        assert is_num is True

    @pytest.mark.asyncio
    async def test_none_confirma_y_si_es_fantasma_es_503(self):
        with patch.object(lookup_guard, "fetch_one", AsyncMock(return_value=None)), \
             patch.object(lookup_guard, "fetch_val", AsyncMock(return_value=1)):
            with pytest.raises(TransientLookupError):
                await lookup_guard.resolve_signature_policy(
                    self.DOC, self.USER,
                    schema_name="100_test", context="t",
                )

    @pytest.mark.asyncio
    async def test_none_confirmado_ausente_es_validation_error(self):
        from shared.exceptions import ValidationError as _VE
        with patch.object(lookup_guard, "fetch_one", AsyncMock(return_value=None)), \
             patch.object(lookup_guard, "fetch_val", AsyncMock(return_value=None)):
            with pytest.raises(_VE):
                await lookup_guard.resolve_signature_policy(
                    self.DOC, self.USER,
                    schema_name="100_test", context="t",
                )

    @pytest.mark.asyncio
    async def test_signature_policy_null_es_validation_error(self):
        from shared.exceptions import ValidationError as _VE
        row = {"signature_policy": None, "is_numerator": True}
        with patch.object(lookup_guard, "fetch_one", AsyncMock(return_value=row)):
            with pytest.raises(_VE):
                await lookup_guard.resolve_signature_policy(
                    self.DOC, self.USER,
                    schema_name="100_test", context="t",
                )

    @pytest.mark.asyncio
    async def test_signature_policy_string_vacio_tambien_es_validation_error(self):
        from shared.exceptions import ValidationError as _VE
        row = {"signature_policy": "", "is_numerator": False}
        with patch.object(lookup_guard, "fetch_one", AsyncMock(return_value=row)):
            with pytest.raises(_VE):
                await lookup_guard.resolve_signature_policy(
                    self.DOC, self.USER,
                    schema_name="100_test", context="t",
                )


class TestRestApiUsaLaMismaPuerta:

    @pytest.mark.asyncio
    async def test_rest_api_llama_al_helper_compartido(self):
        from api_gateway import rest_api_signing as rest_mod

        ctx = MagicMock()
        ctx.schema_name = "100_test"

        request = MagicMock()
        request.path_params = {"document_id": "11111111-1111-1111-1111-111111111111"}
        request.headers = {"X-API-Key": "k", "X-User-ID": "22222222-2222-2222-2222-222222222222"}

        helper_spy = AsyncMock(return_value=("electronic", False))
        sign_mock = AsyncMock(return_value={"status": "ok"})

        with patch.object(rest_mod, "validate_rest_api_key",
                          AsyncMock(return_value=ctx)), \
             patch(
                 "services.documents.signing.lookup_guard.resolve_signature_policy",
                 helper_spy,
             ), \
             patch.object(rest_mod.documents, "sign_document", sign_mock):
            resp = await rest_mod.api_sign_document(request)

        assert helper_spy.await_count == 1, (
            "REGRESIÓN GDI-276: rest_api NO delegó en resolve_signature_policy "
            "— la puerta REST volvió a resolver la política por su cuenta"
        )
        assert helper_spy.await_args.kwargs["schema_name"] == "100_test"
        assert helper_spy.await_args.kwargs["context"] == "rest_api.sign_document"
        assert sign_mock.await_count == 1
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_rest_api_con_policy_null_no_firma(self):
        from api_gateway import rest_api_signing as rest_mod
        from shared.exceptions import ValidationError as _VE

        ctx = MagicMock()
        ctx.schema_name = "100_test"

        request = MagicMock()
        request.path_params = {"document_id": "11111111-1111-1111-1111-111111111111"}
        request.headers = {"X-API-Key": "k", "X-User-ID": "22222222-2222-2222-2222-222222222222"}

        sign_mock = AsyncMock(return_value={"status": "ok"})

        with patch.object(rest_mod, "validate_rest_api_key",
                          AsyncMock(return_value=ctx)), \
             patch(
                 "services.documents.signing.lookup_guard.resolve_signature_policy",
                 AsyncMock(side_effect=_VE(
                     "El tipo de documento no tiene configurada una política "
                     "de firma (signature_policy es NULL)."
                 )),
             ), \
             patch.object(rest_mod.documents, "sign_document", sign_mock):
            resp = await rest_mod.api_sign_document(request)

        assert resp.status_code == 422
        assert sign_mock.await_count == 0, (
            "REGRESIÓN CRÍTICO 1 (REST): NULL degradó a electrónica"
        )


class TestSuperSignNoDegradaAElectronicoSilenciosamente:

    @pytest.mark.asyncio
    async def test_phantom_policy_missing_nunca_dispara_firma_electronica(self):
        from endpoints.documents import super_sign as endpoint_mod

        endpoint_fetch_one = AsyncMock(return_value={
            "user_id": "22222222-2222-2222-2222-222222222222",
            "full_name": "Firmante Test",
        })
        helper_fetch_one = AsyncMock(return_value=None)
        confirm_val_mock = AsyncMock(return_value=1)
        elec_mock = AsyncMock(return_value={"flow": "electronic"})
        dig_mock = AsyncMock(return_value={"flow": "digital"})

        request = MagicMock()
        request.state.tenant_user_id = "22222222-2222-2222-2222-222222222222"
        request.client = None
        request.headers = {}

        body = MagicMock()
        body.provider_name = None
        current_user = MagicMock()

        with patch.object(endpoint_mod, "fetch_one", endpoint_fetch_one), \
             patch.object(lookup_guard, "fetch_one", helper_fetch_one), \
             patch.object(lookup_guard, "fetch_val", confirm_val_mock), \
             patch.object(endpoint_mod, "super_sign_document", elec_mock), \
             patch(
                 "services.documents.signing.dispatcher.dispatch_digital_signing",
                 dig_mock,
             ):
            with pytest.raises(HTTPException) as excinfo:
                await endpoint_mod.super_sign(
                    request=request,
                    document_id="11111111-1111-1111-1111-111111111111",
                    body=body,
                    current_user=current_user,
                    schema_name="100_test",
                )

        assert excinfo.value.status_code == 503
        assert elec_mock.await_count == 0, (
            "REGRESIÓN CRÍTICA: cayó al default electrónico con pol_row=None"
        )
        assert dig_mock.await_count == 0

    @pytest.mark.asyncio
    async def test_policy_de_verdad_ausente_es_validation_error_no_electronica(self):
        from endpoints.documents import super_sign as endpoint_mod

        endpoint_fetch_one = AsyncMock(return_value={
            "user_id": "22222222-2222-2222-2222-222222222222",
            "full_name": "Firmante Test",
        })
        helper_fetch_one = AsyncMock(return_value=None)
        confirm_val_mock = AsyncMock(return_value=None)
        elec_mock = AsyncMock(return_value={"flow": "electronic"})
        dig_mock = AsyncMock(return_value={"flow": "digital"})

        request = MagicMock()
        request.state.tenant_user_id = "22222222-2222-2222-2222-222222222222"
        request.client = None
        request.headers = {}

        body = MagicMock()
        body.provider_name = None
        current_user = MagicMock()

        with patch.object(endpoint_mod, "fetch_one", endpoint_fetch_one), \
             patch.object(lookup_guard, "fetch_one", helper_fetch_one), \
             patch.object(lookup_guard, "fetch_val", confirm_val_mock), \
             patch.object(endpoint_mod, "super_sign_document", elec_mock), \
             patch(
                 "services.documents.signing.dispatcher.dispatch_digital_signing",
                 dig_mock,
             ):
            with pytest.raises(HTTPException) as excinfo:
                await endpoint_mod.super_sign(
                    request=request,
                    document_id="11111111-1111-1111-1111-111111111111",
                    body=body,
                    current_user=current_user,
                    schema_name="100_test",
                )

        assert excinfo.value.status_code == 400
        assert elec_mock.await_count == 0
        assert dig_mock.await_count == 0

    @pytest.mark.asyncio
    async def test_signature_policy_null_no_degrada_a_electronica(self):
        from endpoints.documents import super_sign as endpoint_mod

        endpoint_fetch_one = AsyncMock(return_value={
            "user_id": "22222222-2222-2222-2222-222222222222",
            "full_name": "Firmante Test",
        })
        helper_fetch_one = AsyncMock(return_value={
            "signature_policy": None,
            "is_numerator": True,
        })
        elec_mock = AsyncMock(return_value={"flow": "electronic"})
        dig_mock = AsyncMock(return_value={"flow": "digital"})

        request = MagicMock()
        request.state.tenant_user_id = "22222222-2222-2222-2222-222222222222"
        request.client = None
        request.headers = {}

        body = MagicMock()
        body.provider_name = None
        current_user = MagicMock()

        with patch.object(endpoint_mod, "fetch_one", endpoint_fetch_one), \
             patch.object(lookup_guard, "fetch_one", helper_fetch_one), \
             patch.object(endpoint_mod, "super_sign_document", elec_mock), \
             patch(
                 "services.documents.signing.dispatcher.dispatch_digital_signing",
                 dig_mock,
             ):
            with pytest.raises(HTTPException) as excinfo:
                await endpoint_mod.super_sign(
                    request=request,
                    document_id="11111111-1111-1111-1111-111111111111",
                    body=body,
                    current_user=current_user,
                    schema_name="100_test",
                )

        assert excinfo.value.status_code == 400
        assert elec_mock.await_count == 0, (
            "REGRESIÓN CRÍTICO 1 camino 2: signature_policy NULL degradó a electrónica"
        )
        assert dig_mock.await_count == 0


class TestEscriResumeNoPisaConfirmed:

    @pytest.mark.asyncio
    async def test_guard_falla_a_leer_reencola_y_no_re_firma(self):
        from workers import escri as escri_mod

        job = {
            "session_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "document_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "schema_name": "100_test",
            "user_id":     "cccccccc-cccc-cccc-cccc-cccccccccccc",
            "reservation_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
            "payload": {
                "is_confirming":   True,
                "official_number": "EX-2026-0000123-TEST",
            },
        }

        fetch_one_mock = AsyncMock(side_effect=Exception("pool timeout"))

        notary_mock = AsyncMock(return_value=b"nunca-me-llamaron")
        r2_client = MagicMock()
        r2_client.upload_oficial = MagicMock(return_value="oficial")
        r2_client.get_tosign_url = MagicMock(return_value="http://tosign/x.pdf")
        get_r2_mock = AsyncMock(return_value=r2_client)

        requeue_mock = AsyncMock()

        import asyncio
        heartbeat_lost = asyncio.Event()
        async def _dummy_hb():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass
        heartbeat_task = asyncio.create_task(_dummy_hb())

        worker = escri_mod.EscriWorker.__new__(escri_mod.EscriWorker)
        worker._worker_id = "worker-test"
        worker._spawn_heartbeat = MagicMock(return_value=(heartbeat_task, heartbeat_lost))
        worker._get_official_number = AsyncMock(return_value="EX-2026-0000123-TEST")
        worker._mark_document_signed = AsyncMock()
        worker._mark_session_signed = AsyncMock()
        worker._mark_session_failed = AsyncMock()
        worker._get_source_type = AsyncMock(return_value="Comun")
        requeue_sin_techo_mock = AsyncMock()
        worker._requeue_session_pending = requeue_sin_techo_mock
        worker._requeue_guard_unverifiable = requeue_mock

        with patch.object(escri_mod, "fetch_one", fetch_one_mock), \
             patch.object(escri_mod, "get_tenant_r2_client", get_r2_mock), \
             patch.object(escri_mod, "call_notary_sign_pdf", notary_mock), \
             patch.object(escri_mod, "check_breaker_before_call", AsyncMock()), \
             patch.object(escri_mod, "confirm_number", AsyncMock()), \
             patch.object(escri_mod, "finalize_number", AsyncMock()):
            await worker._process_job(job)

        assert notary_mock.await_count == 0, (
            "REGRESIÓN GDI-276 CRÍTICO 3: el guard falla-abierto — bajo "
            "saturación (que es cuando se pierde la lectura) se re-firma "
            "sin saber si ya hay un PDF válido en R2"
        )
        assert r2_client.upload_oficial.call_count == 0, (
            "REGRESIÓN GDI-276 CRÍTICO 3: se subió el PDF sin verificar estado"
        )
        assert requeue_mock.await_count == 1
        assert requeue_mock.await_args.kwargs.get("retry_after", 0) >= 1
        assert requeue_sin_techo_mock.await_count == 0, (
            "el guard volvió a usar el reencolado sin techo: bajo saturación "
            "sostenida el job gira cada 30s para siempre"
        )

    @pytest.mark.asyncio
    async def test_guard_reencola_hasta_el_techo_y_despues_falla(self):
        from workers import escri as escri_mod
        from config.constants import ESCRI_GUARD_MAX_ATTEMPTS

        worker = escri_mod.EscriWorker.__new__(escri_mod.EscriWorker)
        worker._mark_session_failed = AsyncMock()
        execute_mock = AsyncMock()

        with patch.object(escri_mod, "execute", execute_mock):
            await worker._requeue_guard_unverifiable(
                session_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                doc_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                payload={"official_number": "EX-2026-0000123-TEST"},
                origen="reservation_check",
                retry_after=30,
            )
        assert execute_mock.await_count == 1, "debería haber reencolado"
        assert worker._mark_session_failed.await_count == 0
        payload_persistido = execute_mock.await_args.args[3]
        assert payload_persistido["guard_check_attempts"] == 1
        assert payload_persistido["official_number"] == "EX-2026-0000123-TEST"

        execute_mock.reset_mock()
        with patch.object(escri_mod, "execute", execute_mock):
            await worker._requeue_guard_unverifiable(
                session_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                doc_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                payload={"guard_check_attempts": ESCRI_GUARD_MAX_ATTEMPTS - 1},
                origen="reservation_check",
                retry_after=30,
            )
        assert execute_mock.await_count == 0, (
            "REGRESIÓN: el guard siguió reencolando después del techo — vuelve "
            "el giro infinito bajo saturación sostenida"
        )
        assert worker._mark_session_failed.await_count == 1
        motivo = worker._mark_session_failed.await_args.args[1]
        assert "guard_unverifiable" in motivo, (
            "el motivo tiene que decir por qué se detuvo, no quedar en 'unknown' "
            "como la deuda que ya arrastramos"
        )

    @pytest.mark.asyncio
    async def test_resume_con_reservation_confirmed_desvia_a_autoheal(self):
        from workers import escri as escri_mod

        job = {
            "session_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "document_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "schema_name": "100_test",
            "user_id":     "cccccccc-cccc-cccc-cccc-cccccccccccc",
            "reservation_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
            "payload": {
                "is_confirming":   True,
                "official_number": "EX-2026-0000123-TEST",
            },
        }

        fetch_one_mock = AsyncMock(return_value={"reservation_status": "CONFIRMED"})

        notary_mock = AsyncMock(return_value=b"nunca-me-llamaron")
        r2_client = MagicMock()
        r2_client.upload_oficial = MagicMock(return_value="oficial")
        r2_client.get_tosign_url = MagicMock(return_value="http://nunca")
        get_r2_mock = AsyncMock(return_value=r2_client)

        mark_doc_mock = AsyncMock()
        mark_session_mock = AsyncMock()

        import asyncio
        heartbeat_lost = asyncio.Event()
        async def _dummy_hb():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass
        heartbeat_task = asyncio.create_task(_dummy_hb())

        worker = escri_mod.EscriWorker.__new__(escri_mod.EscriWorker)
        worker._worker_id = "worker-test"
        worker._spawn_heartbeat = MagicMock(return_value=(heartbeat_task, heartbeat_lost))
        worker._get_official_number = AsyncMock(return_value="EX-2026-0000123-TEST")
        worker._mark_document_signed = mark_doc_mock
        worker._mark_session_signed = mark_session_mock
        worker._mark_session_failed = AsyncMock()
        worker._get_source_type = AsyncMock(return_value="Comun")
        worker._publish_public_with_retry = AsyncMock()

        with patch.object(escri_mod, "fetch_one", fetch_one_mock), \
             patch.object(escri_mod, "get_tenant_r2_client", get_r2_mock), \
             patch.object(escri_mod, "call_notary_sign_pdf", notary_mock), \
             patch.object(escri_mod, "check_breaker_before_call", AsyncMock()), \
             patch.object(escri_mod, "confirm_number", AsyncMock()), \
             patch.object(escri_mod, "finalize_number", AsyncMock()):
            await worker._process_job(job)

        assert notary_mock.await_count == 0, (
            "REGRESIÓN GDI-276 CRÍTICO 3: se re-firmó un documento ya CONFIRMED"
        )
        assert r2_client.upload_oficial.call_count == 0, (
            "REGRESIÓN GDI-276 CRÍTICO 3: se pisó el PDF en R2"
        )
        assert mark_session_mock.await_count == 1
        assert mark_doc_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_resume_con_reservation_confirming_sigue_re_firmando(self):
        from workers import escri as escri_mod

        job = {
            "session_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "document_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "schema_name": "100_test",
            "user_id":     "cccccccc-cccc-cccc-cccc-cccccccccccc",
            "reservation_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
            "payload": {
                "is_confirming":   True,
                "official_number": "EX-2026-0000123-TEST",
            },
        }

        fetch_one_mock = AsyncMock(return_value={"reservation_status": "CONFIRMING"})
        r2_client = MagicMock()
        r2_client.get_tosign_url = MagicMock(return_value=None)
        get_r2_mock = AsyncMock(return_value=r2_client)

        breaker_mock = AsyncMock()

        import asyncio
        heartbeat_lost = asyncio.Event()
        async def _dummy_hb():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass
        heartbeat_task = asyncio.create_task(_dummy_hb())

        worker = escri_mod.EscriWorker.__new__(escri_mod.EscriWorker)
        worker._worker_id = "worker-test"
        worker._spawn_heartbeat = MagicMock(return_value=(heartbeat_task, heartbeat_lost))
        worker._get_official_number = AsyncMock(return_value="EX-2026-0000123-TEST")
        worker._mark_document_signed = AsyncMock()
        worker._mark_session_signed = AsyncMock()
        worker._mark_session_failed = AsyncMock()
        worker._get_source_type = AsyncMock(return_value="Comun")

        with patch.object(escri_mod, "fetch_one", fetch_one_mock), \
             patch.object(escri_mod, "get_tenant_r2_client", get_r2_mock), \
             patch.object(escri_mod, "check_breaker_before_call", breaker_mock), \
             patch.object(escri_mod, "confirm_number", AsyncMock()), \
             patch.object(escri_mod, "finalize_number", AsyncMock()), \
             patch.object(escri_mod, "call_notary_sign_pdf", AsyncMock()):
            try:
                await worker._process_job(job)
            except Exception:
                pass

        assert breaker_mock.await_count >= 1, (
            "El guard cortó de más: CONFIRMING NO debe desviar a autoheal"
        )

    @pytest.mark.asyncio
    async def test_auto_promote_stale_a_confirmed_desvia_a_autoheal(self):
        from workers import escri as escri_mod

        job = {
            "session_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "document_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "schema_name": "100_test",
            "user_id":     "cccccccc-cccc-cccc-cccc-cccccccccccc",
            "reservation_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
            "payload": {
                "official_number": "EX-2026-0000123-TEST",
            },
        }

        fetch_one_mock = AsyncMock(side_effect=[
            {"reservation_status": "RESERVED"},
            {
                "reservation_status": "CONFIRMED",
                "reservation_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
            },
        ])

        notary_mock = AsyncMock(return_value=b"pdf-signed-fake")
        r2_client = MagicMock()
        r2_client.upload_oficial = MagicMock(return_value="oficial")
        r2_client.get_tosign_url = MagicMock(return_value="http://tosign.local/x.pdf")
        get_r2_mock = AsyncMock(return_value=r2_client)

        mark_doc_mock = AsyncMock()
        mark_session_mock = AsyncMock()

        from shared.exceptions import StaleReservationError
        confirm_mock = AsyncMock(side_effect=StaleReservationError("stale"))

        import asyncio
        heartbeat_lost = asyncio.Event()
        async def _dummy_hb():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass
        heartbeat_task = asyncio.create_task(_dummy_hb())

        signer_data = {
            "full_name": "F", "seal": None, "department_name": "D",
            "municipality_name": "M",
        }

        worker = escri_mod.EscriWorker.__new__(escri_mod.EscriWorker)
        worker._worker_id = "worker-test"
        worker._spawn_heartbeat = MagicMock(return_value=(heartbeat_task, heartbeat_lost))
        worker._get_official_number = AsyncMock(return_value="EX-2026-0000123-TEST")
        worker._mark_document_signed = mark_doc_mock
        worker._mark_session_signed = mark_session_mock
        worker._mark_session_failed = AsyncMock()
        worker._get_source_type = AsyncMock(return_value="Comun")
        worker._publish_public_with_retry = AsyncMock()

        class _FakeResp:
            status_code = 200
            headers = {"content-length": "10"}
            content = b"1234567890"
            def raise_for_status(self):
                pass
        class _FakeClient:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **kw): return _FakeResp()

        with patch.object(escri_mod, "fetch_one", fetch_one_mock), \
             patch.object(escri_mod, "get_tenant_r2_client", get_r2_mock), \
             patch.object(escri_mod, "call_notary_sign_pdf", notary_mock), \
             patch.object(escri_mod, "check_breaker_before_call", AsyncMock()), \
             patch.object(escri_mod, "confirm_number", confirm_mock), \
             patch.object(escri_mod, "finalize_number", AsyncMock()), \
             patch.object(escri_mod, "get_signer_data", AsyncMock(return_value=signer_data)), \
             patch.object(escri_mod, "get_city_from_settings", AsyncMock(return_value="Ciudad")), \
             patch.object(escri_mod.httpx, "AsyncClient", _FakeClient):
            await worker._process_job(job)

        assert confirm_mock.await_count == 1
        assert notary_mock.await_count == 1
        assert r2_client.upload_oficial.call_count == 0, (
            "REGRESIÓN GDI-276 CRÍTICO 3 (2ª puerta): auto-promote a resume "
            "con reservation CONFIRMED subió el PDF y pisó el firmado válido"
        )
        assert mark_session_mock.await_count == 1
        assert mark_doc_mock.await_count == 1
