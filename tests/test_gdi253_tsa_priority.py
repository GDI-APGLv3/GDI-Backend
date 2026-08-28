
from unittest.mock import MagicMock, patch

import pytest

from services.shared import dts_rate_limiter as rl


@pytest.fixture(autouse=True)
def _reset_state():
    with rl._fallback_lock:
        rl._fallback_window["minute"] = None
        rl._fallback_window["count"] = 0
    yield


class TestEffectiveCap:

    def test_priority_true_devuelve_el_max_completo(self):
        assert rl._effective_cap(60, priority=True) == 60

    def test_priority_false_default_5pct_con_60(self):
        assert rl._effective_cap(60, priority=False) == 57

    def test_priority_false_reserva_al_menos_1_con_limite_chico(self):
        assert rl._effective_cap(2, priority=False) == 1

    def test_priority_true_no_reserva_nada_con_limite_chico(self):
        assert rl._effective_cap(2, priority=True) == 2

    def test_limite_1_common_cap_da_cero(self):
        assert rl._effective_cap(1, priority=False) == 0
        assert rl._effective_cap(1, priority=True) == 1


class TestFallbackEnMemoria:

    def test_comun_se_frena_antes_que_special_con_limite_60(self):
        with (
            patch("services.shared.dts_rate_limiter._get_redis", return_value=None),
            patch.object(rl, "DTS_MAX_PER_MINUTE", 60),
            patch.object(rl, "DTS_DEGRADED_DIVISOR", 1),
            patch.object(rl, "SPECIAL_TSA_RESERVED_PCT", 0.05),
        ):
            for i in range(57):
                assert rl.try_consume_dts_slot(priority=False) is True, f"slot {i} debería entrar"

            assert rl.try_consume_dts_slot(priority=False) is False

            assert rl.try_consume_dts_slot(priority=True) is True
            assert rl.try_consume_dts_slot(priority=True) is True
            assert rl.try_consume_dts_slot(priority=True) is True
            assert rl.try_consume_dts_slot(priority=True) is False
            assert rl.try_consume_dts_slot(priority=False) is False

    def test_special_default_priority_false_no_accede_al_piso(self):
        with (
            patch("services.shared.dts_rate_limiter._get_redis", return_value=None),
            patch.object(rl, "DTS_MAX_PER_MINUTE", 60),
            patch.object(rl, "DTS_DEGRADED_DIVISOR", 1),
            patch.object(rl, "SPECIAL_TSA_RESERVED_PCT", 0.05),
        ):
            for _ in range(57):
                assert rl.try_consume_dts_slot() is True
            assert rl.try_consume_dts_slot() is False

    def test_limite_chico_reserva_al_menos_uno_en_memoria(self):
        with (
            patch("services.shared.dts_rate_limiter._get_redis", return_value=None),
            patch.object(rl, "DTS_MAX_PER_MINUTE", 2),
            patch.object(rl, "DTS_DEGRADED_DIVISOR", 1),
            patch.object(rl, "SPECIAL_TSA_RESERVED_PCT", 0.05),
        ):
            assert rl.try_consume_dts_slot(priority=False) is True
            assert rl.try_consume_dts_slot(priority=False) is False
            assert rl.try_consume_dts_slot(priority=True) is True
            assert rl.try_consume_dts_slot(priority=True) is False


class TestRedisPath:

    def test_priority_true_usa_cap_total_en_redis(self):
        m_redis = MagicMock()
        m_redis.eval = MagicMock(return_value=1)

        with (
            patch("services.shared.dts_rate_limiter._get_redis", return_value=m_redis),
            patch.object(rl, "DTS_MAX_PER_MINUTE", 60),
            patch.object(rl, "SPECIAL_TSA_RESERVED_PCT", 0.05),
        ):
            result = rl.try_consume_dts_slot(priority=True)

        assert result is True
        m_redis.eval.assert_called_once()
        args = m_redis.eval.call_args[0]
        assert args[3] == "60"

    def test_priority_false_usa_cap_reducido_en_redis(self):
        m_redis = MagicMock()
        m_redis.eval = MagicMock(return_value=1)

        with (
            patch("services.shared.dts_rate_limiter._get_redis", return_value=m_redis),
            patch.object(rl, "DTS_MAX_PER_MINUTE", 60),
            patch.object(rl, "SPECIAL_TSA_RESERVED_PCT", 0.05),
        ):
            result = rl.try_consume_dts_slot(priority=False)

        assert result is True
        args = m_redis.eval.call_args[0]
        assert args[3] == "57"

    def test_redis_rechazado_cae_a_fallback_en_memoria(self):
        m_redis = MagicMock()
        m_redis.eval = MagicMock(return_value=0)

        with (
            patch("services.shared.dts_rate_limiter._get_redis", return_value=m_redis),
            patch.object(rl, "DTS_MAX_PER_MINUTE", 60),
            patch.object(rl, "SPECIAL_TSA_RESERVED_PCT", 0.05),
        ):
            result = rl.try_consume_dts_slot(priority=False)

        assert result is False

    def test_redis_error_hace_fail_open_al_fallback_con_el_mismo_priority(self):
        m_redis = MagicMock()
        m_redis.eval = MagicMock(side_effect=RuntimeError("redis down"))

        with (
            patch("services.shared.dts_rate_limiter._get_redis", return_value=m_redis),
            patch.object(rl, "DTS_MAX_PER_MINUTE", 60),
            patch.object(rl, "DTS_DEGRADED_DIVISOR", 1),
            patch.object(rl, "SPECIAL_TSA_RESERVED_PCT", 0.05),
        ):
            for _ in range(57):
                assert rl.try_consume_dts_slot(priority=False) is True
            assert rl.try_consume_dts_slot(priority=False) is False
            assert rl.try_consume_dts_slot(priority=True) is True
