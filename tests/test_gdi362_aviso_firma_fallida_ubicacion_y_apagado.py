
import pytest

from services.home.queries import get_failed_signature_notifications_query
from services.home.service import _DISMISS_KEY_PREFIXES, _build_failed_signature_item
from services.documents.signing.failure_reasons import _MOTIVOS, motivo_humano


def _row(**over):
    row = {
        "session_id": "s-1",
        "document_id": "d-1",
        "failure_reason": "stale_reservation",
        "updated_at": "2026-08-20T10:00:00Z",
        "document_reference": "Nota 15",
    }
    row.update(over)
    return row


class TestElLinkNoCaeEn404:
    def test_va_a_la_bandeja_de_firma_y_no_a_documentos(self):
        href = _build_failed_signature_item(_row())["href"]

        assert href.startswith("/documentos-firma?documentId=")
        assert not href.startswith("/documentos/")

    def test_usa_el_helper_compartido_y_no_arma_el_href_a_mano(self):
        from schemas.home_schemas import build_sign_href

        assert _build_failed_signature_item(_row())["href"] == build_sign_href("d-1")


class TestSeApagaSolo:
    def test_deja_de_traerlo_cuando_el_documento_ya_no_espera_mi_firma(self):
        sql = get_failed_signature_notifications_query()

        assert "document_signers" in sql
        assert "ds.status = 'pending'" in sql
        assert "d.status = 'sent_to_sign'" in sql

    def test_la_ventana_de_2_horas_corre_desde_el_visto(self):
        sql = get_failed_signature_notifications_query()

        assert "'seen:signature_failed:'" in sql
        assert "INTERVAL '2 hours'" in sql
        assert "nd.dismissed_at <= NOW() - INTERVAL '2 hours'" in sql

    def test_los_7_dias_siguen_como_red_para_el_que_no_entra_nunca(self):
        assert "INTERVAL '7 days'" in get_failed_signature_notifications_query()

    def test_un_documento_que_fallo_tres_veces_no_muestra_tres_avisos(self):
        sql = get_failed_signature_notifications_query()

        assert "DISTINCT ON (ss.document_id)" in sql
        assert sql.rstrip().endswith("ORDER BY updated_at DESC")


class TestElRechazadoNoGeneraAviso:

    @pytest.mark.parametrize(
        "motivo", ["document_no_longer_signable", "confirmed_and_rejected_conflict"]
    )
    def test_esta_excluido_de_la_campanita(self, motivo):
        assert f"'{motivo}'" in get_failed_signature_notifications_query()

    @pytest.mark.parametrize(
        "motivo", ["document_no_longer_signable", "confirmed_and_rejected_conflict"]
    )
    def test_pero_el_mail_lo_sigue_contando(self, motivo):
        from services.documents.signing.failure_reasons import NO_AVISAR_AL_USUARIO

        assert motivo not in NO_AVISAR_AL_USUARIO
        assert motivo in _MOTIVOS


class TestElVistoPasaLosTresGuards:

    KEY = "seen:signature_failed:550e8400-e29b-41d4-a716-446655440000"

    def test_guard_1_el_schema(self):
        from schemas.home_schemas import DismissRequest

        DismissRequest(key=self.KEY)

    def test_guard_2_el_del_service(self):
        assert self.KEY.startswith(_DISMISS_KEY_PREFIXES)

    def test_la_key_del_visto_no_se_pisa_con_la_del_dismiss(self):
        item = _build_failed_signature_item(_row())

        assert item["key"] == "signature_failed:s-1"
        assert f"seen:{item['key']}" != item["key"]
        assert len(f"seen:signature_failed:{'0' * 36}") <= 160


class TestElCopyNoMandaABuscarloAMano:

    def test_ningun_motivo_promete_ir_a_buscarlo_a_documentos(self):
        culpables = [
            reason
            for reason, (_, que_hacer) in _MOTIVOS.items()
            if "quedó en tus Documentos" in que_hacer
        ]
        assert not culpables, f"copy viejo en: {culpables}"

    def test_el_generico_tambien(self):
        assert "quedó en tus Documentos" not in motivo_humano(None)[1]
        assert "quedó en tus Documentos" not in motivo_humano("motivo_inventado")[1]

    def test_el_rechazado_conserva_su_texto_porque_ese_NO_vuelve(self):
        assert "en tus Documentos" in motivo_humano("document_no_longer_signable")[1]
