
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch


SCHEMA = "100_test"
DOC = "aaaaaaaa-1111-2222-3333-444444444444"
USER = "11111111-1111-1111-1111-111111111111"
SESSION = "SESTEST0001"


def _sesion(**extra):
    base = {
        "session_id": SESSION,
        "schema_name": SCHEMA,
        "document_id": DOC,
        "user_id": USER,
        "status": "pending",
        "is_numerator": True,
        "number": "TOKEN-2026-00000001-TXST-AMBIE",
        "reservation_id": "res-1",
        "file_id": None,
        "batch_id": None,
        "failure_reason": None,
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=4),
    }
    base.update(extra)
    return base


def _request():
    req = MagicMock()
    req.state.tenant_user_id = USER
    return req


def _user():
    u = MagicMock()
    u.user_id = USER
    return u


async def _pollear(session):
    from endpoints.digital_signature import poll as poll_mod

    with patch.object(poll_mod, "_get_session", new_callable=AsyncMock, return_value=session), \
         patch.object(poll_mod, "_poll_rate_limit_ok", return_value=True), \
         patch.object(poll_mod, "release_signing_lock_R2_fail", new_callable=AsyncMock) as m_lock, \
         patch.object(poll_mod, "_mark_session_status", new_callable=AsyncMock, return_value=True) as m_mark, \
         patch.object(poll_mod, "cancel_number", new_callable=AsyncMock), \
         patch.object(poll_mod, "log_signature_event", new_callable=AsyncMock) as m_audit:
        r = await poll_mod.poll_signing(
            SESSION, _request(), current_user=_user(), schema_name=SCHEMA,
        )
    return r, m_audit, m_lock, m_mark


class TestSesionVencida:

    @pytest.mark.asyncio
    async def test_deja_su_fila_de_cierre(self):
        vencida = _sesion(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        r, m_audit, _, _ = await _pollear(vencida)

        assert r["status"] == "expired"
        m_audit.assert_awaited_once()
        kw = m_audit.await_args.kwargs
        assert kw["result"] == "fail"
        assert kw["failure_reason"] == "digital_session_expired"
        assert kw["signature_method"] == "digital_token"
        assert kw["session_id"] == SESSION
        assert kw["document_id"] == DOC
        assert kw["user_id"] == USER

    @pytest.mark.asyncio
    async def test_si_la_auditoria_falla_la_sesion_igual_queda_cerrada(self):
        from endpoints.digital_signature import poll as poll_mod

        vencida = _sesion(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))

        with patch.object(poll_mod, "_get_session", new_callable=AsyncMock, return_value=vencida), \
             patch.object(poll_mod, "_poll_rate_limit_ok", return_value=True), \
             patch.object(poll_mod, "release_signing_lock_R2_fail", new_callable=AsyncMock) as m_lock, \
             patch.object(poll_mod, "_mark_session_status", new_callable=AsyncMock, return_value=True) as m_mark, \
             patch.object(poll_mod, "cancel_number", new_callable=AsyncMock), \
             patch.object(poll_mod, "log_signature_event", new_callable=AsyncMock,
                          side_effect=RuntimeError("audit caida")):
            r = await poll_mod.poll_signing(
                SESSION, _request(), current_user=_user(), schema_name=SCHEMA,
            )

        assert r["status"] == "expired"
        m_lock.assert_awaited_once()
        m_mark.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_un_repoll_no_escribe_una_segunda_fila(self):
        ya_muerta = _sesion(status="expired",
                            expires_at=datetime.now(timezone.utc) - timedelta(minutes=5))
        r, m_audit, m_lock, m_mark = await _pollear(ya_muerta)

        assert r["status"] == "expired"
        m_audit.assert_not_awaited()
        m_lock.assert_not_awaited()
        m_mark.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_una_sesion_en_cierre_no_se_expira_ni_se_audita(self):
        cerrando = _sesion(status="completing",
                           expires_at=datetime.now(timezone.utc) - timedelta(minutes=5))
        r, m_audit, _, m_mark = await _pollear(cerrando)

        assert r["status"] == "completing"
        m_audit.assert_not_awaited()
        m_mark.assert_not_awaited()


class TestNingunDesenlaceQuedaMudo:

    ESTADOS_TERMINALES = {"failed", "expired", "cancelled"}

    def _bloques(self, nodo):
        import ast
        for hijo in ast.walk(nodo):
            for campo in ("body", "orelse", "finalbody"):
                cuerpo = getattr(hijo, campo, None)
                if isinstance(cuerpo, list) and cuerpo:
                    yield cuerpo

    def _llama_a(self, statements, nombre):
        import ast
        for st in statements:
            for n in ast.walk(st):
                if isinstance(n, ast.Call):
                    f = n.func
                    if isinstance(f, ast.Name) and f.id == nombre:
                        return n
                    if isinstance(f, ast.Attribute) and f.attr == nombre:
                        return n
        return None

    def test_todo_cierre_terminal_escribe_su_fila(self):
        import ast
        import pathlib

        fuente = pathlib.Path("endpoints/digital_signature/poll.py").read_text(encoding="utf-8")
        arbol = ast.parse(fuente)

        mudos = []
        for cuerpo in self._bloques(arbol):
            marca = self._llama_a(cuerpo, "_mark_session_status")
            if marca is None:
                continue
            if len(marca.args) < 2 or not isinstance(marca.args[1], ast.Constant):
                continue
            estado = marca.args[1].value
            if estado not in self.ESTADOS_TERMINALES:
                continue
            if self._llama_a(cuerpo, "log_signature_event") is None:
                mudos.append((estado, marca.lineno))

        assert not mudos, (
            "hay cierres terminales que no dejan fila en firma_audit_log: "
            + ", ".join(f"{e} en linea {l}" for e, l in mudos)
        )


class TestElQueLlegaSegundoNoEscribe:

    @pytest.mark.asyncio
    async def test_si_el_sweeper_gano_la_carrera_no_se_audita(self):
        from endpoints.digital_signature import poll as poll_mod

        vencida = _sesion(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))

        with patch.object(poll_mod, "_get_session", new_callable=AsyncMock, return_value=vencida),              patch.object(poll_mod, "_poll_rate_limit_ok", return_value=True),              patch.object(poll_mod, "release_signing_lock_R2_fail", new_callable=AsyncMock),              patch.object(poll_mod, "_mark_session_status", new_callable=AsyncMock,
                          return_value=False) as m_mark,              patch.object(poll_mod, "cancel_number", new_callable=AsyncMock),              patch.object(poll_mod, "log_signature_event", new_callable=AsyncMock) as m_audit:
            r = await poll_mod.poll_signing(
                SESSION, _request(), current_user=_user(), schema_name=SCHEMA,
            )

        assert r["status"] == "expired"
        m_mark.assert_awaited_once()
        m_audit.assert_not_awaited()

    def test_ninguna_rama_audita_sin_mirar_el_resultado_del_cas(self):
        import ast
        import pathlib

        fuente = pathlib.Path("endpoints/digital_signature/poll.py").read_text(encoding="utf-8")
        arbol = ast.parse(fuente)

        terminales = TestNingunDesenlaceQuedaMudo.ESTADOS_TERMINALES
        ayudante = TestNingunDesenlaceQuedaMudo()

        sueltos = []
        for cuerpo in ayudante._bloques(arbol):
            marca = ayudante._llama_a(cuerpo, "_mark_session_status")
            if marca is None:
                continue
            if len(marca.args) < 2 or not isinstance(marca.args[1], ast.Constant):
                continue
            if marca.args[1].value not in terminales:
                continue
            if ayudante._llama_a(cuerpo, "log_signature_event") is None:
                continue

            guardado = False
            for st in cuerpo:
                for n in ast.walk(st):
                    if not isinstance(n, ast.Assign):
                        continue
                    if ayudante._llama_a([ast.Expr(value=n.value)], "_mark_session_status"):
                        guardado = True
            if not guardado:
                sueltos.append((marca.args[1].value, marca.lineno))

        assert not sueltos, (
            "hay cierres que auditan sin mirar si el CAS gano (riesgo de fila "
            "duplicada en firma_audit_log, que es INSERT-only): "
            + ", ".join(f"{e} en linea {l}" for e, l in sueltos)
        )
