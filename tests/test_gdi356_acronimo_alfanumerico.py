import re

import pytest

from endpoints.documents import search_official


def _pattern() -> re.Pattern:
    import inspect

    sig = inspect.signature(search_official.search_official_document)
    declarado = sig.parameters["doc_number"].default

    pattern = getattr(declarado, "pattern", None)
    if not pattern:
        for meta in getattr(declarado, "metadata", None) or []:
            pattern = getattr(meta, "pattern", None)
            if pattern:
                break

    assert pattern, "el endpoint dejo de declarar un pattern para doc_number"
    return re.compile(pattern)


class TestNumerosQueElSistemaEmite:

    @pytest.mark.parametrize(
        "numero, motivo",
        [
            ("IF-2026-00016693-MDEV-INTE344", "el caso que abrio la card: sigla con digitos"),
            ("IF-2026-00016693-MDEV-SECOOPU", "sigla de 7 caracteres"),
            ("ANEXO-2025-00000002-SMG-ADGEN", "el clasico de solo letras sigue andando"),
            ("IF-2026-00000001-MUNI2026-OBRAS2", "municipio de 8 + reparticion con digito"),
            ("IF-2026-00000001-MDEV-REPARTICI1", "reparticion de 10"),
            ("PROV-2026-00003034-MDEV-TAD", "numerado por un ciudadano via TAD (GDI-130)"),
            ("IF-2026-1234-MDEV-LEGAL", "secuencia corta: el patron admite 1-9 digitos"),
            ("IF-2026-00000001-TXST-DIVAGUACLOA", "sigla larga de 11 caracteres"),
            ("IF-2026-00000001-TXST-DEPEDUCRECR2", "sigla larga de 12 caracteres"),
            ("IF-2026-00000001-ZYXW-ABCDEFGHIJKLMNOPQRST", "20: el largo de la columna"),
        ],
    )
    def test_acepta(self, numero, motivo):
        assert _pattern().match(numero), "deberia aceptar %r (%s)" % (numero, motivo)


class TestLoQueSigueRechazando:

    @pytest.mark.parametrize(
        "texto, motivo",
        [
            ("informe tecnico", "texto libre, no es un numero"),
            ("", "vacio"),
            ("IF-2026-00000001-MDEV-A", "sigla de 1 caracter: el minimo es 2"),
            ("IF-2026-00000001-MDEV-ABCDEFGHIJKLMNOPQRSTU", "reparticion de 21: pasa el maximo de 20"),
            ("IF-2026-00000001-MUNICIPIO9-LEGAL", "municipio de 10: pasa el maximo de 8"),
            ("if-2026-00016693-mdev-inte344", "minusculas: el numero se guarda en mayusculas"),
            ("IF-2026-00000001-MDEV", "le falta un segmento"),
            ("IF-26-00000001-MDEV-LEGAL", "el anio tiene que ser de 4 digitos"),
            ("IF-2026-0000000001-MDEV-LEGAL", "secuencia de 10: pasa el maximo de 9"),
            ("IF-2026-00000001-MDEV-LEG AL", "un espacio adentro"),
            ("IF-2026-00000001-MDEV-LEG_AL", "guion bajo: solo se admite [A-Z0-9]"),
        ],
    )
    def test_rechaza(self, texto, motivo):
        assert not _pattern().match(texto), "no deberia aceptar %r (%s)" % (texto, motivo)


class TestElBuscadorEsMasAnchoQueElAlta:

    ALTA_MUNICIPIO = 8
    ALTA_REPARTICION = 20

    def test_tolera_una_sigla_de_reparticion_del_maximo_del_alta(self):
        sigla = "R" * self.ALTA_REPARTICION
        assert _pattern().match("IF-2026-00000001-MDEV-%s" % sigla)

    def test_tolera_las_siglas_largas_que_ya_existen_en_produccion(self):
        for sigla in ("DIVAGUACLOA", "SECEDUCDEPO", "DEPEDUCRECR2", "D" * 19):
            assert _pattern().match("IF-2026-00000001-TXST-%s" % sigla), sigla

    def test_tolera_una_sigla_de_municipio_del_maximo_del_alta(self):
        sigla = "M" * self.ALTA_MUNICIPIO
        assert _pattern().match("IF-2026-00000001-%s-LEGAL" % sigla)

    def test_las_siglas_con_digitos_no_son_un_caso_borde(self):
        for sigla in ("OBRAS2", "D12", "2DA", "A1B2C3"):
            assert _pattern().match("IF-2026-00000001-MDEV-%s" % sigla), sigla
