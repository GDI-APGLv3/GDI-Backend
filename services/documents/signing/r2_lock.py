"""
Lock distribuido para el proceso de firma — dos capas:

Capa 1 (atómica): Redis SETNX — previene la race condition TOCTOU.
  SET NX es atómico en Redis (single-threaded). Si dos requests llegan
  simultáneamente, solo uno adquiere el lock; el otro retorna False de inmediato.

Capa 2 (durable): R2 server-side copy + delete — garantiza que el PDF
  esté en un estado consistente en storage aunque Redis no esté disponible.

Si Redis no está disponible, la Capa 1 se saltea y opera solo con Capa 2
(comportamiento anterior, con riesgo de TOCTOU en ventana de <50ms).
"""
import logging
from services.r2_client import r2_copy, r2_delete, r2_put, r2_head, R2KeyNotFound

log = logging.getLogger(__name__)

_INPROCESS_PREFIX = "inprocess"
_REDIS_LOCK_TTL = 120  # segundos; TTL de seguridad por si el proceso muere sin liberar


def _redis_lock_key(schema_name: str, doc_id: str) -> str:
    return f"signing:lock:{schema_name}:{doc_id.replace('-', '')}"


def _acquire_redis_lock(schema_name: str, doc_id: str) -> bool:
    """SETNX atómico. True = lock adquirido. False = ya existe (otro proceso firmando)."""
    try:
        from services.cache import redis_client
        if redis_client is None:
            return True  # Redis no disponible — continúa con capa R2
        acquired = redis_client.set(
            _redis_lock_key(schema_name, doc_id), "1", nx=True, ex=_REDIS_LOCK_TTL
        )
        return bool(acquired)
    except Exception as e:
        log.error(
            f"DEGRADADO: Redis no disponible, lock deshabilitado. "
            f"Riesgo de concurrencia en firma. error={e}"
        )
        return True  # Fail-open: si Redis falla, la capa R2 actúa como fallback


def _release_redis_lock(schema_name: str, doc_id: str) -> None:
    """Libera el lock Redis. Soft-fail: nunca interrumpe el flujo principal."""
    try:
        from services.cache import redis_client
        if redis_client is None:
            return
        redis_client.delete(_redis_lock_key(schema_name, doc_id))
    except Exception as e:
        log.warning(f"r2_lock.redis_release_error: {e}")


def _tosign_key(doc_id: str) -> str:
    """Key para PDF libre en bucket tosign."""
    return doc_id.replace("-", "") + ".pdf"


def _inprocess_key(doc_id: str) -> str:
    """Key para PDF con lock activo en bucket tosign."""
    return f"{_INPROCESS_PREFIX}/{doc_id.replace('-', '')}.pdf"


async def acquire_signing_lock_R2(*, schema_name: str, doc_id: str) -> bool:
    """
    Adquiere el lock de firma en dos capas.

    Capa 1 — Redis SETNX (atómico): bloquea en <1ms, previene TOCTOU.
    Capa 2 — R2 copy+delete (durable): mueve PDF a inprocess/, consistencia en storage.

    Returns:
        True si el lock fue adquirido en ambas capas.
        False si otro proceso ya tiene el lock (Redis o R2).
    """
    src = _tosign_key(doc_id)
    dst = _inprocess_key(doc_id)

    # Capa 1: Redis SETNX atómico — evita la race condition TOCTOU entre HEAD y COPY
    if not _acquire_redis_lock(schema_name, doc_id):
        log.warning(
            "r2_lock.redis_already_locked",
            extra={"schema_name": schema_name, "doc_id": doc_id},
        )
        return False

    # Capa 2: R2 — verificar y mover el PDF a inprocess/
    try:
        await r2_head(schema_name=schema_name, key=dst)
        # inprocess/ ya existe → lock activo en R2 (ej: Redis expiró pero R2 no)
        log.warning(
            "r2_lock.r2_already_locked",
            extra={"schema_name": schema_name, "doc_id": doc_id, "dst": dst},
        )
        _release_redis_lock(schema_name, doc_id)  # liberar Redis ya que R2 bloqueó
        return False
    except R2KeyNotFound:
        pass  # inprocess/ no existe → continuar

    try:
        await r2_copy(schema_name=schema_name, src=src, dst=dst)
        await r2_delete(schema_name=schema_name, key=src)
        log.info(
            "r2_lock.acquired",
            extra={"schema_name": schema_name, "doc_id": doc_id, "src": src, "dst": dst},
        )
        return True
    except R2KeyNotFound:
        log.warning(
            "r2_lock.tosign_not_found",
            extra={"schema_name": schema_name, "doc_id": doc_id, "src": src},
        )
        _release_redis_lock(schema_name, doc_id)  # liberar Redis si R2 falló
        return False


async def release_signing_lock_R2_success(
    *,
    schema_name: str,
    doc_id: str,
    signed_pdf: bytes,
    is_numerator: bool,
    number: str | None,
) -> None:
    """
    Libera el lock después de una firma exitosa.

    Comportamiento según rol:
    - Numerador (last signer): mueve el PDF firmado a bucket oficial/{year}/{number}.pdf
    - Firmante común: sobrescribe tosign/{uuid}.pdf con el PDF firmado
      (queda disponible para el siguiente firmante).

    El PDF en tosign/inprocess/{uuid}.pdf se elimina en ambos casos.

    Args:
        schema_name: Schema del tenant (keyword-only).
        doc_id: UUID del documento.
        signed_pdf: Bytes del PDF firmado retornado por Notary.
        is_numerator: True si es el firmante final (numerador).
        number: Número oficial asignado (solo usado cuando is_numerator=True).
    """
    from datetime import datetime

    inprocess = _inprocess_key(doc_id)

    if is_numerator and number:
        # Flujo digital (AutoFirma): el PDF firmado llega de AutoFirma → poll.py.
        # El upload al bucket oficial lo hace _complete_numerator_digital_signing en poll.py.
        # Aquí solo liberamos Redis e inprocess/ (el PDF ya no está en tosign/).
        log.info(
            "r2_lock.release_success.numerator",
            extra={
                "schema_name": schema_name,
                "doc_id": doc_id,
                "official_number": number,
            },
        )
    else:
        # Firmante común: sobrescribir tosign/{uuid}.pdf con el PDF firmado.
        dest_key = _tosign_key(doc_id)
        await r2_put(schema_name=schema_name, key=dest_key, body=signed_pdf, bucket="tosign")
        log.info(
            "r2_lock.release_success.common",
            extra={"schema_name": schema_name, "doc_id": doc_id, "dest_key": dest_key},
        )

    # Liberar lock Redis (siempre, independiente del resultado R2)
    _release_redis_lock(schema_name, doc_id)

    # Eliminar inprocess/ (soft-fail: si ya no existe, no es error)
    try:
        await r2_delete(schema_name=schema_name, key=inprocess)
        log.info(
            "r2_lock.inprocess_cleaned",
            extra={"schema_name": schema_name, "doc_id": doc_id},
        )
    except R2KeyNotFound:
        log.warning(
            "r2_lock.release_success.inprocess_already_gone",
            extra={"schema_name": schema_name, "doc_id": doc_id},
        )
    except Exception as e:
        log.warning(
            "r2_lock.release_success.inprocess_delete_failed",
            extra={"schema_name": schema_name, "doc_id": doc_id, "error": str(e)},
        )


async def release_signing_lock_R2_fail(*, schema_name: str, doc_id: str) -> None:
    """
    Rollback del lock: restaura PDF de inprocess/ → tosign/ y libera Redis.

    Se llama cuando Notary falla o hay cualquier error después de adquirir el lock.
    """
    src = _inprocess_key(doc_id)
    dst = _tosign_key(doc_id)

    # Liberar lock Redis antes de tocar R2
    _release_redis_lock(schema_name, doc_id)

    try:
        await r2_copy(schema_name=schema_name, src=src, dst=dst)
        await r2_delete(schema_name=schema_name, key=src)
        log.info(
            "r2_lock.rollback_ok",
            extra={"schema_name": schema_name, "doc_id": doc_id},
        )
    except R2KeyNotFound:
        log.warning(
            "r2_lock.rollback.inprocess_not_found",
            extra={"schema_name": schema_name, "doc_id": doc_id, "src": src},
        )


async def reclaim_orphan_inprocess(*, schema_name: str, doc_id: str, session_id: str) -> None:
    """
    Reclama un PDF huérfano en inprocess/ restaurándolo a tosign/.

    Alias de release_signing_lock_R2_fail con logging de métrica adicional.
    Llamado por el cron de orphan_inprocess (jobs/orphan_inprocess.py).

    Args:
        schema_name: Schema del tenant (keyword-only).
        doc_id: UUID del documento con lock huérfano.
        session_id: UUID de la sesión de firma expirada (para correlación).
    """
    log.info(
        "orphan_reclaim.start",
        extra={"schema_name": schema_name, "doc_id": doc_id, "session_id": session_id},
    )
    await release_signing_lock_R2_fail(schema_name=schema_name, doc_id=doc_id)
    log.info(
        "orphan_reclaim.done",
        extra={"schema_name": schema_name, "doc_id": doc_id, "session_id": session_id},
    )
