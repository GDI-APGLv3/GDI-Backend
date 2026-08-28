

import inspect
import os
from unittest.mock import patch

import pytest

import config.constants as constants


class TestTtlFirmaDigitalUnico:

    def test_los_tres_modulos_usan_la_misma_constante(self):
        from services.documents.signing.providers import autofirma, firmador_gdi
        from endpoints.digital_signature import storage

        esperado = constants.DIGITAL_SIGNATURE_SESSION_TTL_SECONDS
        assert autofirma.TTL_SECONDS == esperado
        assert firmador_gdi.TTL_SECONDS == esperado
        assert storage.TTL_SECONDS == esperado

    def test_el_poll_ya_no_renueva_la_sesion_por_espera_de_sello(self):
        from endpoints.digital_signature import poll

        assert not hasattr(poll, "SPECIAL_TSA_RENEW_SECONDS")
        assert not hasattr(poll, "SPECIAL_TSA_WAIT_MAX_MINUTES")

    def test_ningun_modulo_redefine_el_240(self):
        from services.documents.signing.providers import autofirma, firmador_gdi
        from endpoints.digital_signature import storage

        for mod in (autofirma, firmador_gdi, storage):
            assert "TTL_SECONDS = 240" not in inspect.getsource(mod), mod.__name__

    def test_se_puede_cambiar_por_env(self):
        import subprocess
        import sys
        from pathlib import Path

        entorno = {**os.environ, "DIGITAL_SIGNATURE_SESSION_TTL_SECONDS": "600"}
        salida = subprocess.run(
            [sys.executable, "-c",
             "from config.constants import DIGITAL_SIGNATURE_SESSION_TTL_SECONDS as t; print(t)"],
            cwd=str(Path(__file__).resolve().parents[1]),
            env=entorno,
            capture_output=True,
            text=True,
        )
        assert salida.returncode == 0, salida.stderr
        assert salida.stdout.strip().splitlines()[-1] == "600"


class TestKillSwitchWorker:

    def test_por_defecto_esta_prendido(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ESCRI_WORKER_ENABLED", None)
            assert constants.escri_worker_enabled() is True

    @pytest.mark.parametrize("valor", ["false", "False", "0", "no", "off", "OFF"])
    def test_apaga_con_los_valores_habituales(self, valor):
        with patch.dict(os.environ, {"ESCRI_WORKER_ENABLED": valor}):
            assert constants.escri_worker_enabled() is False

    @pytest.mark.parametrize("valor", ["true", "1", "yes", "on"])
    def test_prende_con_los_valores_habituales(self, valor):
        with patch.dict(os.environ, {"ESCRI_WORKER_ENABLED": valor}):
            assert constants.escri_worker_enabled() is True

    def test_se_lee_en_cada_drenado_no_al_importar(self):
        from workers import escri

        source = inspect.getsource(escri.EscriWorker._drain_pending)
        assert "escri_worker_enabled()" in source

    def test_el_drenado_no_reclama_jobs_con_el_switch_apagado(self):
        source = inspect.getsource(__import__("workers.escri", fromlist=["escri"]).EscriWorker._drain_pending)
        assert source.index("escri_worker_enabled()") < source.index("_claim_batch")


class TestCoherenciaTtlDrenado:

    def test_la_configuracion_actual_es_coherente(self):
        ok, msg = constants.check_escri_ttl_coherence()
        assert ok, msg

    def test_detecta_un_tope_que_no_drena(self):
        with (
            patch.object(constants, "ESCRI_QUEUE_MAX_GLOBAL", 5000),
            patch.object(constants, "ESCRI_CONCURRENCY", 1),
            patch.object(constants, "GUNICORN_WORKERS", 1),
        ):
            ok, msg = constants.check_escri_ttl_coherence()
            assert ok is False
            assert "INCOHERENTE" in msg

    def test_el_mensaje_explica_la_cuenta(self):
        _, msg = constants.check_escri_ttl_coherence()
        assert str(constants.ESCRI_QUEUE_MAX_GLOBAL) in msg
        assert "TTL" in msg

    def test_la_estimacion_usa_la_concurrencia_real_no_solo_la_constante(self):
        with (
            patch.object(constants, "ESCRI_QUEUE_MAX_GLOBAL", 100),
            patch.object(constants, "ESCRI_JOB_SECONDS_ESTIMATE", 6.0),
            patch.object(constants, "ESCRI_CONCURRENCY", 2),
            patch.object(constants, "GUNICORN_WORKERS", 2),
        ):
            assert constants.escri_queue_drain_estimate_minutes() == pytest.approx(2.5)

    def test_el_worker_lo_chequea_al_arrancar(self):
        from workers import escri

        assert "check_escri_ttl_coherence" in inspect.getsource(escri.EscriWorker.run)


class TestIndiceNumeracion:

    def _template(self) -> str:
        from pathlib import Path

        ruta = Path(__file__).resolve().parents[2] / "GDI-BD" / "sql" / "03-create-municipio.sql"
        if not ruta.exists():
            pytest.skip("GDI-BD no está en este worktree")
        return ruta.read_text(encoding="utf-8")

    def test_el_indice_existe_en_el_template(self):
        assert "official_docs_year_seq" in self._template()

    def test_el_indice_no_filtra_por_reservation_status(self):
        template = self._template()
        bloque = template[template.index("official_docs_year_seq"):]
        bloque = bloque[: bloque.index(";")]
        assert "reservation_status" not in bloque
        assert "global_sequence IS NOT NULL" in bloque

    def test_el_max_mas_uno_vive_en_un_solo_lugar(self):
        import shared.numbering as numbering

        source = inspect.getsource(numbering)
        assert source.count("COALESCE(MAX(global_sequence), 0) + 1") == 1, (
            "El MAX+1 de la numeración GLOBAL tiene que vivir SOLO dentro de "
            "_next_global_sequence(). Apareció otra copia suelta."
        )
        assert source.count("AND reservation_status IN ('RESERVED','CONFIRMING','CONFIRMED')") == 1

    def test_las_cuatro_puertas_llaman_a_la_unica(self):
        import shared.numbering as numbering

        for func in (
            numbering.reserve_citizen_number,
            numbering.reserve_number,
            numbering._generate_confirmed_number,
        ):
            assert "_next_global_sequence" in inspect.getsource(func), (
                f"{func.__name__} no pide el número por la puerta única"
            )

        for wrapper in (
            numbering.generate_official_number,
            numbering.generate_citizen_official_number,
        ):
            fuente = inspect.getsource(wrapper)
            assert "_generate_confirmed_number" in fuente, (
                f"{wrapper.__name__} no pasa por el núcleo compartido"
            )
            assert "INSERT INTO official_documents" not in fuente, (
                f"{wrapper.__name__} inserta por su cuenta en vez de delegar"
            )
            assert "_next_global_sequence" not in fuente, (
                f"{wrapper.__name__} pide número por fuera del núcleo"
            )

    def test_la_puerta_unica_recicla_antes_de_incrementar(self):
        import shared.numbering as numbering

        fuente = inspect.getsource(numbering._next_global_sequence)
        assert fuente.index("reservation_status = 'CANCELLED'") < fuente.index(
            "COALESCE(MAX(global_sequence), 0) + 1"
        )
        assert "FOR UPDATE SKIP LOCKED" in fuente
        assert "AND reservation_status IN ('RESERVED','CONFIRMING','CONFIRMED')" in fuente
