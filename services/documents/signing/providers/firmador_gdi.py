"""
Provider FirmadorGDI — firma digital con token físico PKCS#11.

Reemplaza AutoFirmaProvider. Protocolo @firma 1.9 idéntico, solo difieren:
- URI scheme: gdifirma:// (en vez de afirma://)
- keystore: PKCS11 (en vez de WINDOWS)
- provider_name: "firmador_gdi"
"""
import base64
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from services.cache import redis_client
from database import execute

_DEFAULT_STORAGE_URL = "https://<your-backend-app>.fly.dev/digital-signature/storage"
STORAGE_BASE_URL = os.getenv("AUTOFIRMA_STORAGE_URL", _DEFAULT_STORAGE_URL)
TTL_SECONDS = 240


class FirmadorGDIProvider:
    name = "firmador_gdi"

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
        # 1. IDs alfanuméricos (requisito protocolo @firma)
        file_id = "DATA" + secrets.token_hex(6).upper()
        session_id = "SES" + secrets.token_hex(6).upper()

        # 2. Datos del firmante para el sello visual (mismos que firma electrónica)
        from services.shared.signer_data import get_signer_data
        try:
            signer = await get_signer_data(user_id, schema_name=schema_name)
            seal = signer["seal"]
            department = signer["department_name"]
            municipality = signer["municipality_name"]
        except Exception:
            seal = ""
            department = ""
            municipality = ""

        # Firma digital: el nombre lo pone el CN del certificado del token (titular real),
        # NO el full_name del sistema. FirmadorGDI reemplaza $$SUBJECTCN$$ por el CN del cert.
        # seal/department/municipality sí salen del sistema.
        layer2_lines = ["$$SUBJECTCN$$"]
        if seal:
            layer2_lines.append(seal)
        if department:
            layer2_lines.append(department)
        if municipality:
            layer2_lines.append(municipality)
        layer2_text = "\\n".join(layer2_lines)

        # 3. Properties string (coordenadas calculadas por Notary)
        properties_str = (
            "signaturePage=last\n"
            f"signaturePositionOnPageLowerLeftX={int(sig_llx)}\n"
            f"signaturePositionOnPageLowerLeftY={int(sig_lly)}\n"
            f"signaturePositionOnPageUpperRightX={int(sig_urx)}\n"
            f"signaturePositionOnPageUpperRightY={int(sig_ury)}\n"
            "signReason=Firma digital GDI\n"
            "signatureProductionCity=Argentina\n"
            f"layer2Text={layer2_text}\n"
            "layer2Font=HELVETICA\n"
            "layer2FontSize=0"
        )
        # BASE64 ANTES de URL-encode (crítico: si se URL-encode primero, firma queda invisible)
        properties_b64 = base64.b64encode(properties_str.encode("utf-8")).decode("ascii")

        # 4. PDF en base64
        pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")

        # 5. URL-encode del storage_url para el XML
        storage_url_encoded = quote(STORAGE_BASE_URL, safe="")
        properties_b64_encoded = quote(properties_b64, safe="")

        # 6. XML envoltorio (sin espacios entre elementos — protocolo @firma es sensible)
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<op>"
            f'<e k="format" v="PAdES"/>'
            f'<e k="algorithm" v="SHA256withRSA"/>'
            f'<e k="stservlet" v="{storage_url_encoded}"/>'
            f'<e k="id" v="{session_id}"/>'
            f'<e k="keystore" v="PKCS11"/>'
            f'<e k="properties" v="{properties_b64_encoded}"/>'
            f'<e k="dat" v="{quote(pdf_b64, safe="")}"/>'
            "</op>"
        )

        # 7. Guardar XML en Redis con TTL
        if redis_client:
            redis_client.setex(
                f"firma:storage:{schema_name}:{file_id}",
                TTL_SECONDS,
                xml,
            )
            redis_client.setex(
                f"firma:storage:meta:{schema_name}:{session_id}",
                TTL_SECONDS,
                json.dumps({"file_id": file_id, "schema_name": schema_name}),
            )

        # 8. INSERT en digital_signature_sessions
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=TTL_SECONDS)
        await execute(
            """
            INSERT INTO public.digital_signature_sessions (
                session_id, file_id, schema_name, user_id, document_id,
                is_numerator, number, provider_name, status, expires_at,
                user_cuit, ip_address, user_agent
            ) VALUES ($1, $2, $3, $4::uuid, $5::uuid, $6, $7, $8, 'pending', $9, $10, $11::inet, $12)
            """,
            session_id,
            file_id,
            schema_name,
            user_id,
            document_id,
            is_numerator,
            number,
            self.name,
            expires_at,
            user_cuit,
            ip_address,
            user_agent,
            schema_name="public",
        )

        # 9. URI gdifirma:// (mismo protocolo @firma, distinto scheme y keystore)
        uri = (
            f"gdifirma://sign?ver=1_0"
            f"&fileid={file_id}"
            f"&rtservlet={storage_url_encoded}"
            f"&stservlet={storage_url_encoded}"
            f"&id={session_id}"
            f"&keystore=PKCS11"
        )

        return {
            "session_id": session_id,
            "file_id": file_id,
            "provider_name": self.name,
            "user_payload": uri,
            "expires_at": expires_at,
        }

    def poll_signing(self, *, session_id: str, schema_name: str):
        """Idéntico a AutoFirmaProvider — misma estructura de datos en Redis."""
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
        """Elimina claves Redis asociadas a la sesión."""
        if not redis_client:
            return
        keys_to_delete = [
            f"firma:storage:{schema_name}:{session_id}",
            f"firma:storage:meta:{schema_name}:{session_id}",
        ]
        if file_id:
            keys_to_delete.append(f"firma:storage:{schema_name}:{file_id}")
        redis_client.delete(*keys_to_delete)
