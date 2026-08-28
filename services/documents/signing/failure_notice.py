from shared.logging import get_logger

from services.documents.signing.failure_reasons import (
    NO_AVISAR_AL_USUARIO, motivo_humano,
)

log = get_logger(__name__)


async def avisar_firma_fallida(
    *,
    session_id: str,
    schema_name: str,
    document_id: str,
    user_id: str | None,
    reason: str | None,
) -> bool:
    if reason in NO_AVISAR_AL_USUARIO:
        log.debug(
            "aviso_firma.omitido session=%s reason=%s — no es un fallo del usuario",
            session_id[:8], reason,
        )
        return False
    if not user_id:
        return False

    try:
        from database import fetch_one

        destinatario = await fetch_one(
            "SELECT email, full_name FROM users WHERE id = $1::uuid",
            user_id,
            schema_name=schema_name,
        )
        if not destinatario or not destinatario.get("email"):
            log.info(
                "aviso_firma.sin_email session=%s user=%s — queda solo la campanita",
                session_id[:8], user_id[:8],
            )
            return False

        from shared.email import send_email

        qué_pasó, qué_hacer = motivo_humano(reason)
        saludo = destinatario.get("full_name") or ""
        await send_email(
            destinatario["email"],
            "Tu documento no se pudo firmar",
            text=(
                f"Hola {saludo}," + "\n\n"
                f"{qué_pasó}\n\n"
                f"{qué_hacer}\n\n"
                f"Documento: {document_id}\n"
            ),
        )
        log.info(
            "aviso_firma.enviado session=%s user=%s reason=%s",
            session_id[:8], user_id[:8], reason,
        )
        return True
    except Exception as exc:  # noqa: BLE001 — ver docstring del módulo
        log.error(
            "aviso_firma.fallido session=%s: %s — el usuario se entera solo por "
            "la campanita", session_id[:8], exc,
        )
        return False
