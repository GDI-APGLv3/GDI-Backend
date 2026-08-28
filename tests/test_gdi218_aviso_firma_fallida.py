import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from services.documents.signing.failure_reasons import (
    NO_AVISAR_AL_USUARIO, motivo_humano,
)


class TestMotivosHumanizados:

    def test_traduce_los_codigos_que_traduce_el_front(self):
        for code in ("stale_reservation", "cas_confirm_failure", "numerator_partial_failure"):
            qué_pasó, qué_hacer = motivo_humano(code)
            assert code not in qué_pasó, "el código crudo no se le muestra al usuario"
            assert qué_hacer, "todo aviso cierra con la acción"

    def test_todo_motivo_cierra_diciendo_que_hacer(self):
        for code in ("stale_reservation", "notary_business_error", "pdf_integrity_failed",
                     "r2_object_locked", "document_no_longer_signable", None, "código_inventado"):
            _, qué_hacer = motivo_humano(code)
            assert len(qué_hacer) > 20

    def test_un_codigo_desconocido_no_se_muestra_crudo(self):
        qué_pasó, _ = motivo_humano("algun_codigo_nuevo_del_worker")
        assert "algun_codigo_nuevo" not in qué_pasó

    def test_los_motivos_internos_no_le_importan_al_usuario(self):
        assert "superseded" in NO_AVISAR_AL_USUARIO
        assert "document_already_signing" in NO_AVISAR_AL_USUARIO


class TestDisparoDelAviso:

    def _worker(self):
        from workers.escri import EscriWorker
        return EscriWorker()

    @pytest.mark.asyncio
    async def test_ca1_falla_definitiva_dispara_el_aviso(self):
        worker = self._worker()
        aviso = AsyncMock()
        fila = {"schema_name": "100_test", "document_id": "doc-1", "user_id": "user-1"}

        with (
            patch("workers.escri.fetch_one", AsyncMock(return_value=fila)),
            patch.object(worker, "_avisar_firma_fallida", aviso),
        ):
            await worker._mark_session_failed("sess-1", "stale_reservation")

        aviso.assert_awaited_once()
        assert aviso.call_args.kwargs["reason"] == "stale_reservation"

    @pytest.mark.asyncio
    async def test_ca4_si_otro_worker_ya_la_resolvio_no_avisa(self):
        worker = self._worker()
        aviso = AsyncMock()

        with (
            patch("workers.escri.fetch_one", AsyncMock(return_value=None)),
            patch.object(worker, "_avisar_firma_fallida", aviso),
        ):
            await worker._mark_session_failed("sess-1", "stale_reservation")

        aviso.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ca2_un_motivo_interno_no_genera_aviso(self):
        worker = self._worker()

        with patch("shared.email.send_email", autospec=True) as mail:
            await worker._avisar_firma_fallida(
                session_id="s", schema_name="100_test", document_id="d",
                user_id="u", reason="superseded",
            )

        mail.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_manda_el_mail_con_el_motivo_y_el_paso_siguiente(self):
        worker = self._worker()
        usuario = {"email": "juan@muni.gob.ar", "full_name": "Juan"}

        with (
            patch("database.fetch_one", AsyncMock(return_value=usuario)),
            patch("shared.email.send_email", autospec=True) as mail,
        ):
            await worker._avisar_firma_fallida(
                session_id="s", schema_name="100_test", document_id="doc-42",
                user_id="u", reason="stale_reservation",
            )

        mail.assert_awaited_once()
        args, kwargs = mail.call_args
        assert args[0] == "juan@muni.gob.ar"
        qué_pasó, qué_hacer = motivo_humano("stale_reservation")
        assert qué_pasó in kwargs["text"]
        assert qué_hacer in kwargs["text"]

    @pytest.mark.asyncio
    async def test_si_el_mail_falla_no_rompe_el_manejo_del_fallo(self):
        worker = self._worker()

        with (
            patch("database.fetch_one", AsyncMock(side_effect=RuntimeError("BD caída"))),
        ):
            await worker._avisar_firma_fallida(
                session_id="s", schema_name="100_test", document_id="d",
                user_id="u", reason="stale_reservation",
            )

    @pytest.mark.asyncio
    async def test_sin_email_del_usuario_no_explota(self):
        worker = self._worker()

        with (
            patch("database.fetch_one", AsyncMock(return_value={"email": None, "full_name": "X"})),
            patch("shared.email.send_email", autospec=True) as mail,
        ):
            await worker._avisar_firma_fallida(
                session_id="s", schema_name="100_test", document_id="d",
                user_id="u", reason="stale_reservation",
            )

        mail.assert_not_awaited()


class TestCampanita:

    def test_la_query_filtra_los_motivos_internos(self):
        from services.home.queries import get_failed_signature_notifications_query

        sql = get_failed_signature_notifications_query()
        for interno in NO_AVISAR_AL_USUARIO:
            assert interno in sql, f"{interno} tiene que estar excluido también en la campanita"

    def test_la_query_respeta_el_dismiss_y_el_municipio(self):
        from services.home.queries import get_failed_signature_notifications_query

        sql = get_failed_signature_notifications_query()
        assert "notification_dismissals" in sql
        assert "'signature_failed:'" in sql
        assert "ss.schema_name = $2" in sql

    def test_el_item_trae_el_texto_ya_humanizado(self):
        from services.home.service import _build_failed_signature_item

        row = {
            "session_id": "s-1", "document_id": "d-1",
            "failure_reason": "stale_reservation",
            "updated_at": "2026-08-20T10:00:00Z",
            "document_reference": "Nota 15",
        }
        item = _build_failed_signature_item(row)
        assert item["key"] == "signature_failed:s-1"
        assert item["message"] == motivo_humano("stale_reservation")[0]
        assert item["next_step"] == motivo_humano("stale_reservation")[1]
        assert "documentos-firma" in item["href"]
        assert item["href"].endswith("d-1")

    def test_la_clave_de_dismiss_esta_permitida(self):
        from schemas.home_schemas import DismissRequest

        DismissRequest(key="signature_failed:550e8400-e29b-41d4-a716-446655440000")

    def test_el_guard_del_service_tambien_la_acepta(self):
        from services.home.service import _DISMISS_KEY_PREFIXES

        assert "signature_failed:".startswith(_DISMISS_KEY_PREFIXES) or             "signature_failed:x".startswith(_DISMISS_KEY_PREFIXES), (
                "el guard de dismiss_notification rechaza la clave que la "
                "propia campanita genera"
            )


class TestLaLlamadaRespetaLaFirmaReal:

    def test_los_parametros_existen_en_send_email(self):
        import inspect
        from shared.email import send_email
        from workers.escri import EscriWorker

        firma = inspect.signature(send_email)
        from services.documents.signing import failure_notice
        fuente = inspect.getsource(failure_notice.avisar_firma_fallida)

        assert "to_email=" not in fuente
        assert "body=" not in fuente
        assert "text" in firma.parameters
        assert "subject" in firma.parameters

    @pytest.mark.asyncio
    async def test_la_llamada_no_explota_contra_la_firma_real(self):
        from workers.escri import EscriWorker

        worker = EscriWorker()
        usuario = {"email": "juan@muni.gob.ar", "full_name": "Juan"}
        exploto = []

        async def _spy(*args, **kwargs):
            return True

        with (
            patch("database.fetch_one", AsyncMock(return_value=usuario)),
            patch("shared.email.send_email", autospec=True, side_effect=_spy) as m,
        ):
            await worker._avisar_firma_fallida(
                session_id="s", schema_name="100_test", document_id="d",
                user_id="u", reason="stale_reservation",
            )

        m.assert_awaited_once()


class TestElCasoQueMotivoLaCard:

    @pytest.mark.asyncio
    async def test_el_sweeper_avisa_cuando_la_sesion_vence_en_la_cola(self):
        from workers import sweeper_escri

        aviso = AsyncMock()
        fila = {
            "session_id": "5e551000-0000-0000-0000-00000000000a",
            "document_id": "d0c00000-0000-0000-0000-00000000000a",
            "user_id": "0e551000-0000-0000-0000-00000000000a",
            "failure_reason": "pending_expired_worker_offline",
        }

        with patch("services.documents.signing.failure_notice.avisar_firma_fallida", aviso):
            await sweeper_escri._avisar_expirada("100_test", fila)

        aviso.assert_awaited_once()
        assert aviso.call_args.kwargs["reason"] == "pending_expired_worker_offline"

    @pytest.mark.asyncio
    async def test_si_el_aviso_falla_el_barrido_sigue(self):
        from workers import sweeper_escri

        with patch("services.documents.signing.failure_notice.avisar_firma_fallida",
                   AsyncMock(side_effect=RuntimeError("smtp caído"))):
            await sweeper_escri._avisar_expirada("100_test", {
                "session_id": "s", "document_id": "d", "user_id": "u",
                "failure_reason": "pending_expired_worker_offline",
            })

    def test_ese_motivo_tiene_texto_propio_y_no_cae_al_generico(self):
        qué_pasó, qué_hacer = motivo_humano("pending_expired_worker_offline")
        generico, _ = motivo_humano("codigo_que_no_existe")
        assert qué_pasó != generico
        assert "venció" in qué_pasó or "demorado" in qué_pasó

    def test_los_dos_updates_del_sweeper_traen_el_firmante(self):
        import inspect
        from workers import sweeper_escri

        for fn in (sweeper_escri._handle_pending_expired,
                   sweeper_escri._handle_common_pending_expired):
            src = inspect.getsource(fn)
            assert "status        = 'expired'" in src or "status         = 'expired'" in src
            assert "user_id::text" in src, f"{fn.__name__} no devuelve el firmante"
            assert "_avisar_expirada" in src, f"{fn.__name__} no avisa"

    def test_la_campanita_incluye_los_dos_estados_terminales(self):
        from services.home.queries import get_failed_signature_notifications_query

        sql = get_failed_signature_notifications_query()
        assert "IN ('failed', 'expired')" in sql
