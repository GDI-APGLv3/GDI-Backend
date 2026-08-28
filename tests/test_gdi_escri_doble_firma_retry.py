
import hashlib
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from workers.escri import EscriWorker


_UNSIGNED_PDF = b"%PDF-1.4 dummy unsigned"
_UNSIGNED_SHA = hashlib.sha256(_UNSIGNED_PDF).hexdigest()
_SIGNED_PDF = b"%PDF signed cryptographically"
_SIGNED_SHA = hashlib.sha256(_SIGNED_PDF).hexdigest()


def _make_common_job(schema: str = "100_test") -> dict:
    return {
        "session_id": str(uuid.uuid4()),
        "schema_name": schema,
        "document_id": str(uuid.uuid4()),
        "user_id": str(uuid.uuid4()),
        "job_type": "sign_common",
        "payload": {},
    }


def _signer_data() -> dict:
    return {
        "full_name": "Carlos Pérez",
        "seal": "Sello Municipal",
        "department_name": "Secretaría General",
        "municipality_name": "Municipalidad del Futuro",
    }


class _FetchOneRouter:
    def __init__(self, *, signed_at=None, payload=None, retry_count=1):
        self.signed_at = signed_at
        self.payload = payload
        self.retry_count = retry_count
        self.calls = []

    async def __call__(self, sql, *args, **kwargs):
        self.calls.append(sql)
        s = sql.lower()
        if "from document_signers" in s and "signed_at" in s:
            return {"signed_at": self.signed_at}
        if "from public.signing_sessions" in s and "payload" in s:
            return {"payload": self.payload}
        if "update public.signing_sessions" in s and "returning session_id" in s:
            return {"session_id": args[0] if args else "fence_ok"}
        if "update_retry_count" in s and "returning" in s:
            return {"n": self.retry_count}
        return None


def _r2_get_router(*, inprocess_bytes=_UNSIGNED_PDF, tosign_bytes=None):
    from services.r2_client import R2KeyNotFound

    async def _get(*args, **kwargs):
        key = kwargs.get("key", "")
        if key.startswith("inprocess/"):
            if inprocess_bytes is None:
                raise R2KeyNotFound(f"not found: {key}")
            return inprocess_bytes
        if tosign_bytes is None:
            raise R2KeyNotFound(f"not found: {key}")
        return tosign_bytes

    return AsyncMock(side_effect=_get)


def _patched(
    worker: EscriWorker,
    *,
    notary_mock: AsyncMock,
    execute_mock: AsyncMock,
    fetch_one_router: _FetchOneRouter,
    release_success_mock: AsyncMock,
    release_fail_mock: AsyncMock,
    r2_get_mock: AsyncMock | None = None,
    mark_signed_mock: AsyncMock | None = None,
    mark_failed_mock: AsyncMock | None = None,
):
    if r2_get_mock is None:
        r2_get_mock = AsyncMock(return_value=_UNSIGNED_PDF)
    if mark_signed_mock is None:
        mark_signed_mock = AsyncMock(return_value=None)
    if mark_failed_mock is None:
        mark_failed_mock = AsyncMock(return_value=None)
    return [
        patch("services.shared.notary_api.call_notary_sign_pdf", notary_mock),
        patch("services.shared.signer_data.get_signer_data",
              AsyncMock(return_value=_signer_data())),
        patch("workers.escri.check_breaker_before_call",
              AsyncMock(return_value=None)),
        patch("workers.escri.fetch_one", fetch_one_router),
        patch("workers.escri.execute", execute_mock),
        patch("services.r2_client.r2_get_object", r2_get_mock),
        patch("services.documents.signing.r2_lock.release_signing_lock_R2_success",
              release_success_mock),
        patch("services.documents.signing.r2_lock.release_signing_lock_R2_fail",
              release_fail_mock),
        patch("services.documents.signing.audit_logger.log_signature_event",
              AsyncMock(return_value=None)),
        patch.object(worker, "_spawn_heartbeat",
                     return_value=(MagicMock(), MagicMock(is_set=lambda: False))),
        patch.object(worker, "_stop_heartbeat", new=AsyncMock(return_value=None)),
        patch.object(worker, "_mark_session_signed_common", new=mark_signed_mock),
        patch.object(worker, "_mark_session_failed", new=mark_failed_mock),
    ]


def _enter(patches):
    entered = [p.__enter__() for p in patches]
    return entered


def _exit(patches):
    for p in reversed(patches):
        p.__exit__(None, None, None)


@pytest.mark.asyncio
async def test_update_transitorio_no_llama_a_notary_de_nuevo():
    worker = EscriWorker()
    notary = AsyncMock(return_value=_SIGNED_PDF)

    update_attempts = {"n": 0}

    async def execute_side_effect(sql, *args, **kwargs):
        s = sql.lower().strip()
        if "update document_signers" in s and "set status = 'signed'" in s:
            update_attempts["n"] += 1
            if update_attempts["n"] == 1:
                raise asyncpg.PostgresConnectionError("blip transitorio")
            return "UPDATE 1"
        return "UPDATE 1"

    execute_mock = AsyncMock(side_effect=execute_side_effect)
    router = _FetchOneRouter(signed_at=None, payload=None)
    r2_success = AsyncMock(return_value=None)
    r2_fail = AsyncMock(return_value=None)

    patches = _patched(
        worker,
        notary_mock=notary, execute_mock=execute_mock,
        fetch_one_router=router,
        release_success_mock=r2_success, release_fail_mock=r2_fail,
    )
    _enter(patches)
    try:
        await worker._process_common_job(_make_common_job())
    finally:
        _exit(patches)

    assert notary.await_count == 1
    assert r2_success.await_count == 1
    assert r2_fail.await_count == 0


@pytest.mark.asyncio
async def test_reentrada_con_signed_uploaded_no_llama_a_notary():
    worker = EscriWorker()
    notary = AsyncMock(return_value=_SIGNED_PDF)
    execute_mock = AsyncMock(return_value="UPDATE 1")
    router = _FetchOneRouter(
        signed_at=None,
        payload={"signed_uploaded": True},
    )
    r2_success = AsyncMock(return_value=None)
    r2_fail = AsyncMock(return_value=None)

    patches = _patched(
        worker,
        notary_mock=notary, execute_mock=execute_mock,
        fetch_one_router=router,
        release_success_mock=r2_success, release_fail_mock=r2_fail,
    )
    _enter(patches)
    try:
        await worker._process_common_job(_make_common_job())
    finally:
        _exit(patches)

    assert notary.await_count == 0
    assert r2_success.await_count == 0
    assert r2_fail.await_count == 0


@pytest.mark.asyncio
async def test_reentrada_con_signed_at_no_llama_a_notary():
    worker = EscriWorker()
    notary = AsyncMock(return_value=_SIGNED_PDF)
    execute_mock = AsyncMock(return_value="UPDATE 1")
    router = _FetchOneRouter(signed_at="2026-08-19T12:00:00Z", payload=None)
    r2_success = AsyncMock(return_value=None)
    r2_fail = AsyncMock(return_value=None)

    patches = _patched(
        worker,
        notary_mock=notary, execute_mock=execute_mock,
        fetch_one_router=router,
        release_success_mock=r2_success, release_fail_mock=r2_fail,
    )
    _enter(patches)
    try:
        await worker._process_common_job(_make_common_job())
    finally:
        _exit(patches)

    assert notary.await_count == 0
    assert r2_success.await_count == 0
    assert r2_fail.await_count == 0


@pytest.mark.asyncio
async def test_update_cero_filas_reencola_en_vez_de_failed():
    worker = EscriWorker()
    notary = AsyncMock(return_value=_SIGNED_PDF)

    async def execute_side_effect(sql, *args, **kwargs):
        s = sql.lower().strip()
        if "update document_signers" in s and "set status = 'signed'" in s:
            return "UPDATE 0"
        return "UPDATE 1"

    execute_mock = AsyncMock(side_effect=execute_side_effect)
    router = _FetchOneRouter(signed_at=None, payload=None, retry_count=1)
    r2_success = AsyncMock(return_value=None)
    r2_fail = AsyncMock(return_value=None)
    mark_failed = AsyncMock(return_value=None)

    patches = _patched(
        worker,
        notary_mock=notary, execute_mock=execute_mock,
        fetch_one_router=router,
        release_success_mock=r2_success, release_fail_mock=r2_fail,
        mark_failed_mock=mark_failed,
    )
    _enter(patches)
    try:
        await worker._process_common_job(_make_common_job())
    finally:
        _exit(patches)

    assert notary.await_count == 1
    assert r2_fail.await_count == 0
    assert mark_failed.await_count == 0
    reencole_calls = [
        c for c in execute_mock.await_args_list
        if "status       = 'pending'" in (c.args[0] if c.args else "")
    ]
    assert len(reencole_calls) == 1


@pytest.mark.asyncio
async def test_reentrada_sin_marker_hash_mismatch_va_a_rama_b():
    worker = EscriWorker()
    notary = AsyncMock(return_value=_SIGNED_PDF)
    execute_mock = AsyncMock(return_value="UPDATE 1")
    router = _FetchOneRouter(
        signed_at=None,
        payload={"unsigned_sha256": _UNSIGNED_SHA},
    )
    r2_success = AsyncMock(return_value=None)
    r2_fail = AsyncMock(return_value=None)
    mark_signed = AsyncMock(return_value=None)
    mark_failed = AsyncMock(return_value=None)
    r2_get = _r2_get_router(inprocess_bytes=None, tosign_bytes=_SIGNED_PDF)

    patches = _patched(
        worker,
        notary_mock=notary, execute_mock=execute_mock,
        fetch_one_router=router,
        release_success_mock=r2_success, release_fail_mock=r2_fail,
        r2_get_mock=r2_get,
        mark_signed_mock=mark_signed, mark_failed_mock=mark_failed,
    )
    _enter(patches)
    try:
        await worker._process_common_job(_make_common_job())
    finally:
        _exit(patches)

    assert notary.await_count == 0
    assert r2_success.await_count == 0
    assert r2_fail.await_count == 0
    assert mark_failed.await_count == 0
    assert mark_signed.await_count == 1


@pytest.mark.asyncio
async def test_reentrada_sin_marker_hash_igual_falla_limpio():
    worker = EscriWorker()
    notary = AsyncMock(return_value=_SIGNED_PDF)
    execute_mock = AsyncMock(return_value="UPDATE 1")
    router = _FetchOneRouter(
        signed_at=None,
        payload={"unsigned_sha256": _UNSIGNED_SHA},
    )
    r2_success = AsyncMock(return_value=None)
    r2_fail = AsyncMock(return_value=None)
    mark_signed = AsyncMock(return_value=None)
    mark_failed = AsyncMock(return_value=None)
    r2_get = _r2_get_router(inprocess_bytes=None, tosign_bytes=_UNSIGNED_PDF)

    patches = _patched(
        worker,
        notary_mock=notary, execute_mock=execute_mock,
        fetch_one_router=router,
        release_success_mock=r2_success, release_fail_mock=r2_fail,
        r2_get_mock=r2_get,
        mark_signed_mock=mark_signed, mark_failed_mock=mark_failed,
    )
    _enter(patches)
    try:
        await worker._process_common_job(_make_common_job())
    finally:
        _exit(patches)

    assert notary.await_count == 0
    assert r2_success.await_count == 0
    assert mark_signed.await_count == 0
    assert mark_failed.await_count == 1


@pytest.mark.asyncio
async def test_post_upload_retry_count_maximo_marca_failed():
    worker = EscriWorker()
    notary = AsyncMock(return_value=_SIGNED_PDF)

    async def execute_side_effect(sql, *args, **kwargs):
        s = sql.lower().strip()
        if "update document_signers" in s and "set status = 'signed'" in s:
            return "UPDATE 0"
        return "UPDATE 1"

    execute_mock = AsyncMock(side_effect=execute_side_effect)
    router = _FetchOneRouter(signed_at=None, payload=None, retry_count=3)
    r2_success = AsyncMock(return_value=None)
    r2_fail = AsyncMock(return_value=None)
    mark_failed = AsyncMock(return_value=None)

    patches = _patched(
        worker,
        notary_mock=notary, execute_mock=execute_mock,
        fetch_one_router=router,
        release_success_mock=r2_success, release_fail_mock=r2_fail,
        mark_failed_mock=mark_failed,
    )
    _enter(patches)
    try:
        await worker._process_common_job(_make_common_job())
    finally:
        _exit(patches)

    assert notary.await_count == 1
    assert r2_fail.await_count == 0
    assert mark_failed.await_count == 1
