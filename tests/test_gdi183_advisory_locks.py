
import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.advisory_lock import (
    LOCK_ID_ORPHAN_INPROCESS,
    LOCK_ID_SWEEPER_ESCRI,
    LOCK_ID_SWEEPER_TAD_WEBHOOK,
    LOCK_ID_TST_SWEEP,
)


def _conn_que_gana():
    conn = MagicMock()
    conn.fetchval = AsyncMock(side_effect=[True, True])
    return conn


def _conn_que_pierde():
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=False)
    return conn


def _ctx_de(conn):
    @asynccontextmanager
    async def _ctx(**kwargs):
        yield conn
    return _ctx


class TestCatalogoDeIds:
    def test_ids_son_todos_distintos(self):
        ids = [
            888888,
            LOCK_ID_SWEEPER_ESCRI,
            LOCK_ID_SWEEPER_TAD_WEBHOOK,
            LOCK_ID_ORPHAN_INPROCESS,
            LOCK_ID_TST_SWEEP,
        ]
        assert len(ids) == len(set(ids)), f"IDs duplicados en el catálogo: {ids}"

    def test_sweeper_escri_conserva_su_id_historico(self):
        from workers.sweeper_escri import SWEEPER_ADVISORY_LOCK_ID
        assert SWEEPER_ADVISORY_LOCK_ID == LOCK_ID_SWEEPER_ESCRI == 888890


class TestGlobalJobLock:
    @pytest.mark.asyncio
    async def test_usa_try_lock_de_sesion_y_no_el_de_transaccion(self):
        from shared.advisory_lock import global_job_lock

        conn = _conn_que_gana()
        with patch("shared.advisory_lock.get_conn", _ctx_de(conn)):
            async with global_job_lock(999001, "prueba") as got:
                assert got is True

        sqls = [c.args[0] for c in conn.fetchval.await_args_list]
        assert sqls[0] == "SELECT pg_try_advisory_lock($1)"
        assert sqls[1] == "SELECT pg_advisory_unlock($1)"
        assert not any("xact" in s for s in sqls)

    @pytest.mark.asyncio
    async def test_perdedor_no_libera_candado_ajeno(self):
        from shared.advisory_lock import global_job_lock

        conn = _conn_que_pierde()
        with patch("shared.advisory_lock.get_conn", _ctx_de(conn)):
            async with global_job_lock(999002, "prueba") as got:
                assert got is False

        assert conn.fetchval.await_count == 1

    @pytest.mark.asyncio
    async def test_libera_aunque_el_cuerpo_explote(self):
        from shared.advisory_lock import global_job_lock

        conn = _conn_que_gana()
        with patch("shared.advisory_lock.get_conn", _ctx_de(conn)):
            with pytest.raises(RuntimeError):
                async with global_job_lock(999003, "prueba") as got:
                    assert got is True
                    raise RuntimeError("boom")

        sqls = [c.args[0] for c in conn.fetchval.await_args_list]
        assert sqls[-1] == "SELECT pg_advisory_unlock($1)"


class TestOrphanInprocess:

    @pytest.mark.asyncio
    async def test_con_candado_recupera_y_libera(self):
        from jobs.orphan_inprocess import reclaim_expired_pending_sessions

        conn = _conn_que_gana()
        body = AsyncMock()
        with patch("shared.advisory_lock.get_conn", _ctx_de(conn)), \
             patch("jobs.orphan_inprocess._reclaim_async", body):
            await reclaim_expired_pending_sessions()

        body.assert_awaited_once()
        calls = [c.args for c in conn.fetchval.await_args_list]
        assert calls[0] == ("SELECT pg_try_advisory_lock($1)", LOCK_ID_ORPHAN_INPROCESS)
        assert calls[1] == ("SELECT pg_advisory_unlock($1)", LOCK_ID_ORPHAN_INPROCESS)

    @pytest.mark.asyncio
    async def test_sin_candado_no_toca_la_auditoria(self):
        from jobs.orphan_inprocess import reclaim_expired_pending_sessions

        conn = _conn_que_pierde()
        body = AsyncMock()
        with patch("shared.advisory_lock.get_conn", _ctx_de(conn)), \
             patch("jobs.orphan_inprocess._reclaim_async", body):
            await reclaim_expired_pending_sessions()

        body.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_error_del_cuerpo_no_propaga_pero_libera(self):
        from jobs.orphan_inprocess import reclaim_expired_pending_sessions

        conn = _conn_que_gana()
        body = AsyncMock(side_effect=RuntimeError("boom"))
        with patch("shared.advisory_lock.get_conn", _ctx_de(conn)), \
             patch("jobs.orphan_inprocess._reclaim_async", body):
            await reclaim_expired_pending_sessions()

        assert conn.fetchval.await_count == 2


class TestSweeperTadWebhook:
    @pytest.mark.asyncio
    async def test_con_candado_barre_y_libera(self):
        from workers.sweeper_tad_webhook import sweep_tad_webhook_jobs

        conn = _conn_que_gana()
        ex = AsyncMock(return_value="UPDATE 0")
        with patch("shared.advisory_lock.get_conn", _ctx_de(conn)), \
             patch("workers.sweeper_tad_webhook.execute", ex):
            await sweep_tad_webhook_jobs()

        ex.assert_awaited_once()
        calls = [c.args for c in conn.fetchval.await_args_list]
        assert calls[0] == ("SELECT pg_try_advisory_lock($1)", LOCK_ID_SWEEPER_TAD_WEBHOOK)
        assert calls[1] == ("SELECT pg_advisory_unlock($1)", LOCK_ID_SWEEPER_TAD_WEBHOOK)

    @pytest.mark.asyncio
    async def test_sin_candado_no_hace_el_update(self):
        from workers.sweeper_tad_webhook import sweep_tad_webhook_jobs

        conn = _conn_que_pierde()
        ex = AsyncMock()
        with patch("shared.advisory_lock.get_conn", _ctx_de(conn)), \
             patch("workers.sweeper_tad_webhook.execute", ex):
            await sweep_tad_webhook_jobs()

        ex.assert_not_awaited()

    def test_registra_con_max_instances(self):
        from workers.sweeper_tad_webhook import schedule_sweeper_tad_webhook

        sched = MagicMock()
        schedule_sweeper_tad_webhook(sched)
        kw = sched.add_job.call_args.kwargs
        assert kw["max_instances"] == 1
        assert kw["coalesce"] is True


class TestTstSweep:
    @pytest.mark.asyncio
    async def test_con_candado_barre_y_libera(self):
        from jobs.fill_number_gaps_tst import run_tst_sweep

        conn = _conn_que_gana()
        body = AsyncMock()
        with patch("shared.advisory_lock.get_conn", _ctx_de(conn)), \
             patch("jobs.fill_number_gaps_tst._run_sweep", body):
            await run_tst_sweep()

        body.assert_awaited_once()
        calls = [c.args for c in conn.fetchval.await_args_list]
        assert calls[0] == ("SELECT pg_try_advisory_lock($1)", LOCK_ID_TST_SWEEP)
        assert calls[1] == ("SELECT pg_advisory_unlock($1)", LOCK_ID_TST_SWEEP)

    @pytest.mark.asyncio
    async def test_sin_candado_no_manda_mails_duplicados(self):
        from jobs.fill_number_gaps_tst import run_tst_sweep

        conn = _conn_que_pierde()
        body = AsyncMock()
        with patch("shared.advisory_lock.get_conn", _ctx_de(conn)), \
             patch("jobs.fill_number_gaps_tst._run_sweep", body):
            await run_tst_sweep()

        body.assert_not_awaited()

    def test_los_dos_cron_comparten_id_de_candado_a_proposito(self):
        from jobs.fill_number_gaps_tst import (
            schedule_tst_sweep, TST_SWEEP_HOUR_1, TST_SWEEP_HOUR_2,
        )

        sched = MagicMock()
        schedule_tst_sweep(sched)
        assert sched.add_job.call_count == 2
        assert TST_SWEEP_HOUR_1 != TST_SWEEP_HOUR_2


class TestConcurrenciaEntreCopias:

    @staticmethod
    def _fabrica_de_conexiones():
        tomado = {"por": None}

        def nueva_conn(worker_id):
            conn = MagicMock()

            async def fetchval(sql, lock_id):
                if "try_advisory_lock" in sql:
                    if tomado["por"] is None:
                        tomado["por"] = worker_id
                        return True
                    return False
                if "advisory_unlock" in sql:
                    assert tomado["por"] == worker_id, \
                        "un worker intentó liberar un candado que no era suyo"
                    tomado["por"] = None
                    return True
                raise AssertionError(f"SQL inesperado: {sql}")

            conn.fetchval = AsyncMock(side_effect=fetchval)
            return conn

        return nueva_conn

    @pytest.mark.asyncio
    @pytest.mark.parametrize("n_workers", [2, 6, 12])
    async def test_solo_una_copia_escribe_la_auditoria(self, n_workers):
        from jobs.orphan_inprocess import reclaim_expired_pending_sessions

        nueva_conn = self._fabrica_de_conexiones()
        ejecuciones = []

        async def cuerpo_lento():
            ejecuciones.append(1)
            await asyncio.sleep(0.01)

        async def una_copia(worker_id):
            conn = nueva_conn(worker_id)
            with patch("shared.advisory_lock.get_conn", _ctx_de(conn)), \
                 patch("jobs.orphan_inprocess._reclaim_async", cuerpo_lento):
                await reclaim_expired_pending_sessions()

        await asyncio.gather(*(una_copia(i) for i in range(n_workers)))

        assert len(ejecuciones) == 1, (
            f"con {n_workers} workers el reclaim corrió {len(ejecuciones)} veces: "
            f"eso son {len(ejecuciones)} eventos duplicados en firma_audit_log"
        )

    @pytest.mark.asyncio
    async def test_el_candado_queda_libre_para_la_corrida_siguiente(self):
        from jobs.orphan_inprocess import reclaim_expired_pending_sessions

        nueva_conn = self._fabrica_de_conexiones()
        ejecuciones = []

        async def cuerpo_que_explota():
            ejecuciones.append(1)
            raise RuntimeError("el worker se cayó a mitad del barrido")

        for worker_id in range(3):
            conn = nueva_conn(worker_id)
            with patch("shared.advisory_lock.get_conn", _ctx_de(conn)), \
                 patch("jobs.orphan_inprocess._reclaim_async", cuerpo_que_explota):
                await reclaim_expired_pending_sessions()

        assert len(ejecuciones) == 3, \
            "tras un fallo el candado quedó pegado y la tarea no volvió a correr"
