"""
HMAC inter-service para autenticar llamadas internas al microservicio Notary.

Header resultante: X-Internal-Sign: t=<unix_ts>,v1=<HMAC_SHA256_base64>

Payload firmado: "{ts}|{METHOD}|{path}|{sha256(body_bytes).hexdigest()}"

El secreto se configura via variable de entorno NOTARY_INTERNAL_HMAC_SECRET.
Si no está configurado (entornos legacy), la función devuelve cadena vacía
para no romper llamadas existentes.
"""
import base64
import hashlib
import hmac
import logging
import os
import time

log = logging.getLogger(__name__)

# Secreto compartido con el microservicio Notary.
# Vacío = feature desactivada (backward compatible con Notary sin validación HMAC).
_SECRET_STR = os.environ.get("NOTARY_INTERNAL_HMAC_SECRET", "")
_SECRET = _SECRET_STR.encode("utf-8") if _SECRET_STR else b""

# Advertir en producción (Fly.io) si el secreto no está configurado.
# Las llamadas a Notary irán sin firma HMAC, lo que es un riesgo de seguridad.
if not _SECRET and os.getenv("FLY_APP_NAME"):
    log.error(
        "NOTARY_INTERNAL_HMAC_SECRET no configurado en Fly.io. "
        "Las llamadas a Notary irán SIN firma HMAC. Setear con: "
        "flyctl secrets set NOTARY_INTERNAL_HMAC_SECRET=<valor> -a <app>"
    )


def build_internal_hmac_header(*, method: str, path: str, body_bytes: bytes) -> str:
    """
    Construye el valor del header X-Internal-Sign para autenticación inter-servicio.

    Formato: 't=<unix_timestamp>,v1=<HMAC_SHA256_base64>'

    El receptor debe:
    1. Parsear t= y rechazar si abs(now - t) > 60 segundos (replay protection).
    2. Reconstruir el payload y verificar v1= con HMAC-SHA256.

    Args:
        method: Método HTTP en mayúsculas (ej: 'POST').
        path: Path del endpoint (ej: '/sign-pdf').
        body_bytes: Cuerpo de la request (bytes). Puede ser b'' para GET.

    Returns:
        Valor para header X-Internal-Sign, o cadena vacía si el secreto
        no está configurado (para no romper entornos sin HMAC en Notary).
    """
    if not _SECRET:
        log.debug("NOTARY_INTERNAL_HMAC_SECRET no configurado, omitiendo X-Internal-Sign")
        return ""

    ts = int(time.time())
    body_hash = hashlib.sha256(body_bytes).hexdigest()
    payload = f"{ts}|{method}|{path}|{body_hash}".encode("utf-8")
    digest = hmac.new(_SECRET, payload, hashlib.sha256).digest()
    sig_b64 = base64.b64encode(digest).decode("ascii")
    header_value = f"t={ts},v1={sig_b64}"
    log.debug("X-Internal-Sign generado: t=%d, path=%s", ts, path)
    return header_value
