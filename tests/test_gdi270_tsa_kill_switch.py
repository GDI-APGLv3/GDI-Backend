
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestFlagDefault:
    def test_default_es_apagado(self):
        import importlib
        import config.constants as c
        with patch.dict("os.environ", {}, clear=False):
            c_reload = importlib.reload(c)
            assert c_reload.TSA_DEFERRED_SEAL_ENABLED is False

    @pytest.mark.parametrize("valor,esperado", [
        ("true", True), ("True", True), ("1", True), ("yes", True), ("on", True),
        ("false", False), ("0", False), ("", False), ("cualquier-cosa", False),
    ])
    def test_parseo_de_la_env(self, valor, esperado):
        import importlib
        import config.constants as c
        with patch.dict("os.environ", {"TSA_DEFERRED_SEAL_ENABLED": valor}):
            assert importlib.reload(c).TSA_DEFERRED_SEAL_ENABLED is esperado

    def teardown_method(self):
        import importlib
        import config.constants as c
        importlib.reload(c)


def _r2_client(bucket_preoficial="tenant-preoficial"):
    from services.storage.cloudflare import CloudflareR2Client
    with patch.dict("os.environ", {
        "CF_R2_ENDPOINT": "https://r2.test",
        "CF_R2_ACCESS_KEY_ID": "k",
        "CF_R2_SECRET_ACCESS_KEY": "s",
    }):
        c = CloudflareR2Client(
            bucket_oficial="tenant-oficial",
            bucket_tosign="tenant-tosign",
            bucket_preoficial=bucket_preoficial,
        )
    c._client = MagicMock()
    return c


class TestResolveBucket:
    def test_oficial_por_default(self):
        assert _r2_client().resolve_pdf_bucket() == ("tenant-oficial", "oficial")

    def test_preoficial_cuando_esta_configurado(self):
        assert _r2_client().resolve_pdf_bucket("preoficial") == ("tenant-preoficial", "preoficial")

    def test_sin_preoficial_la_lectura_degrada_a_oficial(self):
        assert _r2_client(bucket_preoficial=None).resolve_pdf_bucket("preoficial") == (
            "tenant-oficial", "oficial",
        )

    def test_location_invalida_es_error(self):
        with pytest.raises(ValueError):
            _r2_client().resolve_pdf_bucket("staging")


class TestUploadRespetaLocation:
    def test_upload_preoficial_va_al_bucket_sin_lock(self):
        c = _r2_client()
        res = c.upload_oficial(b"%PDF-1.7 fake", "IF-2026-1.pdf", "preoficial")
        assert c._client.put_object.call_args.kwargs["Bucket"] == "tenant-preoficial"
        assert res["location"] == "preoficial"

    def test_upload_default_sigue_yendo_a_oficial(self):
        c = _r2_client()
        res = c.upload_oficial(b"%PDF-1.7 fake", "IF-2026-1.pdf")
        assert c._client.put_object.call_args.kwargs["Bucket"] == "tenant-oficial"
        assert res["location"] == "oficial"

    def test_sin_preoficial_la_escritura_falla_en_vez_de_ir_al_worm(self):
        from shared.exceptions import PreOficialNotProvisionedError

        c = _r2_client(bucket_preoficial=None)
        with pytest.raises(PreOficialNotProvisionedError):
            c.upload_oficial(b"%PDF-1.7 fake", "IF-2026-1.pdf", "preoficial")
        c._client.put_object.assert_not_called()

    def test_sin_preoficial_la_lectura_sigue_degradando(self):
        c = _r2_client(bucket_preoficial=None)
        assert c.resolve_pdf_bucket("preoficial") == ("tenant-oficial", "oficial")


class TestLecturaConFallback:
    def test_lee_del_bucket_pedido(self):
        c = _r2_client()
        c._client.get_object = MagicMock(
            return_value={"Body": MagicMock(read=MagicMock(return_value=b"pdf"))}
        )
        assert c.get_oficial_bytes("IF-2026-1", "preoficial") == b"pdf"
        assert c._client.get_object.call_args.kwargs["Bucket"] == "tenant-preoficial"

    def test_fallback_al_otro_bucket_ante_404(self):
        from botocore.exceptions import ClientError
        c = _r2_client()
        err = ClientError({"Error": {"Code": "NoSuchKey", "Message": "nope"}}, "GetObject")
        c._client.get_object = MagicMock(side_effect=[
            err,
            {"Body": MagicMock(read=MagicMock(return_value=b"pdf"))},
        ])
        assert c.get_oficial_bytes("IF-2026-1", "oficial") == b"pdf"
        assert c._client.get_object.call_args.kwargs["Bucket"] == "tenant-preoficial"

    def test_error_no_404_no_dispara_fallback(self):
        from botocore.exceptions import ClientError
        c = _r2_client()
        err = ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "GetObject")
        c._client.get_object = MagicMock(side_effect=err)
        assert c.get_oficial_bytes("IF-2026-1", "oficial") is None
        assert c._client.get_object.call_count == 1


class TestExistsAny:

    def test_any_encuentra_en_preoficial(self):
        from botocore.exceptions import ClientError
        c = _r2_client()
        err = ClientError({"Error": {"Code": "404", "Message": "x"}}, "HeadObject")
        c._client.head_object = MagicMock(side_effect=[err, {}])
        assert c.exists_oficial("IF-2026-1.pdf", "any") is True

    def test_any_false_si_no_esta_en_ninguno(self):
        from botocore.exceptions import ClientError
        c = _r2_client()
        err = ClientError({"Error": {"Code": "404", "Message": "x"}}, "HeadObject")
        c._client.head_object = MagicMock(side_effect=[err, err])
        assert c.exists_oficial("IF-2026-1.pdf", "any") is False

    def test_any_propaga_errores_de_verificacion(self):
        from botocore.exceptions import ClientError
        c = _r2_client()
        err = ClientError({"Error": {"Code": "AccessDenied", "Message": "x"}}, "HeadObject")
        c._client.head_object = MagicMock(side_effect=err)
        with pytest.raises(ClientError):
            c.exists_oficial("IF-2026-1.pdf", "any")


class TestTargetLocation:

    def test_diferido_apagado_va_derecho_a_oficial(self):
        with patch("services.storage.pdf_location.TSA_DEFERRED_SEAL_ENABLED", False):
            from services.storage.pdf_location import target_pdf_location
            assert target_pdf_location() == "oficial"

    def test_diferido_prendido_va_a_preoficial(self):
        with patch("services.storage.pdf_location.TSA_DEFERRED_SEAL_ENABLED", True):
            from services.storage.pdf_location import target_pdf_location
            assert target_pdf_location() == "preoficial"


class TestPersistLocation:
    @pytest.mark.asyncio
    async def test_oficial_no_escribe(self):
        from services.storage.pdf_location import persist_pdf_location
        m = AsyncMock()
        with patch("services.storage.pdf_location.execute", m):
            await persist_pdf_location("doc-1", "oficial", schema_name="100_test")
        m.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_preoficial_actualiza_por_id(self):
        from services.storage.pdf_location import persist_pdf_location
        m = AsyncMock()
        with patch("services.storage.pdf_location.execute", m):
            await persist_pdf_location("doc-1", "preoficial", schema_name="100_test")
        m.assert_awaited_once()
        assert "pdf_location" in m.call_args.args[0]
        assert m.call_args.args[1] == "doc-1"
        assert m.call_args.kwargs["schema_name"] == "100_test"

    @pytest.mark.asyncio
    async def test_sin_id_cae_al_numero(self):
        from services.storage.pdf_location import persist_pdf_location
        m = AsyncMock()
        with patch("services.storage.pdf_location.execute", m):
            await persist_pdf_location(
                None, "preoficial", schema_name="100_test", official_number="IF-2026-1"
            )
        assert "official_number = $1" in m.call_args.args[0]
        assert m.call_args.args[1] == "IF-2026-1"

    @pytest.mark.asyncio
    async def test_nunca_lanza(self):
        from services.storage.pdf_location import persist_pdf_location
        with patch("services.storage.pdf_location.execute", AsyncMock(side_effect=Exception("BD caída"))):
            await persist_pdf_location("doc-1", "preoficial", schema_name="100_test")


def _make_sign_job() -> dict:
    import uuid
    from datetime import datetime, timezone
    return {
        "session_id": str(uuid.uuid4()),
        "schema_name": "100_test",
        "document_id": str(uuid.uuid4()),
        "reservation_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "payload": {"official_number": "IF-2026-77"},
        "job_type": "sign",
        "created_at": datetime.now(timezone.utc),
    }


async def _run_in_threadpool(fn, *args, **kwargs):
    return fn(*args, **kwargs)


def _httpx_mock(pdf_bytes: bytes = b"pdf tosign"):
    m_resp = MagicMock()
    m_resp.content = pdf_bytes
    m_resp.raise_for_status = MagicMock()
    m_client = AsyncMock()
    m_client.get = AsyncMock(return_value=m_resp)
    m_cm = MagicMock()
    m_cm.__aenter__ = AsyncMock(return_value=m_client)
    m_cm.__aexit__ = AsyncMock(return_value=False)
    return m_cm


class TestSignJobConSelloApagado:

    async def _correr(self, m_slot, m_notary, m_r2, *, diferido: bool = False):
        from workers.escri import EscriWorker
        worker = EscriWorker()
        worker._mark_document_signed = AsyncMock()
        worker._mark_session_signed = AsyncMock()
        worker._publish_public_with_retry = AsyncMock()
        worker._requeue_sign_tsa_pending = AsyncMock()

        with (
            patch("services.storage.pdf_location.TSA_DEFERRED_SEAL_ENABLED", diferido),
            patch("workers.escri.get_tenant_r2_client", AsyncMock(return_value=m_r2)),
            patch("workers.escri.get_signer_data", AsyncMock(return_value={
                "full_name": "S", "seal": "Sello",
                "department_name": "D", "municipality_name": "M",
            })),
            patch("workers.escri.get_city_from_settings", AsyncMock(return_value="Ciudad")),
            patch("workers.escri.fetch_one", AsyncMock(return_value={
                "official_number": "IF-2026-77", "type": "HTML", "session_id": "x",
            })),
            patch("workers.escri.execute", AsyncMock()),
            patch("workers.escri.call_notary_sign_pdf", m_notary),
            patch("workers.escri.confirm_number", AsyncMock()),
            patch("workers.escri.finalize_number", AsyncMock()),
            patch("workers.escri.check_breaker_before_call", AsyncMock()),
            patch("workers.escri.run_in_threadpool", _run_in_threadpool),
            patch("httpx.AsyncClient", return_value=_httpx_mock()),
        ):
            await worker._process_job(_make_sign_job())
        return worker

    @pytest.mark.asyncio
    async def test_no_pide_cupo_ni_reencola(self):
        m_slot = MagicMock(return_value=False)
        m_notary = AsyncMock(return_value=b"%PDF firmado")
        m_r2 = MagicMock()
        m_r2.get_tosign_url = MagicMock(return_value="http://r2/x.pdf")
        m_r2.upload_oficial = MagicMock(return_value={"status": "success", "location": "preoficial"})

        worker = await self._correr(m_slot, m_notary, m_r2)

        m_slot.assert_not_called()
        worker._requeue_sign_tsa_pending.assert_not_awaited()
        m_notary.assert_awaited_once()
        assert m_notary.call_args.kwargs["defer_timestamp"] is True

    @pytest.mark.asyncio
    async def test_con_diferido_prendido_sube_a_preoficial_no_al_worm(self):
        m_r2 = MagicMock()
        m_r2.get_tosign_url = MagicMock(return_value="http://r2/x.pdf")
        m_r2.upload_oficial = MagicMock(return_value={"status": "success", "location": "preoficial"})

        await self._correr(MagicMock(return_value=False), AsyncMock(return_value=b"%PDF"), m_r2,
                           diferido=True)

        m_r2.upload_oficial.assert_called_once()
        assert m_r2.upload_oficial.call_args.args[2] == "preoficial"

    @pytest.mark.asyncio
    async def test_con_diferido_apagado_sube_directo_a_oficial(self):
        m_r2 = MagicMock()
        m_r2.get_tosign_url = MagicMock(return_value="http://r2/x.pdf")
        m_r2.upload_oficial = MagicMock(return_value={"status": "success", "location": "oficial"})

        await self._correr(MagicMock(return_value=False), AsyncMock(return_value=b"%PDF"), m_r2)

        m_r2.upload_oficial.assert_called_once()
        assert m_r2.upload_oficial.call_args.args[2] == "oficial"

    @pytest.mark.asyncio
    async def test_publica_al_publico_igual(self):
        m_r2 = MagicMock()
        m_r2.get_tosign_url = MagicMock(return_value="http://r2/x.pdf")
        m_r2.upload_oficial = MagicMock(return_value={"status": "success", "location": "preoficial"})

        worker = await self._correr(MagicMock(return_value=False), AsyncMock(return_value=b"%PDF"), m_r2)

        worker._publish_public_with_retry.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_alerta_b_b_definitivo(self):
        m_r2 = MagicMock()
        m_r2.get_tosign_url = MagicMock(return_value="http://r2/x.pdf")
        m_r2.upload_oficial = MagicMock(return_value={"status": "success", "location": "preoficial"})
        m_alert = AsyncMock()

        with patch("workers.escri.send_alert_mail", m_alert):
            await self._correr(MagicMock(return_value=False), AsyncMock(return_value=b"%PDF"), m_r2)

        m_alert.assert_not_awaited()


class TestRetrievalUsaPdfLocation:

    @pytest.mark.asyncio
    async def test_bytes_se_piden_al_bucket_de_la_columna(self):
        from services.documents.retrieval import official_url as mod

        m_r2 = MagicMock()
        m_r2.get_oficial_bytes = MagicMock(return_value=b"%PDF")
        with (
            patch.object(mod, "validate_document_id", AsyncMock(return_value=None)),
            patch.object(mod, "fetch_one", AsyncMock(return_value={
                "official_number": "IF-2026-77",
                "pdf_location": "preoficial",
                "status": "signed",
            })),
            patch("services.storage.cloudflare.get_tenant_r2_client", AsyncMock(return_value=m_r2)),
            patch.object(mod, "run_in_threadpool", _run_in_threadpool),
        ):
            res = await mod.get_official_document_bytes("doc-1", schema_name="100_test")

        assert res["pdf_bytes"] == b"%PDF"
        assert m_r2.get_oficial_bytes.call_args.args[1] == "preoficial"

    @pytest.mark.asyncio
    async def test_columna_ausente_cae_a_oficial(self):
        from services.documents.retrieval import official_url as mod

        m_r2 = MagicMock()
        m_r2.get_oficial_url = MagicMock(return_value="https://r2/firmada")
        with (
            patch.object(mod, "validate_document_id", AsyncMock(return_value=None)),
            patch.object(mod, "fetch_one", AsyncMock(return_value={
                "official_number": "IF-2026-77", "status": "signed",
            })),
            patch("services.storage.cloudflare.get_tenant_r2_client", AsyncMock(return_value=m_r2)),
            patch.object(mod, "run_in_threadpool", _run_in_threadpool),
        ):
            res = await mod.get_official_document_url("doc-1", schema_name="100_test")

        assert res["pdf_url"] == "https://r2/firmada"
        assert m_r2.get_oficial_url.call_args.args[1] == "oficial"


class TestDtsLegacyInactivo:

    @pytest.mark.asyncio
    async def test_un_job_dts_sobreviviente_cae_en_unknown_job_type(self):
        import uuid
        from unittest.mock import AsyncMock
        from workers.escri import EscriWorker

        worker = EscriWorker()
        worker._mark_session_failed = AsyncMock()
        worker._beat = lambda: None

        session_id = str(uuid.uuid4())
        job = {
            "session_id": session_id,
            "schema_name": "100_test",
            "document_id": str(uuid.uuid4()),
            "job_type": "dts",
            "payload": {},
        }

        await worker._process_batch([job])

        worker._mark_session_failed.assert_awaited_once()
        assert "unknown_job_type" in worker._mark_session_failed.call_args.args[1]

    def test_el_carril_no_dejo_restos_en_el_worker(self):
        from workers import escri

        for muerto in ('_process_dts_job', '_requeue_dts_pending',
                       '_requeue_dts_rate_limited', '_handle_dts_lock_terminal'):
            assert not hasattr(escri.EscriWorker, muerto), muerto


class TestUrlFirmadaUsaLaLocationEfectiva:

    def test_tad_pide_la_url_con_la_location_del_resultado(self):
        import inspect
        from workers.escri import EscriWorker

        fuente = inspect.getsource(EscriWorker._url_pdf_ciudadano)
        assert 'resultado.get("pdf_location")' in fuente
        assert "target_pdf_location()" not in fuente

    def test_la_firma_tad_devuelve_la_location_efectiva(self):
        import inspect
        from services.documents.signing import citizen_signing

        fuente = inspect.getsource(citizen_signing.sign_and_number_citizen_document)
        assert '"pdf_location": _effective_loc' in fuente

    def test_ningun_generador_de_url_recalcula_el_destino(self):
        import inspect
        from services.documents.signing import citizen_signing

        fuente = inspect.getsource(citizen_signing)
        for linea in fuente.splitlines():
            if "get_oficial_url" in linea:
                assert "target_pdf_location()" not in linea, linea


class TestFaltaDeProvisionamientoEsDiagnosticable:

    def test_tiene_failure_reason_propio(self):
        from workers.escri import _failure_code
        from shared.exceptions import PreOficialNotProvisionedError

        assert _failure_code(
            PreOficialNotProvisionedError("falta el bucket")
        ) == "preoficial_not_provisioned"

    def test_no_se_confunde_con_un_error_de_notary(self):
        from workers.escri import _failure_code
        from shared.exceptions import PreOficialNotProvisionedError, NotaryBusinessError

        assert _failure_code(PreOficialNotProvisionedError("x")) != _failure_code(
            NotaryBusinessError("y")
        )

    def test_es_un_error_de_r2_tipado(self):
        from shared.exceptions import PreOficialNotProvisionedError, R2Error

        assert issubclass(PreOficialNotProvisionedError, R2Error)


class TestRuidoDeLogsEnElFallback:

    def _cliente_con_fallback(self):
        from botocore.exceptions import ClientError
        c = _r2_client()
        err = ClientError({"Error": {"Code": "NoSuchKey", "Message": "nope"}}, "GetObject")
        c._client.get_object = MagicMock(side_effect=[
            err,
            {"Body": MagicMock(read=MagicMock(return_value=b"pdf"))},
        ])
        return c

    def test_sin_location_explicita_no_alerta(self, caplog):
        import logging
        c = self._cliente_con_fallback()
        with caplog.at_level(logging.WARNING):
            assert c.get_oficial_bytes("IF-2026-1") == b"pdf"
        assert not [r for r in caplog.records if "desincronizada" in r.getMessage()]

    def test_con_location_explicita_si_alerta(self, caplog):
        import logging
        c = self._cliente_con_fallback()
        with caplog.at_level(logging.WARNING):
            assert c.get_oficial_bytes("IF-2026-1", "oficial") == b"pdf"
        assert [r for r in caplog.records if "desincronizada" in r.getMessage()]
