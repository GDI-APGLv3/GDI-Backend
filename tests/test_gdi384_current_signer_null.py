
import pytest

from services.documents.signing.details_builder import (
    _build_final_response,
    _can_user_sign,
    _find_current_signer,
)

FIRMANTE = "a1000000-0000-0000-0000-00000000000a"
MIRON = "da2ed827-146b-4d14-8dbe-1681cb568450"


def _grouped(*, pendientes=None, completados=None):
    pendientes = pendientes or []
    completados = completados or []
    return {
        "pending": pendientes,
        "completed": completados,
        "pending_count": len(pendientes),
        "completed_count": len(completados),
    }


def _firmante_completo(user_id=FIRMANTE):
    return {
        "user_id": user_id,
        "full_name": "Miguel Herrera",
        "email": "mherrera@munitest.com",
        "profile_picture_url": None,
        "signing_order": 1,
        "is_numerator": True,
        "has_signed": True,
        "seal_name": "Secretario",
    }


class TestFindCurrentSigner:

    def test_no_firmante_devuelve_vacio(self):
        grouped = _grouped(completados=[_firmante_completo()])
        assert _find_current_signer(grouped, MIRON) == {}

    def test_firmante_se_encuentra(self):
        grouped = _grouped(completados=[_firmante_completo()])
        assert _find_current_signer(grouped, FIRMANTE)["user_id"] == FIRMANTE

    def test_sin_firmante_no_puede_firmar(self):
        grouped = _grouped(completados=[_firmante_completo()])
        assert _can_user_sign({}, grouped) is False


@pytest.mark.asyncio
class TestCurrentSignerEnLaRespuesta:

    async def _armar(self, user_id, grouped, monkeypatch):
        import services.documents.signing.details_builder as mod

        async def _sin_embeddings(*a, **k):
            return False

        monkeypatch.setattr(mod, "_check_has_embeddings", _sin_embeddings)

        return await _build_final_response(
            document_info={
                "document_id": "aee4d8ec-ea70-4940-95e1-2ce598becd6e",
                "created_by": FIRMANTE,
                "sent_by": FIRMANTE,
                "status": "signed",
                "reference": "TEST-AUTO",
                "document_type_name": "Informe",
                "document_type_acronym": "IF",
                "document_type_visibility": "privado",
                "last_modified_at": None,
                "signature_policy": "electronic",
                "official_number": "IF-2026-00002475-TXST-INNO",
            },
            creator_info=None,
            signatures_grouped=grouped,
            user_id=user_id,
            document_id="aee4d8ec-ea70-4940-95e1-2ce598becd6e",
            has_official_number=True,
            schema_name="100_test",
        )

    async def test_el_que_NO_firma_recibe_null(self, monkeypatch):
        grouped = _grouped(completados=[_firmante_completo()])
        resp = await self._armar(MIRON, grouped, monkeypatch)

        assert resp["current_signer"] is None

    async def test_no_devuelve_al_que_consulta_disfrazado_de_firmante(self, monkeypatch):
        grouped = _grouped(completados=[_firmante_completo()])
        resp = await self._armar(MIRON, grouped, monkeypatch)

        assert resp["current_signer"] != {"user_id": MIRON}
        if resp["current_signer"] is not None:
            assert resp["current_signer"].get("user_id") != MIRON

    async def test_el_resto_de_la_respuesta_no_cambia(self, monkeypatch):
        grouped = _grouped(completados=[_firmante_completo()])
        resp = await self._armar(MIRON, grouped, monkeypatch)

        assert resp["can_sign"] is False
        assert resp["signature_progress"]["completed"] == 1
        assert resp["signature_progress"]["total"] == 1
        assert resp["is_sector_viewer"] is True

    async def test_el_firmante_SIGUE_recibiendo_su_bloque(self, monkeypatch):
        grouped = _grouped(completados=[_firmante_completo()])
        resp = await self._armar(FIRMANTE, grouped, monkeypatch)

        cs = resp["current_signer"]
        assert cs is not None
        assert cs["user_id"] == FIRMANTE
        assert cs["user_name"] == "Miguel Herrera"
        assert cs["is_numerator"] is True
        assert cs["already_signed"] is True

    async def test_firmante_pendiente_tambien_recibe_su_bloque(self, monkeypatch):
        pendiente = _firmante_completo()
        pendiente["has_signed"] = False
        grouped = _grouped(pendientes=[pendiente])
        resp = await self._armar(FIRMANTE, grouped, monkeypatch)

        assert resp["current_signer"] is not None
        assert resp["current_signer"]["already_signed"] is False


class TestModeloAceptaNull:

    def test_response_model_valida_con_none(self):
        from models.documents.signing import DocumentSignatureDetailsResponse

        campo = DocumentSignatureDetailsResponse.model_fields["current_signer"]
        assert not campo.is_required(), "current_signer debe poder ser null (GDI-384)"
