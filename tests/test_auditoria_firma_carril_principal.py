
import ast
import inspect
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest


def _llamadas_por_nombre(func_node: ast.AST) -> dict[str, int]:
    conteo: dict[str, int] = {}
    for nodo in ast.walk(func_node):
        if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute):
            conteo[nodo.func.attr] = conteo.get(nodo.func.attr, 0) + 1
    return conteo


def _func_node(nombre_metodo: str) -> ast.AST:
    import workers.escri as escri_mod

    arbol = ast.parse(inspect.getsource(escri_mod))
    for nodo in ast.walk(arbol):
        if isinstance(nodo, (ast.AsyncFunctionDef, ast.FunctionDef)) and nodo.name == nombre_metodo:
            return nodo
    raise AssertionError(f"no encontré {nombre_metodo} en workers/escri.py")


def test_cada_firma_dada_por_buena_deja_su_auditoria():
    llamadas = _llamadas_por_nombre(_func_node("_process_job"))
    marcadas = llamadas.get("_mark_session_signed", 0)
    auditadas = llamadas.get("_audit_firma", 0)

    assert marcadas > 0, "cambió la estructura: _process_job ya no marca sesiones"
    assert auditadas >= marcadas, (
        f"_process_job marca {marcadas} firmas como buenas pero solo audita "
        f"{auditadas}. Cada camino de éxito tiene que llamar a _audit_firma."
    )


def test_el_fallo_de_firma_tambien_se_audita():
    llamadas = _llamadas_por_nombre(_func_node("_process_job"))
    assert llamadas.get("_mark_session_failed", 0) > 0
    assert llamadas.get("_audit_firma", 0) >= 2, (
        "se esperan auditorías de éxito Y de fallo en _process_job"
    )


@pytest.mark.asyncio
async def test_audit_firma_delega_en_log_signature_event():
    from workers.escri import EscriWorker

    worker = EscriWorker.__new__(EscriWorker)
    espia = AsyncMock()
    with patch("services.documents.signing.audit_logger.log_signature_event", espia):
        await worker._audit_firma(
            schema="100_test", doc_id="d" * 8, user_id="u" * 8,
            session_id="s" * 8, official_number="IF-2026-00000001-TEST-INNO",
        )

    espia.assert_awaited_once()
    kwargs = espia.await_args.kwargs
    assert kwargs["signature_method"] == "electronic"
    assert kwargs["result"] == "ok"
    assert kwargs["official_number"] == "IF-2026-00000001-TEST-INNO"


@pytest.mark.asyncio
async def test_audit_firma_nunca_tumba_una_firma_ya_hecha():
    from workers.escri import EscriWorker

    worker = EscriWorker.__new__(EscriWorker)
    with patch(
        "services.documents.signing.audit_logger.log_signature_event",
        AsyncMock(side_effect=RuntimeError("la base se cayó")),
    ):
        await worker._audit_firma(
            schema="100_test", doc_id="d" * 8, user_id="u" * 8, session_id="s" * 8,
        )


@pytest.mark.parametrize("entrada,esperado_tipo", [
    ("2027-05-12T10:30:00Z", datetime),
    ("2027-05-12T10:30:00+00:00", datetime),
    ("2027-05-12 10:30:00", datetime),
    (None, type(None)),
    ("", type(None)),
    ("no soy una fecha", type(None)),
    (12345, type(None)),
])
def test_como_fecha_acepta_texto_y_descarta_basura(entrada, esperado_tipo):
    from services.documents.signing.audit_logger import _como_fecha
    assert isinstance(_como_fecha(entrada), esperado_tipo)


def test_como_fecha_deja_pasar_un_datetime_intacto():
    from services.documents.signing.audit_logger import _como_fecha
    ahora = datetime.now(timezone.utc)
    assert _como_fecha(ahora) is ahora


@pytest.mark.asyncio
async def test_certificado_con_fechas_de_texto_no_pierde_la_auditoria():
    from services.documents.signing import audit_logger

    ejecutado = AsyncMock()
    with patch("database.execute", ejecutado):
        await audit_logger.log_signature_event(
            schema_name="100_test",
            document_id="11111111-1111-1111-1111-111111111111",
            user_id="22222222-2222-2222-2222-222222222222",
            signature_method="digital_token",
            result="ok",
            cert_not_after="2027-05-12T10:30:00Z",
            tsa_time="2026-08-25T19:53:06Z",
        )

    ejecutado.assert_awaited_once()
    params = ejecutado.await_args.args[1:]
    fechas = [p for p in params if isinstance(p, datetime)]
    assert len(fechas) >= 2, (
        "cert_not_after y tsa_time tienen que llegar como datetime, no como str"
    )
    assert not any(p == "2027-05-12T10:30:00Z" for p in params), (
        "quedó un string crudo entre los parámetros: asyncpg lo rechazaría"
    )
