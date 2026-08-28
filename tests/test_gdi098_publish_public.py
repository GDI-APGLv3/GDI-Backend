
import pytest
from unittest.mock import patch, AsyncMock

from services.storage.publish_public import (
    maybe_publish_official_pdf,
    _visibility_cache,
)
from services.r2_client import R2KeyNotFound

SCHEMA = "100_test"
DOC_TYPE_ID = 53
DOC_ID = "dddddddd-0000-0000-0000-000000000001"
OFFICIAL_NUMBER = "IF-2026-00000001-MT-DGOBR"


def _fetch_publico():
    return AsyncMock(side_effect=[
        {"document_type_id": DOC_TYPE_ID},
        {"visibility": "publico"},
    ])


@pytest.fixture(autouse=True)
def _clear_cache():
    _visibility_cache.clear()
    yield
    _visibility_cache.clear()


class TestTipoPublicoCopia:

    @pytest.mark.asyncio
    async def test_tipo_publico_dispara_copia(self):
        with patch("database.fetch_one", new=_fetch_publico()), \
             patch("services.storage.publish_public.r2_copy", new=AsyncMock()) as mock_copy:
            await maybe_publish_official_pdf(
                schema_name=SCHEMA,
                official_number=OFFICIAL_NUMBER,
                document_id=DOC_ID,
            )

        mock_copy.assert_awaited_once()
        _, kwargs = mock_copy.call_args
        assert kwargs["schema_name"] == SCHEMA
        assert kwargs["src"] == f"{OFFICIAL_NUMBER}.pdf"
        assert kwargs["dst"] == f"{DOC_ID}.pdf"
        assert kwargs["src_bucket"] == "oficial"
        assert kwargs["dst_bucket"] == "publico"

    @pytest.mark.asyncio
    async def test_gdi270_pdf_location_preoficial_copia_desde_preoficial(self):
        fetch_mock = AsyncMock(side_effect=[
            {"document_type_id": DOC_TYPE_ID, "pdf_location": "preoficial"},
            {"visibility": "publico"},
        ])
        with patch("database.fetch_one", new=fetch_mock), \
             patch("services.storage.publish_public.r2_copy", new=AsyncMock()) as mock_copy:
            await maybe_publish_official_pdf(
                schema_name=SCHEMA,
                official_number=OFFICIAL_NUMBER,
                document_id=DOC_ID,
            )

        mock_copy.assert_awaited_once()
        _, kwargs = mock_copy.call_args
        assert kwargs["src_bucket"] == "preoficial"
        assert kwargs["dst_bucket"] == "publico"

    @pytest.mark.asyncio
    async def test_pdf_location_null_default_oficial(self):
        with patch("database.fetch_one", new=_fetch_publico()), \
             patch("services.storage.publish_public.r2_copy", new=AsyncMock()) as mock_copy:
            await maybe_publish_official_pdf(
                schema_name=SCHEMA,
                official_number=OFFICIAL_NUMBER,
                document_id=DOC_ID,
            )

        mock_copy.assert_awaited_once()
        _, kwargs = mock_copy.call_args
        assert kwargs["src_bucket"] == "oficial"

    @pytest.mark.asyncio
    async def test_cache_evita_segunda_query_de_visibilidad(self):
        fetch_mock = AsyncMock(side_effect=[
            {"document_type_id": DOC_TYPE_ID},
            {"visibility": "publico"},
            {"document_type_id": DOC_TYPE_ID},
        ])
        with patch("database.fetch_one", new=fetch_mock), \
             patch("services.storage.publish_public.r2_copy", new=AsyncMock()):
            await maybe_publish_official_pdf(
                schema_name=SCHEMA, official_number=OFFICIAL_NUMBER,
                document_id=DOC_ID,
            )
            await maybe_publish_official_pdf(
                schema_name=SCHEMA, official_number="OTRO-NUM",
                document_id="dddddddd-0000-0000-0000-000000000002",
            )

        assert fetch_mock.await_count == 3


class TestTipoNoPublicoNoCopia:

    @pytest.mark.asyncio
    async def test_tipo_interno_no_copia(self):
        fetch_mock = AsyncMock(side_effect=[
            {"document_type_id": DOC_TYPE_ID},
            {"visibility": "interno"},
        ])
        with patch("database.fetch_one", new=fetch_mock), \
             patch("services.storage.publish_public.r2_copy", new=AsyncMock()) as mock_copy:
            await maybe_publish_official_pdf(
                schema_name=SCHEMA, official_number=OFFICIAL_NUMBER,
                document_id=DOC_ID,
            )

        mock_copy.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tipo_reservado_no_copia(self):
        fetch_mock = AsyncMock(side_effect=[
            {"document_type_id": DOC_TYPE_ID},
            {"visibility": "reservado"},
        ])
        with patch("database.fetch_one", new=fetch_mock), \
             patch("services.storage.publish_public.r2_copy", new=AsyncMock()) as mock_copy:
            await maybe_publish_official_pdf(
                schema_name=SCHEMA, official_number=OFFICIAL_NUMBER,
                document_id=DOC_ID,
            )

        mock_copy.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tipo_sin_fila_no_copia(self):
        with patch("database.fetch_one", new=AsyncMock(return_value=None)), \
             patch("services.storage.publish_public.r2_copy", new=AsyncMock()) as mock_copy:
            await maybe_publish_official_pdf(
                schema_name=SCHEMA, official_number=OFFICIAL_NUMBER,
                document_id=DOC_ID,
            )

        mock_copy.assert_not_awaited()


class TestBucketPublicoAusente:

    @pytest.mark.asyncio
    async def test_bucket_publico_none_no_rompe(self):
        with patch("database.fetch_one", new=_fetch_publico()), \
             patch(
                 "services.storage.publish_public.r2_copy",
                 new=AsyncMock(side_effect=ValueError("Tenant '100_test' no tiene bucket_publico configurado")),
             ):
            await maybe_publish_official_pdf(
                schema_name=SCHEMA, official_number=OFFICIAL_NUMBER,
                document_id=DOC_ID,
            )


class TestRetryYSoftFail:

    @pytest.mark.asyncio
    async def test_fallo_persistente_nunca_propaga(self):
        with patch("database.fetch_one", new=_fetch_publico()), \
             patch("services.storage.publish_public.r2_copy",
                   new=AsyncMock(side_effect=RuntimeError("R2 caído"))) as mock_copy, \
             patch("services.storage.publish_public.asyncio.sleep", new=AsyncMock()):
            await maybe_publish_official_pdf(
                schema_name=SCHEMA, official_number=OFFICIAL_NUMBER,
                document_id=DOC_ID,
            )

        assert mock_copy.await_count == 3

    @pytest.mark.asyncio
    async def test_key_not_found_hace_fallback_a_put(self):
        with patch("database.fetch_one", new=_fetch_publico()), \
             patch("services.storage.publish_public.r2_copy",
                   new=AsyncMock(side_effect=R2KeyNotFound(f"{OFFICIAL_NUMBER}.pdf"))), \
             patch("services.storage.publish_public.r2_put", new=AsyncMock()) as mock_put:
            await maybe_publish_official_pdf(
                schema_name=SCHEMA, official_number=OFFICIAL_NUMBER,
                document_id=DOC_ID,
                signed_pdf_bytes=b"%PDF-1.4 ...",
            )

        mock_put.assert_awaited_once()
        _, kwargs = mock_put.call_args
        assert kwargs["bucket"] == "publico"
        assert kwargs["key"] == f"{DOC_ID}.pdf"


class TestResolucionPorDocumentId:

    @pytest.mark.asyncio
    async def test_resuelve_type_id_desde_document_id(self):
        with patch("database.fetch_one", new=_fetch_publico()) as fetch_mock, \
             patch("services.storage.publish_public.r2_copy", new=AsyncMock()) as mock_copy:
            await maybe_publish_official_pdf(
                schema_name=SCHEMA, official_number=OFFICIAL_NUMBER,
                document_id=DOC_ID,
            )

        assert fetch_mock.await_count == 2
        mock_copy.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fallback_a_document_type_id_sin_draft(self):
        fetch_mock = AsyncMock(side_effect=[
            None,
            {"visibility": "publico"},
        ])
        with patch("database.fetch_one", new=fetch_mock), \
             patch("services.storage.publish_public.r2_copy", new=AsyncMock()) as mock_copy:
            await maybe_publish_official_pdf(
                schema_name=SCHEMA, official_number=OFFICIAL_NUMBER,
                document_id=DOC_ID,
                document_type_id=DOC_TYPE_ID,
            )

        mock_copy.assert_awaited_once()
        _, kwargs = mock_copy.call_args
        assert kwargs["dst"] == f"{DOC_ID}.pdf"

    @pytest.mark.asyncio
    async def test_sin_document_id_no_rompe(self):
        with patch("database.fetch_one", new=AsyncMock()) as fetch_mock, \
             patch("services.storage.publish_public.r2_copy", new=AsyncMock()) as mock_copy:
            await maybe_publish_official_pdf(
                schema_name=SCHEMA, official_number=OFFICIAL_NUMBER,
            )

        fetch_mock.assert_not_awaited()
        mock_copy.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_draft_gana_sobre_document_type_id_contradictorio(self):
        OTRO_TYPE_ID = 99
        fetch_mock = AsyncMock(side_effect=[
            {"document_type_id": DOC_TYPE_ID},
            {"visibility": "interno"},
        ])
        with patch("database.fetch_one", new=fetch_mock), \
             patch("services.storage.publish_public.r2_copy", new=AsyncMock()) as mock_copy:
            await maybe_publish_official_pdf(
                schema_name=SCHEMA, official_number=OFFICIAL_NUMBER,
                document_id=DOC_ID,
                document_type_id=OTRO_TYPE_ID,
            )

        called_params = fetch_mock.call_args_list[1].args
        assert OTRO_TYPE_ID not in called_params
        assert DOC_TYPE_ID in called_params
        mock_copy.assert_not_awaited()
