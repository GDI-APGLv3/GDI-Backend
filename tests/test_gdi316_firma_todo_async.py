import inspect

import pytest
from unittest.mock import AsyncMock, patch

from services.documents.signing import unified_signing


class TestElFlagNoExiste:

    def test_no_queda_lectura_del_flag_en_el_ruteo(self):
        src = inspect.getsource(unified_signing)
        assert "get_async_signing_flag" not in src
        assert "use_async" not in src

    def test_la_funcion_que_lo_leia_se_borro(self):
        from services.shared import settings_utils

        assert not hasattr(settings_utils, "get_async_signing_flag")
        assert not hasattr(settings_utils, "invalidate_async_flag_cache")
        assert not hasattr(settings_utils, "_async_flag_cache")

    def test_ningun_modulo_del_backend_lee_la_columna(self):
        from services.shared import settings_utils
        from services.documents.signing import numerator

        for mod in (unified_signing, settings_utils, numerator):
            src = inspect.getsource(mod)
            assert "SELECT electronic_signing_async" not in src
            assert "settings.electronic_signing_async FROM" not in src


class TestElRuteoQueQuedo:

    def test_el_numerador_llama_directo_al_carril_async(self):
        src = inspect.getsource(unified_signing.super_sign_document)
        i_enqueue = src.index("_try_reserve_and_enqueue")
        anterior = src[:i_enqueue].rstrip().splitlines()[-1]
        assert "if " not in anterior, f"la llamada quedó gateada por: {anterior!r}"

    def test_el_firmante_comun_llama_directo_al_carril_async(self):
        src = inspect.getsource(unified_signing.super_sign_document)
        i_enqueue = src.index("_try_enqueue_common_signer")
        anterior = src[:i_enqueue].rstrip().splitlines()[-1]
        assert "if " not in anterior, f"la llamada quedó gateada por: {anterior!r}"

    def test_special_sigue_yendo_por_el_camino_sincronico(self):
        src = inspect.getsource(unified_signing.super_sign_document)
        assert "sign_document_as_numerator" in src
        assert "SPECIAL" in src

    def test_el_carril_sincronico_no_se_borro(self):
        from services.documents.signing.numerator import sign_document_as_numerator

        assert callable(sign_document_as_numerator)


class TestElFallbackDelComunNoEsOcioso:

    def test_el_none_del_encolado_cae_al_sincronico(self):
        src = inspect.getsource(unified_signing.super_sign_document)
        i = src.index("_try_enqueue_common_signer")
        fragmento = src[i:i + 900]
        assert "sign_document(" in fragmento

    def test_el_docstring_del_encolado_explica_por_que_devuelve_none(self):
        doc = inspect.getdoc(unified_signing._try_enqueue_common_signer) or ""
        assert "lock" in doc.lower()
        assert "409" in doc


class TestLaMigracionFinal:

    def _sql(self):
        from pathlib import Path

        ruta = (Path(__file__).resolve().parents[2] / "GDI-BD" / "sql" /
                "migrations" / "107_gdi316_drop_electronic_signing_async.sql")
        if not ruta.exists():
            pytest.skip("GDI-BD no está en el worktree — la migración vive en ese repo")
        return ruta.read_text(encoding="utf-8")

    def test_dropea_la_columna_en_todos_los_schemas(self):
        sql = self._sql()
        assert "DROP COLUMN IF EXISTS electronic_signing_async" in sql
        assert "FOR s IN" in sql, "tiene que recorrer los schemas, no uno solo"

    def test_avisa_que_va_despues_del_deploy(self):
        sql = self._sql()
        assert "DESPUÉS del deploy" in sql
