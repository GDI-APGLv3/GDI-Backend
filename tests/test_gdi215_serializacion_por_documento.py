
import asyncio
import inspect

import pytest

from workers.escri import EscriWorker


def _job(session_id: str, document_id: str, job_type: str = "sign") -> dict:
    return {"session_id": session_id, "document_id": document_id, "job_type": job_type}


class TestSerializacionPorDocumento:
    @pytest.mark.asyncio
    async def test_dos_jobs_del_mismo_documento_no_corren_a_la_vez(self):
        worker = EscriWorker()
        en_vuelo = 0
        solapamiento = False

        async def fake_process(job):
            nonlocal en_vuelo, solapamiento
            en_vuelo += 1
            if en_vuelo > 1:
                solapamiento = True
            await asyncio.sleep(0.01)
            en_vuelo -= 1

        worker._process_job = fake_process
        worker._process_common_job = fake_process

        await worker._process_batch([
            _job("s1", "doc-A", "sign_common"),
            _job("s2", "doc-A", "sign"),
        ])

        assert solapamiento is False

    @pytest.mark.asyncio
    async def test_respeta_el_orden_del_claim_dentro_del_documento(self):
        worker = EscriWorker()
        orden: list[str] = []

        async def fake_sign(job):
            orden.append(f"sign:{job['session_id']}")

        async def fake_common(job):
            orden.append(f"common:{job['session_id']}")

        worker._process_job = fake_sign
        worker._process_common_job = fake_common

        await worker._process_batch([
            _job("s1", "doc-A", "sign_common"),
            _job("s2", "doc-A", "sign"),
        ])

        assert orden == ["common:s1", "sign:s2"]

    @pytest.mark.asyncio
    async def test_documentos_distintos_siguen_en_paralelo(self):
        worker = EscriWorker()
        en_vuelo = 0
        max_en_vuelo = 0

        async def fake_process(job):
            nonlocal en_vuelo, max_en_vuelo
            en_vuelo += 1
            max_en_vuelo = max(max_en_vuelo, en_vuelo)
            await asyncio.sleep(0.01)
            en_vuelo -= 1

        worker._process_job = fake_process

        await worker._process_batch([
            _job("s1", "doc-A"),
            _job("s2", "doc-B"),
            _job("s3", "doc-C"),
        ])

        assert max_en_vuelo == 3

    @pytest.mark.asyncio
    async def test_todos_los_jobs_se_procesan(self):
        worker = EscriWorker()
        procesados: list[str] = []

        async def fake_process(job):
            procesados.append(job["session_id"])

        worker._process_job = fake_process
        worker._process_common_job = fake_process

        await worker._process_batch([
            _job("s1", "doc-A", "sign_common"),
            _job("s2", "doc-A", "sign"),
            _job("s3", "doc-B"),
        ])

        assert sorted(procesados) == ["s1", "s2", "s3"]

    @pytest.mark.asyncio
    async def test_un_job_que_falla_no_arrastra_a_los_demas(self):
        worker = EscriWorker()
        procesados: list[str] = []

        async def fake_process(job):
            if job["session_id"] == "s1":
                raise RuntimeError("boom")
            procesados.append(job["session_id"])

        worker._process_job = fake_process

        await worker._process_batch([_job("s1", "doc-A"), _job("s2", "doc-B")])

        assert procesados == ["s2"]

    @pytest.mark.asyncio
    async def test_jobs_sin_document_id_no_se_agrupan_entre_si(self):
        worker = EscriWorker()
        en_vuelo = 0
        max_en_vuelo = 0

        async def fake_citizen(job):
            nonlocal en_vuelo, max_en_vuelo
            en_vuelo += 1
            max_en_vuelo = max(max_en_vuelo, en_vuelo)
            await asyncio.sleep(0.01)
            en_vuelo -= 1

        worker._process_citizen_job = fake_citizen

        await worker._process_batch([
            {"session_id": "s1", "document_id": None, "job_type": "sign_citizen"},
            {"session_id": "s2", "document_id": None, "job_type": "sign_citizen"},
        ])

        assert max_en_vuelo == 2


class TestUxDel409:
    def test_el_detail_del_409_es_estructurado(self):
        from endpoints.documents import super_sign

        source = inspect.getsource(super_sign.super_sign)
        assert 'detail="document_already_signing"' not in source
        assert "DocumentAlreadySigningError" in source

    def test_conserva_el_slug_para_clientes_viejos(self):
        from endpoints.documents import super_sign

        source = inspect.getsource(super_sign.super_sign)
        assert '"code": "document_already_signing"' in source
