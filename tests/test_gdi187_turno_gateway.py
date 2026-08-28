
import inspect

from api_gateway.tools import documents as gw_documents
from services.documents.retrieval.pending_signatures import _is_my_turn_condition


def _squash(sql: str) -> str:
    return " ".join(sql.split())


class TestDefinicionUnica:
    def test_gateway_usa_el_helper_canonico(self):
        source = inspect.getsource(gw_documents.get_pending_signatures)
        assert "_is_my_turn_condition" in source

    def test_gateway_ya_no_tiene_la_condicion_vieja(self):
        source = inspect.getsource(gw_documents.get_pending_signatures)
        sql_only = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )
        assert "ds2.is_numerator = pd.is_numerator" not in _squash(sql_only)

    def test_el_helper_frena_al_numerador_con_comunes_pendientes(self):
        cond = _squash(_is_my_turn_condition("pd"))
        assert "pd.is_numerator = true AND ds2.is_numerator = false" in cond

    def test_el_helper_no_deja_que_el_numerador_frene_a_un_comun(self):
        cond = _squash(_is_my_turn_condition("pd"))
        assert "pd.is_numerator = false AND ds2.is_numerator = false" in cond
        assert "ds2.signing_order < pd.signing_order" in cond

    def test_la_lista_y_el_flag_usan_la_misma_condicion(self):
        source = inspect.getsource(gw_documents.get_pending_signatures)
        assert source.count("_is_my_turn_condition('pd')") == 2
