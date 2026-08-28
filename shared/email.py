
import os
from email.message import EmailMessage
from typing import List, Optional, Union

import httpx

from shared.logging import get_logger

logger = get_logger(__name__)


RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_API_URL = "https://api.resend.com/emails"
RESEND_TIMEOUT = 10.0

FROM_EMAIL = os.getenv("FROM_EMAIL", "GDI Latam <noreply@example.com>")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM = os.getenv("SMTP_FROM", "") or FROM_EMAIL
SMTP_TLS = os.getenv("SMTP_TLS", "true").lower() in ("1", "true", "yes")
SMTP_SSL = os.getenv("SMTP_SSL", "false").lower() in ("1", "true", "yes")
SMTP_TIMEOUT = 15.0


def _normalize_recipients(to: Union[str, List[str]]) -> List[str]:
    if isinstance(to, str):
        recipients = [to]
    else:
        recipients = list(to)
    return [r.strip() for r in recipients if r and r.strip()]


async def send_email(
    to: Union[str, List[str]],
    subject: str,
    *,
    text: Optional[str] = None,
    html: Optional[str] = None,
    from_email: Optional[str] = None,
) -> bool:
    recipients = _normalize_recipients(to)
    if not recipients:
        logger.warning(f"send_email sin destinatarios validos. Asunto: {subject}")
        return False

    if not text and not html:
        logger.warning(f"send_email sin cuerpo (text/html). Asunto: {subject}")
        return False

    if RESEND_API_KEY:
        return await _send_via_resend(
            recipients, subject, text=text, html=html,
            from_email=from_email or FROM_EMAIL,
        )

    if SMTP_HOST:
        return await _send_via_smtp(
            recipients, subject, text=text, html=html,
            from_email=from_email or SMTP_FROM,
        )

    logger.warning(
        f"Email no enviado (sin RESEND_API_KEY ni SMTP_HOST). Asunto: {subject}"
    )
    return False


async def _send_via_resend(
    recipients: List[str],
    subject: str,
    *,
    text: Optional[str],
    html: Optional[str],
    from_email: str,
) -> bool:
    payload = {
        "from": from_email,
        "to": recipients,
        "subject": subject,
    }
    if text:
        payload["text"] = text
    if html:
        payload["html"] = html

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
            logger.info(f"Email enviado via Resend a {recipients}: {subject}")
            return True

        if response.status_code in (401, 403):
            logger.critical(
                f"Resend rechazo email por autenticacion ({response.status_code}) "
                f"- RESEND_API_KEY invalida o expirada. Email perdido: '{subject}' "
                f"| {response.text[:200]}"
            )
            return False

        logger.error(
            f"Resend rechazo email '{subject}': "
            f"{response.status_code} {response.text[:200]}"
        )
        return False

    except httpx.TimeoutException:
        logger.error(f"Timeout enviando email via Resend: {subject}")
        return False
    except Exception as e:
        logger.error(f"Excepcion enviando email via Resend '{subject}': {e}")
        return False


async def _send_via_smtp(
    recipients: List[str],
    subject: str,
    *,
    text: Optional[str],
    html: Optional[str],
    from_email: str,
) -> bool:
    try:
        import aiosmtplib
    except ImportError:
        logger.error(
            "SMTP_HOST configurado pero aiosmtplib no esta instalado. "
            f"Email no enviado: {subject}"
        )
        return False

    message = EmailMessage()
    message["From"] = from_email
    message["To"] = ", ".join(recipients)
    message["Subject"] = subject

    if text:
        message.set_content(text)
        if html:
            message.add_alternative(html, subtype="html")
    else:
        message.set_content(html, subtype="html")

    try:
        await aiosmtplib.send(
            message,
            hostname=SMTP_HOST,
            port=SMTP_PORT,
            username=SMTP_USER or None,
            password=SMTP_PASSWORD or None,
            use_tls=SMTP_SSL,
            start_tls=SMTP_TLS and not SMTP_SSL,
            timeout=SMTP_TIMEOUT,
        )
        logger.info(f"Email enviado via SMTP a {recipients}: {subject}")
        return True
    except Exception as e:
        logger.error(f"Excepcion enviando email via SMTP '{subject}': {e}")
        return False
