"""
Sistema de alertas por mail para fallos criticos.

Usa la API de Resend directamente via httpx (sin agregar la libreria `resend`
a requirements.txt). Comparte las mismas variables de entorno que
GDI-BackOffice-Back: RESEND_API_KEY y FROM_EMAIL.

Soft-fail: si no hay API key configurada o el envio falla, se loguea pero
NUNCA lanza excepcion. La logica de negocio NO debe depender de que el mail
llegue.

Uso:
    from shared.alerts import send_alert_mail

    await send_alert_mail(
        subject="[GDI ALERTA] Firma fallida - IF-2026-00000045",
        body="Documento ABC fallo al firmar 2 veces.\\nError: Notary timeout",
        schema_name="100_test",
    )
"""

import os
from typing import Optional

import httpx

from shared.logging import get_logger

logger = get_logger(__name__)


# Mismas variables que GDI-BackOffice-Back para reutilizar la API key
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "GDI Latam <noreply@example.com>")
ALERT_TO_EMAIL = os.getenv("ALERT_TO_EMAIL", "alerts@example.com")

RESEND_API_URL = "https://api.resend.com/emails"
RESEND_TIMEOUT = 10.0  # segundos


async def send_alert_mail(
    subject: str,
    body: str,
    *,
    schema_name: Optional[str] = None,
) -> bool:
    """
    Envia un mail de alerta via Resend. Non-blocking.

    Si RESEND_API_KEY no esta configurada, loguea WARNING y retorna False.
    Si el envio falla por cualquier razon, loguea ERROR y retorna False.
    NUNCA lanza excepcion: la logica de negocio no debe depender de esto.

    Args:
        subject: Asunto del mail.
        body: Cuerpo del mail (texto plano).
        schema_name: Schema del tenant donde ocurrio el fallo. Se agrega
            al cuerpo si se pasa.

    Returns:
        True si Resend acepto el envio, False en cualquier otro caso.
    """
    if not RESEND_API_KEY:
        logger.warning(
            f"RESEND_API_KEY no configurada. Alerta no enviada: {subject}"
        )
        return False

    full_body = f"{body}\n\nTenant: {schema_name}" if schema_name else body

    payload = {
        "from": FROM_EMAIL,
        "to": [ALERT_TO_EMAIL],
        "subject": subject,
        "text": full_body,
    }

    try:
        async with httpx.AsyncClient(timeout=RESEND_TIMEOUT) as client:
            response = await client.post(
                RESEND_API_URL,
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

            if response.status_code in (200, 201):
                logger.info(f"Alerta enviada a {ALERT_TO_EMAIL}: {subject}")
                return True

            # Errores de autenticación (401/403) o cliente (4xx) indican
            # configuración rota (API key incorrecta o expirada). Usar
            # logger.critical para que sea visible en alertas de logs.
            if response.status_code in (401, 403):
                logger.critical(
                    f"Resend rechazó alerta por error de autenticación "
                    f"({response.status_code}) - RESEND_API_KEY inválida o expirada. "
                    f"Alerta perdida: '{subject}' | Response: {response.text[:200]}"
                )
                return False

            if 400 <= response.status_code < 500:
                logger.critical(
                    f"Resend rechazó alerta con error de cliente ({response.status_code}) - "
                    f"posible config rota (FROM_EMAIL, ALERT_TO_EMAIL). "
                    f"Alerta perdida: '{subject}' | Response: {response.text[:200]}"
                )
                return False

            logger.error(
                f"Resend rechazó la alerta '{subject}': "
                f"{response.status_code} {response.text[:200]}"
            )
            return False

    except httpx.TimeoutException:
        logger.error(f"Timeout enviando alerta a Resend: {subject}")
        return False
    except Exception as e:
        logger.error(f"Excepcion enviando alerta '{subject}': {e}")
        return False
