"""
INSERT-only audit trail para firmas (Ley 25.506 - Firma Digital Argentina).

La tabla public.firma_audit_log tiene REVOKE UPDATE/DELETE: los registros
no pueden modificarse una vez insertados.

Usa asyncpg (patrón del proyecto) y nunca lanza excepciones
hacia el caller: loggea el error pero no interrumpe el flujo de firma.

Columnas opcionales (cert_*, tsa_*, revocation_*) se populan en Fase 2
cuando se integra firma digital con token/cloud.
"""
import logging
import re
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
    re.IGNORECASE,
)

def _uuid_or_none(val: str | None) -> str | None:
    """Retorna val si es UUID válido, None si no (evita error ::uuid cast con session_ids alfanuméricos de AutoFirma)."""
    if val and _UUID_RE.match(val):
        return val
    return None

# Nombre de la tabla en el schema public (compartida entre todos los tenants)
_AUDIT_TABLE = "public.firma_audit_log"


async def log_signature_event(
    *,
    schema_name: str,
    document_id: str,
    user_id: str,
    signature_method: str,           # 'electronic', 'digital_token', 'digital_cloud'
    result: str,                      # 'ok', 'fail', 'pending'
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
) -> None:
    """
    Inserta un registro en public.firma_audit_log.

    Async (asyncpg). Nunca lanza excepción hacia el caller:
    si falla el INSERT, loggea el error y continúa.

    Args:
        schema_name: Schema del tenant (keyword-only). Guardado en la fila para correlación.
        document_id: UUID del documento firmado.
        user_id: UUID del usuario que firmó.
        signature_method: 'electronic' (Fase 1) | 'digital_token' | 'digital_cloud' (Fase 2+).
        result: 'ok' | 'fail' | 'pending'.
        failure_reason: Mensaje de error si result='fail'.
        session_id: UUID de la sesión de firma (Fase 2, None en Fase 1).
        user_cuit: CUIT del firmante (Fase 2, None en Fase 1).
        official_number: Número oficial asignado (solo numerador exitoso).
        document_hash_pre: SHA-256 del PDF antes de firmar (Fase 2).
        document_hash_post: SHA-256 del PDF firmado (Fase 2).
        cert_serial: Número de serie del certificado (Fase 2).
        cert_issuer_dn: DN del emisor del certificado (Fase 2).
        cert_subject_dn: DN del sujeto del certificado (Fase 2).
        cert_subject_cuit: CUIT en el certificado (Fase 2).
        cert_not_after: Fecha de vencimiento del certificado (Fase 2).
        cert_policy_oids: Lista de OIDs de política del certificado (Fase 2).
        signature_algorithm: Algoritmo de firma (ej: 'SHA256withRSA') (Fase 2).
        signature_level: Nivel PAdES (ej: 'B-B', 'B-T') (Fase 2).
        tsa_url: URL del servidor de timestamp (Fase 2).
        tsa_serial: Número de serie del timestamp (Fase 2).
        tsa_time: Timestamp del TSA (Fase 2).
        time_skew_seconds: Diferencia entre reloj del servidor y TSA (Fase 2).
        ip_address: IP del cliente (formato inet de PostgreSQL).
        user_agent: User-Agent del cliente.
        revocation_method: 'crl' | 'ocsp' (Fase 2).
        revocation_status: 'good' | 'revoked' | 'unknown' (Fase 2).
        revocation_check_time: Momento de la verificación de revocación (Fase 2).
        r2_object_key: Key del objeto R2 resultante de la firma.
    """
    try:
        from database import execute

        # La tabla firma_audit_log vive en public (compartida entre tenants).
        # Usamos 'public' como schema_name para el SET search_path,
        # y guardamos el schema_name del tenant en la columna correspondiente.
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
                r2_object_key
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
                $29
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
            cert_not_after,
            cert_policy_oids,
            signature_algorithm,
            signature_level,
            tsa_url,
            tsa_serial,
            tsa_time,
            time_skew_seconds,
            ip_address,
            user_agent,
            revocation_method,
            revocation_status,
            revocation_check_time,
            result,
            failure_reason,
            r2_object_key,
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
        # NUNCA interrumpir el flujo de firma por un fallo del audit log.
        log.exception(
            "audit_log.insert_failed",
            extra={
                "schema_name": schema_name,
                "document_id": str(document_id),
                "result": result,
            },
        )
