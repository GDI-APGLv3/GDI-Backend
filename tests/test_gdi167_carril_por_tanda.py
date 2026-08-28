
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from shared.numbering import _misma_tanda_y_mismo_usuario, _liberar_carril_special


TANDA = str(uuid.uuid4())
OTRA_TANDA = str(uuid.uuid4())
USUARIO = str(uuid.uuid4())
OTRO_USUARIO = str(uuid.uuid4())


class TestQuienPuedeCompartirElCarril:

    def test_misma_tanda_y_mismo_usuario_pasa(self):
        assert _misma_tanda_y_mismo_usuario(
            counter_batch_id=TANDA, batch_id=TANDA,
            titular_user_id=USUARIO, user_id=USUARIO,
        ) is True

    def test_misma_tanda_pero_otro_usuario_no_pasa(self):
        assert _misma_tanda_y_mismo_usuario(
            counter_batch_id=TANDA, batch_id=TANDA,
            titular_user_id=USUARIO, user_id=OTRO_USUARIO,
        ) is False

    def test_otra_tanda_del_mismo_usuario_no_pasa(self):
        assert _misma_tanda_y_mismo_usuario(
            counter_batch_id=TANDA, batch_id=OTRA_TANDA,
            titular_user_id=USUARIO, user_id=USUARIO,
        ) is False

    def test_sin_tanda_en_el_counter_no_pasa(self):
        assert _misma_tanda_y_mismo_usuario(
            counter_batch_id=None, batch_id=TANDA,
            titular_user_id=USUARIO, user_id=USUARIO,
        ) is False

    def test_pedido_sin_tanda_no_pasa(self):
        assert _misma_tanda_y_mismo_usuario(
            counter_batch_id=TANDA, batch_id=None,
            titular_user_id=USUARIO, user_id=USUARIO,
        ) is False

    def test_sin_titular_conocido_no_pasa(self):
        assert _misma_tanda_y_mismo_usuario(
            counter_batch_id=TANDA, batch_id=TANDA,
            titular_user_id=None, user_id=USUARIO,
        ) is False

    def test_compara_como_texto(self):
        u = uuid.UUID(TANDA)
        assert _misma_tanda_y_mismo_usuario(
            counter_batch_id=u, batch_id=TANDA,
            titular_user_id=uuid.UUID(USUARIO), user_id=USUARIO,
        ) is True


class TestCuandoSeSueltaElCarril:

    def _od(self, batch_id):
        return {
            "batch_id": batch_id,
            "document_type_id": 7,
            "year": 2026,
            "department_id": str(uuid.uuid4()),
        }

    @pytest.mark.asyncio
    async def test_sin_tanda_libera_como_siempre(self):
        conn = MagicMock()
        conn.execute = AsyncMock()
        conn.fetchval = AsyncMock()

        await _liberar_carril_special(conn, self._od(None), str(uuid.uuid4()))

        conn.execute.assert_awaited_once()
        conn.fetchval.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_con_hermanos_vivos_no_libera(self):
        conn = MagicMock()
        conn.execute = AsyncMock()
        conn.fetchval = AsyncMock(return_value=2)

        await _liberar_carril_special(conn, self._od(TANDA), str(uuid.uuid4()))

        conn.execute.assert_not_awaited(), "soltó el carril con la tanda todavía firmando"

    @pytest.mark.asyncio
    async def test_el_ultimo_de_la_tanda_libera_todo(self):
        conn = MagicMock()
        conn.execute = AsyncMock()
        conn.fetchval = AsyncMock(return_value=0)

        await _liberar_carril_special(conn, self._od(TANDA), str(uuid.uuid4()))

        conn.execute.assert_awaited_once()
        sql = conn.execute.await_args[0][0]
        assert "active_reservation_document_id = NULL" in sql
        assert "active_reservation_batch_id = NULL" in sql, (
            "el carril queda colgado apuntando a una tanda muerta"
        )

    @pytest.mark.asyncio
    async def test_una_fila_sin_la_columna_se_trata_como_sin_tanda(self):
        conn = MagicMock()
        conn.execute = AsyncMock()
        conn.fetchval = AsyncMock()

        od_viejo = {"document_type_id": 7, "year": 2026, "department_id": "x"}
        await _liberar_carril_special(conn, od_viejo, str(uuid.uuid4()))

        conn.execute.assert_awaited_once()


class TestLaFirmaDeAUnaNoCambia:

    def test_batch_id_es_keyword_only_y_opcional(self):
        import inspect
        from shared.numbering import reserve_number

        firma = inspect.signature(reserve_number)
        p = firma.parameters["batch_id"]
        assert p.kind is inspect.Parameter.KEYWORD_ONLY, (
            "batch_id posicional correría los argumentos de todos los callers "
            "en silencio — la regla del repo es keyword-only"
        )
        assert p.default is None

    def test_solo_el_dispatcher_puede_pasar_batch_id(self):
        import pathlib
        import re

        raiz = pathlib.Path(__file__).resolve().parents[1]
        autorizados = {"numbering.py", "dispatcher.py", "batch_digital.py"}
        sospechosos = []
        for archivo in raiz.rglob("*.py"):
            if "tests" in archivo.parts or ".git" in archivo.parts:
                continue
            if archivo.name in autorizados:
                continue
            texto = archivo.read_text(encoding="utf-8", errors="ignore")
            if "reserve_number(" in texto and re.search(r"batch_id\s*=", texto):
                sospechosos.append(archivo.name)
        assert not sospechosos, f"callers inesperados que mandan batch_id: {sospechosos}"

    def test_el_dispatcher_lo_pasa_solo_si_se_lo_dieron(self):
        import inspect
        from services.documents.signing import dispatcher

        firma = inspect.signature(dispatcher.dispatch_digital_signing)
        assert firma.parameters["batch_id"].default is None


class TestCancelarEnElPinLiberaElNumero:

    def test_la_rama_cancelled_llama_cancel_number(self):
        import inspect
        from endpoints.digital_signature import poll

        fuente = inspect.getsource(poll)
        i = fuente.index("if isinstance(result, PollSigningCancelled):")
        ventana = fuente[i:i + 1800]
        codigo = "\n".join(
            l for l in ventana.splitlines() if not l.strip().startswith("#")
        )
        assert "cancel_number(" in codigo, (
            "cancelar en el diálogo del PIN deja el número reservado"
        )

    def test_pasa_el_reservation_id(self):
        import inspect
        from endpoints.digital_signature import poll

        fuente = inspect.getsource(poll)
        i = fuente.index("if isinstance(result, PollSigningCancelled):")
        ventana = fuente[i:i + 1800]
        assert "reservation_id" in ventana

    def test_es_soft_fail(self):
        import inspect
        from endpoints.digital_signature import poll

        fuente = inspect.getsource(poll)
        i = fuente.index("if isinstance(result, PollSigningCancelled):")
        ventana = fuente[i:i + 1800]
        assert "soft-fail" in ventana
