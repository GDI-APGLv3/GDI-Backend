import pytest
from unittest.mock import AsyncMock, patch

from shared.exceptions import ExternalServiceError


def _row(file_id, r2_key, file_name):
    return {"id": file_id, "r2_key": r2_key, "file_name": file_name}


@pytest.mark.asyncio
async def test_fetch_embedded_files_for_signing_sin_adjuntos_devuelve_lista_vacia():
    from services.documents.lifecycle.embedded_files import fetch_embedded_files_for_signing

    with patch("services.documents.lifecycle.embedded_files.fetch_all", new=AsyncMock(return_value=[])):
        result = await fetch_embedded_files_for_signing("doc-1", schema_name="100_test")

    assert result == []


@pytest.mark.asyncio
async def test_fetch_embedded_files_for_signing_ok_en_primer_intento():
    from services.documents.lifecycle.embedded_files import fetch_embedded_files_for_signing

    rows = [_row("f1", "editing/doc-1/f1/a.pdf", "a.pdf"), _row("f2", "editing/doc-1/f2/b.docx", "b.docx")]

    async def _fake_r2_get_object(*, schema_name, key, bucket="tosign"):
        return b"contenido-" + key.encode()

    with patch("services.documents.lifecycle.embedded_files.fetch_all", new=AsyncMock(return_value=rows)), \
         patch("services.documents.lifecycle.embedded_files.r2_get_object", new=_fake_r2_get_object):
        result = await fetch_embedded_files_for_signing("doc-1", schema_name="100_test")

    assert len(result) == 2
    assert result[0][0] == "a.pdf"
    assert result[1][0] == "b.docx"


@pytest.mark.asyncio
async def test_fetch_embedded_files_for_signing_reintenta_y_luego_ok():
    from services.documents.lifecycle.embedded_files import fetch_embedded_files_for_signing

    rows = [_row("f1", "editing/doc-1/f1/a.pdf", "a.pdf")]
    call_count = {"n": 0}

    async def _flaky_r2_get_object(*, schema_name, key, bucket="tosign"):
        call_count["n"] += 1
        if call_count["n"] < 2:
            raise RuntimeError("R2 momentaneamente no disponible")
        return b"contenido-ok"

    with patch("services.documents.lifecycle.embedded_files.fetch_all", new=AsyncMock(return_value=rows)), \
         patch("services.documents.lifecycle.embedded_files.r2_get_object", new=_flaky_r2_get_object), \
         patch("asyncio.sleep", new=AsyncMock()):
        result = await fetch_embedded_files_for_signing("doc-1", schema_name="100_test")

    assert call_count["n"] == 2
    assert result == [("a.pdf", b"contenido-ok")]


@pytest.mark.asyncio
async def test_fetch_embedded_files_for_signing_falla_3_intentos_lanza_external_service_error():
    from services.documents.lifecycle.embedded_files import fetch_embedded_files_for_signing

    rows = [_row("f1", "editing/doc-1/f1/a.pdf", "a.pdf")]

    async def _always_fails(*, schema_name, key, bucket="tosign"):
        raise RuntimeError("R2 caído")

    with patch("services.documents.lifecycle.embedded_files.fetch_all", new=AsyncMock(return_value=rows)), \
         patch("services.documents.lifecycle.embedded_files.r2_get_object", new=_always_fails), \
         patch("asyncio.sleep", new=AsyncMock()):
        with pytest.raises(ExternalServiceError):
            await fetch_embedded_files_for_signing("doc-1", schema_name="100_test")
