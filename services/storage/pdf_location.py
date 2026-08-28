import asyncio

from shared.logging import get_logger
from config.constants import TSA_DEFERRED_SEAL_ENABLED
from database import execute

_PERSIST_MAX_INTENTOS = 3
_PERSIST_BACKOFF_SEGUNDOS = 0.2

log = get_logger(__name__)


def effective_pdf_location(upload_result, requested: str) -> str:
    if isinstance(upload_result, dict):
        return upload_result.get("location") or requested
    return requested


def target_pdf_location() -> str:
    return "preoficial" if TSA_DEFERRED_SEAL_ENABLED else "oficial"


async def persist_pdf_location(
    document_id: str | None,
    effective_location: str,
    *,
    schema_name: str,
    official_number: str | None = None,
) -> None:
    if effective_location == "oficial":
        return
    if not document_id and not official_number:
        log.error(
            "GDI-270: persist_pdf_location sin document_id ni official_number "
            "(schema=%s, location=%s) — la fila queda con el default 'oficial' "
            "y el PDF está en otro bucket",
            schema_name, effective_location,
        )
        return
    referencia = str(document_id or official_number)[:12]
    ultimo_error: Exception | None = None

    for intento in range(1, _PERSIST_MAX_INTENTOS + 1):
        try:
            if document_id:
                await execute(
                    "UPDATE official_documents SET pdf_location = $2 WHERE id = $1",
                    document_id,
                    effective_location,
                    schema_name=schema_name,
                )
            else:
                await execute(
                    "UPDATE official_documents SET pdf_location = $2 WHERE official_number = $1",
                    official_number,
                    effective_location,
                    schema_name=schema_name,
                )
            if intento > 1:
                log.info(
                    "GDI-270: pdf_location=%s persistida en el intento %d para doc=%s",
                    effective_location, intento, referencia,
                )
            return
        except Exception as exc:  # noqa: BLE001 — ver docstring
            ultimo_error = exc
            log.warning(
                "GDI-270: intento %d/%d de persistir pdf_location=%s falló para "
                "doc=%s schema=%s: %s",
                intento, _PERSIST_MAX_INTENTOS, effective_location,
                referencia, schema_name, exc,
            )
            if intento < _PERSIST_MAX_INTENTOS:
                await asyncio.sleep(_PERSIST_BACKOFF_SEGUNDOS * intento)

    log.error(
        "GDI-270: no se pudo persistir pdf_location=%s para doc=%s schema=%s "
        "tras %d intentos: %s — las URLs firmadas de ese documento van a estar "
        "rotas hasta que el conciliador R2<->BD repare la fila",
        effective_location, referencia, schema_name, _PERSIST_MAX_INTENTOS, ultimo_error,
    )
    try:
        from shared.alerts import send_alert_mail
        await send_alert_mail(
            subject=f"[GDI GDI-270] pdf_location no persistida ({schema_name})",
            body=(
                f"El documento {referencia} del municipio {schema_name} quedó con su "
                f"PDF en el bucket '{effective_location}', pero no se pudo actualizar "
                f"official_documents.pdf_location tras {_PERSIST_MAX_INTENTOS} intentos.\n\n"
                f"Último error: {ultimo_error}\n\n"
                f"Consecuencia: la descarga por proxy funciona igual (tiene fallback), "
                f"pero las URLs firmadas de ese documento apuntan al bucket equivocado "
                f"y fallan al abrirse. El conciliador R2<->BD lo repara en su próxima "
                f"corrida (hallazgo 'pdf_location_desincronizada'); si es urgente:\n\n"
                f"  UPDATE {schema_name}.official_documents "
                f"SET pdf_location = '{effective_location}' WHERE id = '{document_id}';"
            ),
        )
    except Exception as alert_exc:  # noqa: BLE001
        log.error("GDI-270: además falló la alerta de pdf_location: %s", alert_exc)
