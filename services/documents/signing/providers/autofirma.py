import base64
import os

from services.cache import redis_client

_DEFAULT_STORAGE_URL = "https://<your-backend-app>.fly.dev/digital-signature/storage"
STORAGE_BASE_URL = os.getenv("AUTOFIRMA_STORAGE_URL", _DEFAULT_STORAGE_URL)
from config.constants import DIGITAL_SIGNATURE_SESSION_TTL_SECONDS as TTL_SECONDS


class AutoFirmaProvider:
    name = "autofirma"

    async def start_signing(
        self,
        *,
        document_id: str,
        user_id: str,
        schema_name: str,
        pdf_bytes: bytes,
        is_numerator: bool,
        number: str | None,
        user_cuit: str | None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        sig_llx: float = 50.0,
        sig_lly: float = 30.0,
        sig_urx: float = 250.0,
        sig_ury: float = 110.0,
    ) -> dict:
        raise NotImplementedError(
            "AutoFirmaProvider.start_signing es código muerto — usar "
            "FirmadorGDIProvider (incluye reservation_id, requerido por el "
            "sweeper GDI-075). AutoFirma solo opera poll/cancel."
        )

    def poll_signing(self, *, session_id: str, schema_name: str):
        from . import (
            PollSigningPending, PollSigningSigned,
            PollSigningCancelled, PollSigningFailed,
        )

        if not redis_client:
            return PollSigningPending()

        raw = redis_client.get(f"firma:storage:{schema_name}:{session_id}")

        if raw is None or raw == "" or raw == b"":
            return PollSigningPending()

        if isinstance(raw, bytes):
            raw_str = raw.decode("utf-8", errors="replace")
        else:
            raw_str = raw

        if raw_str == "CANCEL":
            return PollSigningCancelled()

        try:
            if "|" in raw_str:
                parts = raw_str.split("|", 1)
                cert_der = base64.urlsafe_b64decode(parts[0].encode("ascii") + b"===")
                signed_pdf_bytes = base64.urlsafe_b64decode(parts[1].encode("ascii") + b"===")
            else:
                decoded = base64.urlsafe_b64decode(raw_str.encode("ascii") + b"===")
                pdf_start = decoded.find(b"%PDF-")
                if pdf_start < 0:
                    return PollSigningFailed(
                        error_code="NO_PDF_HEADER",
                        error_message="blob invalido: no se encontro %PDF-",
                    )
                cert_der = decoded[:pdf_start]
                signed_pdf_bytes = decoded[pdf_start:]
        except Exception as e:
            return PollSigningFailed(error_code="BASE64_DECODE_FAIL", error_message=str(e))

        if not signed_pdf_bytes.startswith(b"%PDF-"):
            return PollSigningFailed(
                error_code="NO_PDF_HEADER",
                error_message="blob invalido: no se encontro %PDF-",
            )

        return PollSigningSigned(
            signed_pdf_bytes=signed_pdf_bytes,
            cert_der=cert_der,
        )

    def cancel_signing(
        self, *, session_id: str, schema_name: str, file_id: str | None = None
    ) -> None:
        if not redis_client:
            return
        keys_to_delete = [
            f"firma:storage:{schema_name}:{session_id}",
            f"firma:storage:meta:{schema_name}:{session_id}",
        ]
        if file_id:
            keys_to_delete.append(f"firma:storage:{schema_name}:{file_id}")
        redis_client.delete(*keys_to_delete)
