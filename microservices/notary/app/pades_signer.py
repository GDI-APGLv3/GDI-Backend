
import asyncio
import io
import logging
import time
from typing import Optional

from pyhanko.sign import signers, timestamps, fields
from pyhanko.sign.general import SigningError as PyHankoSigningError
from pyhanko.sign.signers import PdfTimeStamper
from pyhanko.sign.signers.pdf_cms import (
    _translate_pyca_cryptography_cert_to_asn1,
    _translate_pyca_cryptography_key_to_asn1,
)
from pyhanko.sign.general import get_pyca_cryptography_hash
from cryptography.hazmat.primitives.asymmetric.padding import PKCS1v15
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from pyhanko_certvalidator.registry import SimpleCertificateStore
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.stamp import TextStampStyle

from .config import (
    TSA_URL,
    TSA_TIMEOUT,
    TSA_RETRIES,
    PADES_SIGNATURE_FIELD_NAME,
    PADES_SIGNATURE_REASON,
    PADES_SIGNATURE_LOCATION,
)
from .certificate_loader import LoadedCertificate, validate_certificate

logger = logging.getLogger(__name__)


class TsaCircuitBreaker:

    FAILURE_THRESHOLD = 5

    COOLDOWN_SECONDS = 60

    STATE_CLOSED    = "CLOSED"
    STATE_OPEN      = "OPEN"
    STATE_HALF_OPEN = "HALF_OPEN"

    def __init__(self):
        self._state = self.STATE_CLOSED
        self._failure_count = 0
        self._opened_at: float = 0.0


    @property
    def state(self) -> str:
        if self._state == self.STATE_OPEN:
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self.COOLDOWN_SECONDS:
                logger.info(
                    f"TSA CircuitBreaker: cooldown expirado ({elapsed:.0f}s) → HALF_OPEN"
                )
                self._state = self.STATE_HALF_OPEN
        return self._state

    @property
    def is_open(self) -> bool:
        return self.state == self.STATE_OPEN


    def record_success(self):
        if self._state != self.STATE_CLOSED:
            logger.info(
                f"TSA CircuitBreaker: éxito en estado {self._state} → CLOSED"
            )
        self._state = self.STATE_CLOSED
        self._failure_count = 0

    def record_failure(self):
        self._failure_count += 1

        if self._state == self.STATE_HALF_OPEN:
            logger.warning(
                "TSA CircuitBreaker: fallo en HALF_OPEN → OPEN "
                f"(cooldown reinicia {self.COOLDOWN_SECONDS}s)"
            )
            self._open()
            return

        if self._failure_count >= self.FAILURE_THRESHOLD:
            logger.error(
                f"TSA CircuitBreaker: {self._failure_count} fallos consecutivos "
                f"(umbral={self.FAILURE_THRESHOLD}) → OPEN. "
                f"Cooldown: {self.COOLDOWN_SECONDS}s"
            )
            self._open()

    def _open(self):
        self._state = self.STATE_OPEN
        self._opened_at = time.monotonic()
        self._failure_count = 0


_tsa_circuit_breaker = TsaCircuitBreaker()


class PAdESSigningError(Exception):
    pass


class PAdESTimestampError(PAdESSigningError):
    pass


class PAdESTsaUnavailableError(PAdESTimestampError):
    pass


class PAdESCertificateError(PAdESSigningError):
    pass


def create_signer_from_p12(p12_path: str, password: str) -> signers.SimpleSigner:
    signer = signers.SimpleSigner.load_pkcs12(
        pfx_file=p12_path,
        passphrase=password.encode('utf-8') if password else None,
    )
    return signer


class AsyncSimpleSigner(signers.SimpleSigner):

    _pyca_private_key = None

    def sign_raw(self, data: bytes, digest_algorithm: str) -> bytes:
        if self._pyca_private_key is None:
            return super().sign_raw(data, digest_algorithm)

        signature_mechanism = self.get_signature_mechanism_for_digest(
            digest_algorithm
        )
        if (
            signature_mechanism.signature_algo == 'rsassa_pkcs1v15'
            and isinstance(self._pyca_private_key, RSAPrivateKey)
        ):
            hash_algo = get_pyca_cryptography_hash(digest_algorithm)
            return self._pyca_private_key.sign(data, PKCS1v15(), hash_algo)

        return super().sign_raw(data, digest_algorithm)

    async def async_sign_raw(
        self, data: bytes, digest_algorithm: str, dry_run=False
    ) -> bytes:
        return await asyncio.to_thread(self.sign_raw, data, digest_algorithm)


def create_signer_from_certificate(cert: LoadedCertificate) -> AsyncSimpleSigner:
    is_valid, message = validate_certificate(cert)
    if not is_valid:
        raise PAdESCertificateError(f"Certificado inválido: {message}")

    try:
        signing_key = _translate_pyca_cryptography_key_to_asn1(cert.private_key)
        signing_cert = _translate_pyca_cryptography_cert_to_asn1(cert.certificate)

        cert_registry = SimpleCertificateStore()
        if cert.additional_certs:
            cert_registry.register_multiple(
                _translate_pyca_cryptography_cert_to_asn1(c)
                for c in cert.additional_certs
            )
    except Exception as e:
        raise PAdESCertificateError(
            f"Error al construir el firmante desde el certificado: {e}"
        )

    signer = AsyncSimpleSigner(
        signing_cert=signing_cert,
        signing_key=signing_key,
        cert_registry=cert_registry,
    )
    if isinstance(cert.private_key, RSAPrivateKey):
        signer._pyca_private_key = cert.private_key
    return signer


class RetryingTimestamper(timestamps.TimeStamper):

    def __init__(self, url: str, timeout: int, retries: int):
        super().__init__()
        self._client = timestamps.HTTPTimeStamper(url, timeout=timeout)
        self._url = url
        self._retries = retries
        self._dummy_response_lock = asyncio.Lock()

    async def async_dummy_response(self, md_algorithm) -> "cms.ContentInfo":
        try:
            return self._dummy_response_cache[md_algorithm]
        except KeyError:
            pass

        async with self._dummy_response_lock:
            try:
                return self._dummy_response_cache[md_algorithm]
            except KeyError:
                pass
            return await super().async_dummy_response(md_algorithm)

    async def async_request_tsa_response(self, req):
        if _tsa_circuit_breaker.is_open:
            logger.warning(
                f"TSA CircuitBreaker OPEN: rechazando llamada a {self._url} "
                f"sin esperar (fail-fast)."
            )
            raise PAdESTsaUnavailableError(
                "TSA no disponible (circuit breaker abierto). "
                "El servicio de timestamp AC-ONTI está respondiendo con errores "
                "consecutivos. Reintentá en unos segundos."
            )

        last_error = None
        total_attempts = self._retries + 1
        t_total_start = time.perf_counter()
        for attempt in range(total_attempts):
            t_attempt_start = time.perf_counter()
            try:
                resp = await self._client.async_request_tsa_response(req)
                t_attempt_ms = (time.perf_counter() - t_attempt_start) * 1000
                t_total_ms = (time.perf_counter() - t_total_start) * 1000
                logger.info(
                    f"TSA latency OK — attempt={attempt + 1}/{total_attempts} "
                    f"attempt_ms={t_attempt_ms:.1f} total_ms={t_total_ms:.1f} "
                    f"retries_used={attempt} url={self._url}"
                )
                _tsa_circuit_breaker.record_success()
                return resp
            except PAdESTimestampError:
                raise
            except Exception as e:
                t_attempt_ms = (time.perf_counter() - t_attempt_start) * 1000
                last_error = e
                logger.warning(
                    f"TSA latency FAIL — attempt={attempt + 1}/{total_attempts} "
                    f"attempt_ms={t_attempt_ms:.1f} url={self._url} error={e}"
                )
                if attempt < self._retries:
                    await asyncio.sleep(0.2 * (3 ** attempt))

        t_total_ms = (time.perf_counter() - t_total_start) * 1000
        logger.error(
            f"TSA latency EXHAUSTED — attempts={total_attempts} "
            f"total_ms={t_total_ms:.1f} url={self._url} last_error={last_error}"
        )

        _tsa_circuit_breaker.record_failure()

        raise PAdESTimestampError(
            f"TSA {self._url} fallo {total_attempts} intentos seguidos. "
            f"Ultimo error: {last_error}"
        )


_shared_timestamper: Optional[RetryingTimestamper] = (
    RetryingTimestamper(url=TSA_URL, timeout=TSA_TIMEOUT, retries=TSA_RETRIES)
    if TSA_URL
    else None
)


def get_timestamp_client() -> RetryingTimestamper:
    if _shared_timestamper is None:
        raise PAdESTimestampError("TSA_URL no configurado")
    return _shared_timestamper


def count_pades_signatures(pdf_content: bytes) -> int:
    try:
        reader = PdfFileReader(io.BytesIO(pdf_content))
        return len(list(reader.embedded_signatures))
    except Exception as e:
        logger.warning(
            f"notary.count_pades_signatures_parse_failed: {type(e).__name__}: {e} "
            "— asumiendo 0 firmas"
        )
        return 0


def count_pades_timestamps(pdf_content: bytes) -> int:
    try:
        reader = PdfFileReader(io.BytesIO(pdf_content))
        return len(list(reader.embedded_timestamp_signatures))
    except Exception as e:
        logger.warning(
            f"notary.count_pades_timestamps_parse_failed: {type(e).__name__}: {e} "
            "— asumiendo 0 timestamps (fail-open: peor caso es un 2do "
            "DocTimeStamp incremental, no invalida firmas previas)"
        )
        return 0


def calculate_pades_field_position(pades_index: int, base_y: float) -> tuple[float, float]:
    from .config import FIRST_SIGNATURE_X, SECOND_SIGNATURE_X, SIGNATURE_HEIGHT, ROW_SPACING

    if pades_index % 2 == 0:
        x = FIRST_SIGNATURE_X
    else:
        x = SECOND_SIGNATURE_X

    row = pades_index // 2
    row_offset = row * (SIGNATURE_HEIGHT + ROW_SPACING)
    y = base_y - row_offset

    return x, y


async def sign_pdf_combined(
    pdf_content: bytes,
    cert: LoadedCertificate,
    signature_params: dict,
    x: float,
    y: float,
    existing_signature_count: int = 0,
    defer_timestamp: bool = False,
) -> bytes:
    try:
        name = signature_params.get('name', 'Firmante')
        seal = signature_params.get('seal', '')
        department = signature_params.get('department', '')
        entity = signature_params.get('entity', '')

        logger.info(f"Iniciando firma PAdES para: {name}")

        pades_count = count_pades_signatures(pdf_content)
        logger.info(f"  - Firmas PAdES existentes: {pades_count}")

        sig_x, sig_y = x, y
        logger.info(f"  - Posición recibida de layout: ({sig_x}, {sig_y})")

        signer = create_signer_from_certificate(cert)

        pdf_writer = IncrementalPdfFileWriter(io.BytesIO(pdf_content))

        if defer_timestamp:
            timestamper = None
            logger.info("  - Timestamp: DIFERIDO (modo B-B, sin TSA)")
        else:
            timestamper = get_timestamp_client()
            logger.info(f"  - Timestamp: Sí (TSA con reintentos)")

        sig_field_name = f"{PADES_SIGNATURE_FIELD_NAME}_{pades_count + 1}"

        pdf_reader = PdfFileReader(io.BytesIO(pdf_content))
        page_count = pdf_reader.root['/Pages'].get_object()['/Count']
        last_page = page_count - 1

        from .config import SIGNATURE_WIDTH, SIGNATURE_HEIGHT
        sig_field_spec = fields.SigFieldSpec(
            sig_field_name=sig_field_name,
            on_page=last_page,
            box=(sig_x, sig_y, sig_x + SIGNATURE_WIDTH, sig_y + SIGNATURE_HEIGHT),
        )

        signature_meta = signers.PdfSignatureMetadata(
            field_name=sig_field_name,
            name=name,
            reason=f"{seal} - {department}",
            location=entity,
            subfilter=fields.SigSeedSubFilter.PADES,
            md_algorithm='sha256',
        )


        stamp_style = TextStampStyle(
            stamp_text=(
                "%(signer_upper)s\n"
                "%(seal)s\n"
                "%(department)s\n"
                "%(entity)s"
            ),
            border_width=0,
            background_opacity=0.0,
        )

        appearance_text_params = {
            'signer_upper': name.upper(),
            'seal': seal,
            'department': department,
            'entity': entity,
        }

        logger.info(f"  - Campo de firma: {sig_field_name} en página {last_page + 1}")

        pdf_signer = signers.PdfSigner(
            signature_meta=signature_meta,
            signer=signer,
            timestamper=timestamper,
            stamp_style=stamp_style,
            new_field_spec=sig_field_spec,
        )

        output = io.BytesIO()

        await pdf_signer.async_sign_pdf(
            pdf_writer,
            output=output,
            appearance_text_params=appearance_text_params,
        )

        result = output.getvalue()
        logger.info(f"Firma PAdES completada. Tamaño: {len(result)} bytes")

        return result

    except PAdESSigningError:
        raise
    except PyHankoSigningError as e:
        logger.error(f"Error de pyHanko: {e}")
        raise PAdESSigningError(f"Error al firmar PDF: {e}")
    except Exception as e:
        logger.error(f"Error inesperado: {e}")
        raise PAdESSigningError(f"Error inesperado: {e}")


def verify_pades_signature(pdf_content: bytes) -> dict:
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko.pdf_utils.reader import PdfFileReader

    try:
        reader = PdfFileReader(io.BytesIO(pdf_content))
        sig_fields = reader.embedded_signatures

        results = []
        for sig in sig_fields:
            try:
                status = validate_pdf_signature(sig)
                results.append({
                    "field_name": sig.field_name,
                    "signer_name": str(sig.signer_cert.subject) if sig.signer_cert else "Unknown",
                    "valid": status.bottom_line,
                    "intact": status.intact,
                    "trusted": status.trusted,
                    "timestamp": status.timestamp_validity.timestamp if status.timestamp_validity else None,
                })
            except Exception as e:
                results.append({
                    "field_name": sig.field_name,
                    "error": str(e)
                })

        return {
            "signature_count": len(sig_fields),
            "signatures": results
        }

    except Exception as e:
        return {
            "error": str(e),
            "signature_count": 0,
            "signatures": []
        }


async def async_add_document_timestamp(pdf_content: bytes) -> bytes:
    try:
        logger.info(
            f"async_add_document_timestamp: agregando DocTimeStamp a PDF "
            f"de {len(pdf_content)} bytes"
        )
        timestamper = get_timestamp_client()
        pdf_writer = IncrementalPdfFileWriter(io.BytesIO(pdf_content))
        ts_stamper = PdfTimeStamper(timestamper)
        output = io.BytesIO()
        await ts_stamper.async_timestamp_pdf(
            pdf_writer,
            md_algorithm="sha256",
            output=output,
        )
        result = output.getvalue()
        logger.info(
            f"async_add_document_timestamp: DocTimeStamp OK. "
            f"Tamaño resultante: {len(result)} bytes"
        )
        return result
    except PAdESSigningError:
        raise
    except Exception as e:
        logger.error(f"async_add_document_timestamp: error inesperado: {e}")
        raise PAdESSigningError(f"Error al agregar DocTimeStamp al PDF: {e}")


def get_tsa_breaker_state() -> str:
    raw = _tsa_circuit_breaker.state
    return raw.lower()


def get_pades_signature_info() -> dict:
    return {
        "type": "PAdES-B-T",
        "library": "pyHanko",
        "tsa_url": TSA_URL,
        "hash_algorithm": "SHA256",
        "signature_format": "PAdES (PDF Advanced Electronic Signature)",
        "timestamp_enabled": bool(TSA_URL),
    }
