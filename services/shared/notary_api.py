
import hashlib
import os
import httpx
from shared.logging import get_logger
from shared.exceptions import NotaryTimeoutError, NotaryUnavailableError, NotaryBusinessError


def _notary_headers(path: str, body: bytes) -> dict:
    headers = {"x-api-key": NOTARY_API_KEY}
    try:
        from services.notary_internal_hmac import build_internal_hmac_header
        hmac_value = build_internal_hmac_header(method="POST", path=path, body=body)
        if hmac_value:
            headers["X-Internal-Sign"] = hmac_value
    except Exception as _hmac_err:
        logger.warning(f"No se pudo generar X-Internal-Sign para {path} (soft-fail): {_hmac_err}")
    return headers


def _annotate_notary_error(err, *, response=None, exc=None, attempt_label: str = ""):
    err.status_code = response.status_code if response is not None else None
    err.upstream_machine = response.headers.get("fly-machine-id") if response is not None else None
    err.exc_kind = type(exc).__name__ if exc is not None else None
    logger.warning(
        "notary.fail status=%s exc=%s machine=%s label=%s",
        err.status_code, err.exc_kind, err.upstream_machine, attempt_label,
    )
    return err
from services.shared.pdf_utils import add_blank_page_to_pdf
from services.shared.micro_retry import post_micro_with_coldstart_retry

logger = get_logger(__name__)


NOTARY_URL = os.getenv('NOTARY_URL')
if not NOTARY_URL:
    raise RuntimeError("NOTARY_URL no configurado en variables de entorno")

NOTARY_API_KEY = os.getenv('NOTARY_API_KEY')
if not NOTARY_API_KEY:
    raise RuntimeError("NOTARY_API_KEY no configurado en variables de entorno")

NOTARY_TIMEOUT = 20.0


async def call_notary_sign_pdf(
    pdf_bytes: bytes,
    signer_name: str,
    signer_seal: str,
    signer_department: str,
    signer_municipality: str,
    official_number: str,
    city: str = "LATAM",
    stamp_position: str = "",
    *,
    tenant_id: str = None,
    schema_name: str = None,
    expected_sha256: str | None = None,
    defer_timestamp: bool = False,
) -> bytes:
    from services.shared.notary_breaker import (
        check_breaker_before_call, record_notary_failure, record_notary_success,
    )
    await check_breaker_before_call()

    logger.info("Preparando llamada a Notary")
    logger.info(f"URL: {NOTARY_URL}/sign-pdf")
    logger.info(f"Firmante: {signer_name}")
    logger.info(f"Sello: {signer_seal}")
    logger.info(f"Departamento: {signer_department}")
    logger.info(f"Municipalidad: {signer_municipality}")
    logger.info(f"Número oficial: {official_number}")
    logger.info(f"Ciudad: {city}")
    logger.info(f"stamp_position: {stamp_position or '(default)'}")
    logger.info(f"tenant_id: {tenant_id or '(none)'}")
    logger.info(f"defer_timestamp: {defer_timestamp}")
    logger.info(f"   Tamaño PDF: {len(pdf_bytes)} bytes ({len(pdf_bytes)/1024:.2f} KB)")

    _pdf_sha256 = expected_sha256 if expected_sha256 else hashlib.sha256(pdf_bytes).hexdigest()
    logger.info(f"[SU-009] SHA-256 para Notary: {_pdf_sha256}")

    cert_bytes = None
    cert_password = None

    if tenant_id and schema_name:
        try:
            from services.shared.certificate_resolver import resolve_certificate
            cert_bytes, cert_password = await resolve_certificate(
                tenant_id, schema_name=schema_name
            )
            logger.info(f"Certificado resuelto de R2 para {tenant_id} ({len(cert_bytes)} bytes)")
        except Exception as e:
            logger.warning(f"No se pudo resolver certificado de R2 para {tenant_id}: {e}")
            logger.info("Continuando con tenant_id (Notary usará certificado local)")

    files = {
        "pdf_file": ("document.pdf", pdf_bytes, "application/pdf"),
        "name": (None, signer_name),
        "seal": (None, signer_seal),
        "department": (None, signer_department),
        "entity": (None, signer_municipality),
        "document_number": (None, official_number),
        "city": (None, city),
        "expected_sha256": (None, _pdf_sha256)
    }

    if stamp_position:
        files["stamp_position"] = (None, stamp_position)

    if tenant_id:
        files["tenant_id"] = (None, tenant_id)

    if cert_bytes and cert_password:
        files["cert_file"] = ("cert.p12", cert_bytes, "application/x-pkcs12")
        files["cert_password"] = (None, cert_password)

    if defer_timestamp:
        files["defer_timestamp"] = (None, "true")

    headers = _notary_headers("/sign-pdf", pdf_bytes)

    try:
        logger.info("Intento lógico 1/2 (con retry cold-start interno): Enviando PDF original a Notary...")

        async with httpx.AsyncClient(timeout=NOTARY_TIMEOUT) as client:
            response = await post_micro_with_coldstart_retry(
                client,
                f"{NOTARY_URL}/sign-pdf",
                files=files,
                headers=headers,
                max_attempts=3,
                backoff=(2, 4),
                log_label="Notary sign-pdf (intento lógico 1)",
            )

        if response.status_code == 200:
            signed_pdf = response.content
            signature_type = response.headers.get("X-Signature-Type", "unknown")
            logger.info("PDF firmado exitosamente en intento 1")
            logger.info(f"Tipo de firma: {signature_type}")
            logger.info(f"Tamaño firmado: {len(signed_pdf)} bytes ({len(signed_pdf)/1024:.2f} KB)")
            await record_notary_success()
            return signed_pdf

        if response.status_code == 400:
            response_text = response.text.upper()
            response_json = None

            try:
                response_json = response.json()
            except Exception:
                pass

            is_fullpage = (
                "FULLPAGE" in response_text or
                (response_json and "FULLPAGE" in str(response_json).upper())
            )

            if is_fullpage:
                logger.warning("Notary respondió FULLPAGE (PDF sin espacio para firma)")
                logger.info("Agregando página de firma con marcador 'end-text' y reintentando...")

                try:
                    augmented_pdf = add_blank_page_to_pdf(pdf_bytes)
                except Exception as pdf_error:
                    logger.error(f"Error agregando página de firma: {pdf_error}")
                    raise Exception(f"Error manipulando PDF para FULLPAGE: {str(pdf_error)}")

                logger.info("Intento lógico 2/2 (con retry cold-start interno): Enviando PDF aumentado a Notary...")

                _augmented_sha256 = hashlib.sha256(augmented_pdf).hexdigest()
                logger.info(f"[SU-009] SHA-256 PDF aumentado (FULLPAGE retry): {_augmented_sha256}")

                files_retry = {
                    "pdf_file": ("document.pdf", augmented_pdf, "application/pdf"),
                    "name": (None, signer_name),
                    "seal": (None, signer_seal),
                    "department": (None, signer_department),
                    "entity": (None, signer_municipality),
                    "document_number": (None, official_number),
                    "city": (None, city),
                    "expected_sha256": (None, _augmented_sha256)
                }

                if stamp_position:
                    files_retry["stamp_position"] = (None, stamp_position)

                if tenant_id:
                    files_retry["tenant_id"] = (None, tenant_id)

                if cert_bytes and cert_password:
                    files_retry["cert_file"] = ("cert.p12", cert_bytes, "application/x-pkcs12")
                    files_retry["cert_password"] = (None, cert_password)

                if defer_timestamp:
                    files_retry["defer_timestamp"] = (None, "true")

                async with httpx.AsyncClient(timeout=NOTARY_TIMEOUT) as client:
                    response_retry = await post_micro_with_coldstart_retry(
                        client,
                        f"{NOTARY_URL}/sign-pdf",
                        files=files_retry,
                        headers=_notary_headers("/sign-pdf", augmented_pdf),
                        max_attempts=3,
                        backoff=(2, 4),
                        log_label="Notary sign-pdf (intento lógico 2, FULLPAGE retry)",
                    )

                if response_retry.status_code == 200:
                    signed_pdf = response_retry.content
                    signature_type = response_retry.headers.get("X-Signature-Type", "unknown")
                    logger.info("PDF firmado exitosamente en intento 2 (después de FULLPAGE)")
                    logger.info(f"   Tipo de firma: {signature_type}")
                    logger.info(f"   Tamaño firmado: {len(signed_pdf)} bytes ({len(signed_pdf)/1024:.2f} KB)")
                    await record_notary_success()
                    return signed_pdf
                elif response_retry.status_code >= 500:
                    error_msg = f"Notary falló en segundo intento (5xx {response_retry.status_code}): {response_retry.text[:300]}"
                    logger.error(f"[ERR] {error_msg}")
                    err = _annotate_notary_error(NotaryUnavailableError(error_msg), response=response_retry, attempt_label="sign-pdf/retry")
                    await record_notary_failure(err)
                    raise err
                else:
                    error_msg = f"Notary falló en segundo intento ({response_retry.status_code}): {response_retry.text[:300]}"
                    logger.error(f"[ERR] {error_msg}")
                    raise NotaryBusinessError(error_msg)

            else:
                error_msg = f"Notary respondió 400 (no FULLPAGE): {response.text[:300]}"
                logger.error(f"[ERR] {error_msg}")
                raise NotaryBusinessError(error_msg)

        else:
            error_msg = f"Notary respondió {response.status_code}: {response.text[:300]}"
            logger.error(f"[ERR] {error_msg}")
            if response.status_code >= 500:
                err = _annotate_notary_error(NotaryUnavailableError(error_msg), response=response, attempt_label="sign-pdf")
                await record_notary_failure(err)
                raise err
            else:
                raise NotaryBusinessError(error_msg)

    except httpx.TimeoutException as e:
        error_msg = f"Timeout llamando a Notary (>{NOTARY_TIMEOUT}s): {str(e)}"
        logger.error(f"[ERR] {error_msg}")
        err = _annotate_notary_error(NotaryTimeoutError(error_msg), exc=e, attempt_label="sign-pdf/timeout")
        await record_notary_failure(err)
        raise err

    except httpx.RequestError as e:
        error_msg = f"Error de conexión con Notary: {str(e)}"
        logger.error(f"[ERR] {error_msg}")
        err = _annotate_notary_error(NotaryUnavailableError(error_msg), exc=e, attempt_label="sign-pdf/connect")
        await record_notary_failure(err)
        raise err

    except (NotaryUnavailableError, NotaryTimeoutError, NotaryBusinessError):
        raise

    except Exception as e:
        error_msg = f"Error inesperado en flujo de firma: {type(e).__name__} - {str(e)}"
        logger.error(f"[ERR] {error_msg}")
        raise Exception(error_msg)


async def call_notary_verify(pdf_bytes: bytes) -> dict:
    headers = {"x-api-key": NOTARY_API_KEY}
    try:
        from services.notary_internal_hmac import build_internal_hmac_header
        hmac_value = build_internal_hmac_header(method="POST", path="/sign-pdf/verify", body=pdf_bytes)
        if hmac_value:
            headers["X-Internal-Sign"] = hmac_value
    except Exception as _hmac_err:
        logger.warning(f"No se pudo generar X-Internal-Sign para /sign-pdf/verify (soft-fail): {_hmac_err}")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                f"{NOTARY_URL}/sign-pdf/verify",
                files={"pdf_file": ("signed.pdf", pdf_bytes, "application/pdf")},
                headers=headers,
            )
        if response.status_code == 200:
            return response.json()
        return {
            "ok": False,
            "failure_reason": f"notary_verify_http_{response.status_code}",
            "signature_count": 0,
            "signature_visible": False,
            "modification_level": None,
        }
    except Exception as e:
        logger.warning(f"call_notary_verify error (soft): {e}")
        return {
            "ok": False,
            "failure_reason": f"notary_verify_unreachable",
            "signature_count": 0,
            "signature_visible": False,
            "modification_level": None,
        }


async def call_notary_stamp_only(
    pdf_bytes: bytes,
    official_number: str,
    city: str = "LATAM",
    stamp_position: str = "first",
    existing_count: int | None = None,
) -> tuple[bytes, float, float, float, float]:
    import base64
    from services.shared.notary_breaker import (
        check_breaker_before_call, record_notary_failure, record_notary_success,
    )

    await check_breaker_before_call()

    def _build_files(cuerpo: bytes) -> dict:
        f = {
            "pdf_file": ("document.pdf", cuerpo, "application/pdf"),
            "document_number": (None, official_number),
            "city": (None, city),
            "stamp_position": (None, stamp_position),
        }
        if existing_count is not None:
            f["existing_count"] = (None, str(existing_count))
        return f

    files = _build_files(pdf_bytes)

    headers = _notary_headers("/stamp-number", pdf_bytes)

    try:
        async with httpx.AsyncClient(timeout=NOTARY_TIMEOUT) as client:
            response = await post_micro_with_coldstart_retry(
                client,
                f"{NOTARY_URL}/stamp-number",
                files=files,
                headers=headers,
                log_label="Notary stamp-number",
            )
    except httpx.TimeoutException as e:
        err = _annotate_notary_error(NotaryTimeoutError(f"Timeout /stamp-number (>{NOTARY_TIMEOUT}s): {str(e)}"), exc=e, attempt_label="stamp-number/timeout")
        logger.error(f"[ERR] {err}")
        await record_notary_failure(err)
        raise err
    except httpx.RequestError as e:
        err = _annotate_notary_error(NotaryUnavailableError(f"Error de conexión /stamp-number: {str(e)}"), exc=e, attempt_label="stamp-number/connect")
        logger.error(f"[ERR] {err}")
        await record_notary_failure(err)
        raise err

    if response.status_code == 400:
        _texto = response.text.upper()
        try:
            _json = response.json()
        except Exception:
            _json = None
        _es_fullpage = "FULLPAGE" in _texto or (_json and "FULLPAGE" in str(_json).upper())

        if _es_fullpage:
            logger.warning(
                "Notary /stamp-number respondió FULLPAGE (el documento no deja espacio "
                "para la firma) — agregando página de firma y reintentando"
            )
            try:
                augmented_pdf = add_blank_page_to_pdf(pdf_bytes)
            except Exception as pdf_error:
                logger.error(f"Error agregando página de firma: {pdf_error}")
                raise NotaryBusinessError(
                    "notary_fullpage: el documento no deja espacio para la firma y no se "
                    f"pudo agregar una página automáticamente ({pdf_error})"
                )

            try:
                async with httpx.AsyncClient(timeout=NOTARY_TIMEOUT) as client:
                    response = await post_micro_with_coldstart_retry(
                        client,
                        f"{NOTARY_URL}/stamp-number",
                        files=_build_files(augmented_pdf),
                        headers=_notary_headers("/stamp-number", augmented_pdf),
                        log_label="Notary stamp-number (retry FULLPAGE)",
                    )
            except httpx.TimeoutException as e:
                err = _annotate_notary_error(
                    NotaryTimeoutError(f"Timeout /stamp-number en retry FULLPAGE (>{NOTARY_TIMEOUT}s): {str(e)}"),
                    exc=e, attempt_label="stamp-number/fullpage/timeout",
                )
                logger.error(f"[ERR] {err}")
                await record_notary_failure(err)
                raise err
            except httpx.RequestError as e:
                err = _annotate_notary_error(
                    NotaryUnavailableError(f"Error de conexión /stamp-number en retry FULLPAGE: {str(e)}"),
                    exc=e, attempt_label="stamp-number/fullpage/connect",
                )
                logger.error(f"[ERR] {err}")
                await record_notary_failure(err)
                raise err

            if response.status_code == 200:
                logger.info("Notary /stamp-number OK tras agregar la página de firma (GDI-252)")
            elif response.status_code >= 500:
                err = _annotate_notary_error(
                    NotaryUnavailableError(
                        f"Notary /stamp-number falló {response.status_code} en el retry "
                        f"FULLPAGE: {response.text[:300]}"
                    ),
                    response=response,
                    attempt_label="stamp-number/fullpage",
                )
                logger.error(f"[ERR] {err}")
                await record_notary_failure(err)
                raise err
            else:
                raise NotaryBusinessError(
                    f"notary_fullpage: /stamp-number falló también con la página agregada "
                    f"({response.status_code}): {response.text[:300]}"
                )

    if response.status_code != 200:
        error_msg = f"Notary /stamp-number falló {response.status_code}: {response.text[:300]}"
        logger.error(f"[ERR] {error_msg}")
        if response.status_code >= 500:
            err = _annotate_notary_error(NotaryUnavailableError(error_msg), response=response, attempt_label="stamp-number")
            await record_notary_failure(err)
            raise err
        else:
            raise NotaryBusinessError(error_msg)

    await record_notary_success()
    data = response.json()
    stamped_pdf = base64.b64decode(data["stamped_pdf_b64"])
    return (
        stamped_pdf,
        float(data["sig_llx"]),
        float(data["sig_lly"]),
        float(data["sig_urx"]),
        float(data["sig_ury"]),
    )
