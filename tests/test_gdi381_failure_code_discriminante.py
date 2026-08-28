
import pytest

from shared.exceptions import (
    NotaryBreakerOpenError,
    NotaryBusinessError,
    NotaryHashMismatchError,
    NotaryTimeoutError,
    NotaryUnavailableError,
)
from workers.escri import _failure_code


class TestCodigosEstablesNoCambian:

    def test_hash_mismatch(self):
        assert _failure_code(NotaryHashMismatchError("x")) == "pdf_integrity_failed"

    def test_breaker_abierto(self):
        assert _failure_code(NotaryBreakerOpenError("x")) == "notary_circuit_open"

    def test_fullpage(self):
        assert _failure_code(NotaryBusinessError("notary_fullpage: no entra")) == "notary_fullpage"

    def test_business_error_generico(self):
        assert _failure_code(NotaryBusinessError("otra cosa")) == "notary_business_error"

    def test_timeout(self):
        assert _failure_code(NotaryTimeoutError("x")) == "notary_timeout"

    def test_notary_503(self):
        exc = NotaryUnavailableError("x")
        exc.status_code = 503
        assert _failure_code(exc) == "notary_503"

    def test_pdf_too_large(self):
        assert _failure_code(RuntimeError("pdf_too_large: 20MB")) == "pdf_too_large"

    @pytest.mark.parametrize(
        "exc",
        [
            NotaryHashMismatchError("x"),
            NotaryBreakerOpenError("x"),
            NotaryBusinessError("otra cosa"),
            NotaryTimeoutError("x"),
            RuntimeError("pdf_too_large: 20MB"),
        ],
    )
    def test_ninguna_clase_ya_clasificada_cae_en_unknown(self, exc):
        assert not _failure_code(exc).startswith("unknown")


class TestBaldeUnknownDiscrimina:

    def test_timeout_pelado_trae_la_clase(self):
        assert _failure_code(TimeoutError("se acabo el tiempo")) == "unknown:TimeoutError"

    def test_error_nuestro_trae_la_clase(self):
        assert _failure_code(KeyError("document_id")) == "unknown:KeyError"

    def test_dos_excepciones_distintas_dan_codigos_DISTINTOS(self):
        assert _failure_code(TimeoutError("a")) != _failure_code(ValueError("b"))

    def test_sigue_siendo_un_codigo_discreto(self):
        assert _failure_code(ValueError("mensaje A")) == _failure_code(ValueError("mensaje B"))

    def test_prefijo_unknown_se_mantiene(self):
        assert _failure_code(ValueError("x")).startswith("unknown:")


class TestNoFiltraDatosSensibles:

    def test_el_mensaje_no_aparece_en_el_codigo(self):
        secreto = "CUIT 20-12345678-9 de Juan Perez"
        codigo = _failure_code(ValueError(secreto))
        assert secreto not in codigo
        assert "12345678" not in codigo
        assert codigo == "unknown:ValueError"

    def test_el_codigo_es_un_identificador_python(self):
        codigo = _failure_code(ValueError("con espacios\ny saltos"))
        nombre = codigo.split(":", 1)[1]
        assert nombre.isidentifier()
