import pytest
from pydantic import ValidationError

from models.users.user_documents import SignerInfo, UserDocumentInfo
from services.documents.core.queries import (
    search_official_document_by_number_query,
    get_document_info_for_rejection_query,
    get_rejected_documents_for_user_query,
)


class TestSignerInfoAcceptsCitizenSigner:

    def test_firmante_usuario_gdi_ok(self):
        signer = SignerInfo(
            user_id="11111111-1111-1111-1111-111111111111",
            citizen_id=None,
            full_name="Juan Perez",
            profile_picture_url=None,
            signed=True,
            is_numerator=False,
        )
        assert signer.user_id is not None
        assert signer.citizen_id is None

    def test_firmante_ciudadano_tad_ok(self):
        signer = SignerInfo(
            user_id=None,
            citizen_id="22222222-2222-2222-2222-222222222222",
            full_name="Maria Vecina",
            profile_picture_url=None,
            signed=True,
            is_numerator=True,
        )
        assert signer.user_id is None
        assert signer.citizen_id == "22222222-2222-2222-2222-222222222222"

    def test_ambos_none_no_levanta_a_nivel_pydantic(self):
        signer = SignerInfo(full_name="Sin id", signed=False, is_numerator=False)
        assert signer.user_id is None
        assert signer.citizen_id is None


class TestUserDocumentInfoAcceptsCitizenLastEditor:

    def _base_kwargs(self, **overrides):
        base = {
            "id": "33333333-3333-3333-3333-333333333333",
            "reference": "PROV-2026-00003034-MDEV-TAD",
            "display_status": "Firmado",
            "document_type": {"name": "Documento", "acronym": "PROV"},
            "user_role": "viewer",
            "last_editor_name": "Maria Vecina",
            "last_editor_citizen_id": "22222222-2222-2222-2222-222222222222",
            "last_editor_citizen_country_id": "20111111112",
            "official_number": "PROV-2026-00003034-MDEV-TAD",
        }
        base.update(overrides)
        return base

    def test_last_editor_citizen_no_levanta(self):
        doc = UserDocumentInfo(**self._base_kwargs())
        assert doc.last_editor_name == "Maria Vecina"
        assert doc.last_editor_citizen_id == "22222222-2222-2222-2222-222222222222"

    def test_last_editor_usuario_gdi_sin_citizen_fields_ok(self):
        doc = UserDocumentInfo(**self._base_kwargs(
            last_editor_name="Juan Perez",
            last_editor_citizen_id=None,
            last_editor_citizen_country_id=None,
        ))
        assert doc.last_editor_citizen_id is None


class TestQueriesFallbackACitizens:

    def test_search_official_document_by_number_incluye_citizens(self):
        query = search_official_document_by_number_query()
        assert "LEFT JOIN citizens creator_citizen" in query
        assert "LEFT JOIN citizens numerator_citizen" in query
        assert "COALESCE(creator.full_name, creator_citizen.full_name)" in query
        assert "COALESCE(numerator.full_name, numerator_citizen.full_name)" in query
        assert "JOIN users creator" not in query.replace("LEFT JOIN users creator", "")

    def test_get_document_info_for_rejection_incluye_citizens(self):
        query = get_document_info_for_rejection_query()
        assert "LEFT JOIN citizens creator_citizen" in query
        assert "LEFT JOIN users creator" in query
        assert "COALESCE(creator.full_name, creator_citizen.full_name)" in query

    def test_get_rejected_documents_for_user_incluye_citizens(self):
        query = get_rejected_documents_for_user_query()
        assert "LEFT JOIN citizens creator_citizen" in query
        assert "LEFT JOIN users creator" in query
        assert "COALESCE(creator.full_name, creator_citizen.full_name)" in query
