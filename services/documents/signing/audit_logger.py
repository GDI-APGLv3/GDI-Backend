from shared.logging import get_logger
import re
from datetime import datetime

log = get_logger(__name__)

_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)

def _como_fecha(valor):
    if valor is None or isinstance(valor, datetime):
        return valor
    if isinstance(valor, str):
        texto = valor.strip()
        if not texto:
            return None
        try:
            return datetime.fromisoformat(texto.replace("Z", "+00:00"))
        except ValueError:
            log.warning("audit_log.fecha_ilegible valor=%r — se guarda NULL", texto[:40])
            return None
    log.warning("audit_log.fecha_tipo_inesperado tipo=%s — se guarda NULL", type(valor).__name__)
    return None


_AUDIT_TABLE = "public.firma_audit_log"


async def log_signature_event(
    *,
    schema_name: str,
    document_id: str,
    user_id: str,
    signature_method: str,
    result: str,
    failure_reason: str | None = None,
    session_id: str | None = None,
    user_cuit: str | None = None,
    official_number: str | None = None,
    document_hash_pre: bytes | None = None,
    document_hash_post: bytes | None = None,
    cert_serial: str | None = None,
    cert_issuer_dn: str | None = None,
    cert_subject_dn: str | None = None,
    cert_subject_cuit: str | None = None,
    cert_not_after: datetime | None = None,
    cert_policy_oids: list[str] | None = None,
    signature_algorithm: str | None = None,
    signature_level: str | None = None,
    tsa_url: str | None = None,
    tsa_serial: str | None = None,
    tsa_time: datetime | None = None,
    time_skew_seconds: int | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    revocation_method: str | None = None,
    revocation_status: str | None = None,
    revocation_check_time: datetime | None = None,
    r2_object_key: str | None = None,
    actor_type: str = "user",
) -> None:
    try:
        from database import execute

        await execute(
            f"""
            INSERT INTO {_AUDIT_TABLE} (
                schema_name,
                session_id,
                signature_method,
                user_id,
                user_cuit,
                document_id,
                official_number,
                document_hash_pre,
                document_hash_post,
                cert_serial,
                cert_issuer_dn,
                cert_subject_dn,
                cert_subject_cuit,
                cert_not_after,
                cert_policy_oids,
                signature_algorithm,
                signature_level,
                tsa_url,
                tsa_serial,
                tsa_time,
                server_time,
                time_skew_seconds,
                ip_address,
                user_agent,
                revocation_method,
                revocation_status,
                revocation_check_time,
                result,
                failure_reason,
                r2_object_key,
                actor_type
            ) VALUES (
                $1,
                $2,
                $3,
                $4::uuid,
                $5,
                $6::uuid,
                $7,
                $8,
                $9,
                $10,
                $11,
                $12,
                $13,
                $14,
                $15,
                $16,
                $17,
                $18,
                $19,
                $20,
                NOW(),
                $21,
                $22::inet,
                $23,
                $24,
                $25,
                $26,
                $27,
                $28,
                $29,
                $30
            )
            """,
            schema_name,
            session_id,
            signature_method,
            user_id,
            user_cuit,
            str(document_id),
            official_number,
            document_hash_pre,
            document_hash_post,
            cert_serial,
            cert_issuer_dn,
            cert_subject_dn,
            cert_subject_cuit,
            _como_fecha(cert_not_after),
            cert_policy_oids,
            signature_algorithm,
            signature_level,
            tsa_url,
            tsa_serial,
            _como_fecha(tsa_time),
            time_skew_seconds,
            ip_address,
            user_agent,
            revocation_method,
            revocation_status,
            _como_fecha(revocation_check_time),
            result,
            failure_reason,
            r2_object_key,
            actor_type,
            schema_name="public",
        )

        log.debug(
            "audit_log.inserted",
            extra={
                "schema_name": schema_name,
                "document_id": str(document_id),
                "result": result,
                "method": signature_method,
            },
        )

    except Exception:
        log.exception(
            "audit_log.insert_failed",
            extra={
                "schema_name": schema_name,
                "document_id": str(document_id),
                "result": result,
            },
        )
