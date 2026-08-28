import re

from services.documents.retrieval.pending_signatures import (
    _is_my_turn_condition,
    _PENDING_SIGNATURES_QUERY,
    _PENDING_SIGNATURES_COUNT_QUERY,
)


def _squash(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


class TestIsMyTurnCondition:
    def test_numerador_lo_frena_cualquier_firmante_comun(self):
        cond = _squash(_is_my_turn_condition("pd"))
        assert "pd.is_numerator = true AND ds2.is_numerator = false" in cond
        rama_numerador = cond.split("OR")[0]
        assert "signing_order" not in rama_numerador

    def test_firmante_comun_solo_espera_a_los_comunes_de_orden_menor(self):
        cond = _squash(_is_my_turn_condition("pd"))
        rama_comun = cond.split("OR")[1]
        assert "ds2.is_numerator = false" in rama_comun
        assert "ds2.signing_order < pd.signing_order" in rama_comun

    def test_el_numerador_no_frena_a_un_firmante_comun(self):
        cond = _squash(_is_my_turn_condition("pd"))
        assert "ds2.is_numerator = true" not in cond

    def test_no_vuelve_la_comparacion_vieja(self):
        cond = _squash(_is_my_turn_condition("pd"))
        assert "ds2.is_numerator = pd.is_numerator" not in cond

    def test_usa_el_alias_que_se_le_pasa(self):
        assert "ds.document_id" in _is_my_turn_condition("ds")
        assert "pd.document_id" in _is_my_turn_condition("pd")

    def test_solo_mira_firmas_pendientes(self):
        cond = _squash(_is_my_turn_condition("pd"))
        assert "ds2.status = 'pending'" in cond


class TestListaYCountUsanLaMismaCondicion:
    def test_la_lista_la_incluye(self):
        assert _squash(_is_my_turn_condition("pd")) in _squash(_PENDING_SIGNATURES_QUERY)

    def test_el_count_la_incluye(self):
        assert _squash(_is_my_turn_condition("ds")) in _squash(_PENDING_SIGNATURES_COUNT_QUERY)

    def test_is_my_turn_sigue_expuesto_en_la_lista(self):
        assert "as is_my_turn" in _PENDING_SIGNATURES_QUERY
