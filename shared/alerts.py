
import os
from typing import Optional

from shared.email import send_email
from shared.logging import get_logger

logger = get_logger(__name__)


ALERT_TO_EMAIL = os.getenv("ALERT_TO_EMAIL", "alerts@example.com")


async def send_alert_mail(
    subject: str,
    body: str,
    *,
    schema_name: Optional[str] = None,
) -> bool:
    full_body = f"{body}\n\nTenant: {schema_name}" if schema_name else body

    return await send_email(
        to=ALERT_TO_EMAIL,
        subject=subject,
        text=full_body,
    )
