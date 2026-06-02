"""
Módulo para firma digital PAdES de documentos PDF.

Implementa firma PAdES-B-T (Basic with Time) usando pyHanko,
que incluye:
- Firma digital criptográfica embebida en el PDF
- Timestamp de servidor TSA (Time Stamping Authority)
- Cumplimiento con estándares PAdES de ETSI

Autor: GDI Latam
Versión: 1.0.0
"""

import asyncio
import io
import logging
import time
from typing import Optional

from pyhanko.sign import signers, timestamps, fields
from pyhanko.sign.general import SigningError as PyHankoSigningError
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


# =============================================================================
# CIRCUIT BREAKER PARA TSA
# =============================================================================

class TsaCircuitBreaker:
    """
    Circuit breaker minimalista en memoria para las llamadas al TSA (AC-ONTI).

    Estados:
    - CLOSED  : comportamiento normal, las llamadas pasan.
    - OPEN    : el TSA se considera caído; las llamadas fallan rápido con 503.
    - HALF_OPEN: período de prueba tras el cooldown; deja pasar UNA request;
                 si tiene éxito → CLOSED, si falla → OPEN (reinicia cooldown).

    Parámetros (hardcodeados; se pueden externalizar a config.py si se necesita):
    - FAILURE_THRESHOLD = 5   fallos consecutivos para abrir el breaker.
    - COOLDOWN_SECONDS  = 60  segundos en OPEN antes de pasar a HALF_OPEN.

    El estado es compartido entre workers de Gunicorn SOLO si se usa
    threading/uvicorn workers (memoria compartida). Con múltiples procesos
    Gunicorn cada worker tiene su propio estado, lo cual es aceptable:
    cada proceso abrirá su propio breaker tras sus propias fallas.
    """

    # Umbral de fallos consecutivos antes de abrir el breaker.
    # Racional: con TSA_RETRIES=2 y TSA_TIMEOUT=3s cada ciclo de fallos
    # tarda ~9.8s (3 intentos + 0.2s + 0.6s backoff). Con 5 ciclos fallidos
    # el sistema esperó ~49s antes de abrir —suficiente para distinguir
    # una caída real del TSA de timeouts esporádicos.
    FAILURE_THRESHOLD = 5

    # Segundos en estado OPEN antes de intentar HALF_OPEN.
    COOLDOWN_SECONDS = 60

    # Nombres de estados (evita strings sueltos)
    STATE_CLOSED    = "CLOSED"
    STATE_OPEN      = "OPEN"
    STATE_HALF_OPEN = "HALF_OPEN"

    def __init__(self):
        self._state = self.STATE_CLOSED
        self._failure_count = 0       # fallos consecutivos en CLOSED / HALF_OPEN
        self._opened_at: float = 0.0  # timestamp (time.monotonic) cuando se abrió

    # ------------------------------------------------------------------
    # Propiedades de consulta
    # ------------------------------------------------------------------

    @property
    def state(self) -> str:
        """Devuelve el estado actual, resolviendo la transición OPEN→HALF_OPEN."""
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
        """True si el breaker NO permite pasar la request (estado OPEN)."""
        return self.state == self.STATE_OPEN

    # ------------------------------------------------------------------
    # Registro de resultados
    # ------------------------------------------------------------------

    def record_success(self):
        """Llamar tras una respuesta exitosa del TSA."""
        if self._state != self.STATE_CLOSED:
            logger.info(
                f"TSA CircuitBreaker: éxito en estado {self._state} → CLOSED"
            )
        self._state = self.STATE_CLOSED
        self._failure_count = 0

    def record_failure(self):
        """
        Llamar tras un fallo del TSA (timeout, error HTTP, etc.).
        En HALF_OPEN un fallo único reabre el breaker inmediatamente.
        """
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
        self._failure_count = 0  # reinicia para la próxima vez que se cierre


# Singleton del circuit breaker — compartido en el proceso.
_tsa_circuit_breaker = TsaCircuitBreaker()


class PAdESSigningError(Exception):
    """Excepción para errores de firma PAdES."""
    pass


class PAdESTimestampError(PAdESSigningError):
    """Error al obtener timestamp del TSA."""
    pass


class PAdESTsaUnavailableError(PAdESTimestampError):
    """
    Error fail-fast cuando el circuit breaker está abierto.
    El caller debería devolver HTTP 503 al cliente en lugar de 500.
    """
    pass


class PAdESCertificateError(PAdESSigningError):
    """Error relacionado con el certificado."""
    pass


def create_signer_from_p12(p12_path: str, password: str) -> signers.SimpleSigner:
    """
    Crea un firmante pyHanko directamente desde un archivo .p12.

    pyHanko tiene su propio método para cargar PKCS#12 que es más compatible.

    Args:
        p12_path: Ruta al archivo .p12
        password: Password del archivo

    Returns:
        SimpleSigner: Firmante configurado para pyHanko
    """
    # Usar el método nativo de pyHanko para cargar PKCS#12
    signer = signers.SimpleSigner.load_pkcs12(
        pfx_file=p12_path,
        passphrase=password.encode('utf-8') if password else None,
    )
    return signer


def create_signer_from_certificate(cert: LoadedCertificate) -> signers.SimpleSigner:
    """
    Crea un firmante pyHanko desde un certificado cargado.

    Args:
        cert: Certificado cargado con clave privada

    Returns:
        SimpleSigner: Firmante configurado para pyHanko
    """
    # Validar certificado antes de usar
    is_valid, message = validate_certificate(cert)
    if not is_valid:
        raise PAdESCertificateError(f"Certificado inválido: {message}")

    # Si el certificado tiene password directo (cargado desde bytes / R2),
    # usarlo sin leer passwords.json
    password = getattr(cert, '_password', None)

    if password is None:
        # Fallback: leer password de passwords.json (modo local/dev con .p12 en disco).
        # En PRD este branch nunca se ejecuta: _password viene seteado por
        # load_certificate_from_bytes. Aqui fallamos fuerte para facilitar debug.
        from .config import PASSWORDS_FILE
        import json

        try:
            with open(PASSWORDS_FILE, 'r') as f:
                passwords = json.load(f)
            password = passwords.get(cert.tenant_id)
            if password is None:
                raise PAdESCertificateError(
                    f"Tenant '{cert.tenant_id}' no tiene password en passwords.json"
                )
        except FileNotFoundError:
            raise PAdESCertificateError(
                f"passwords.json no encontrado en {PASSWORDS_FILE}"
            )
        except json.JSONDecodeError as e:
            raise PAdESCertificateError(
                f"passwords.json tiene formato invalido: {e}"
            )

    return create_signer_from_p12(str(cert.path), password)


class RetryingTimestamper(timestamps.TimeStamper):
    """
    Wrapper sobre HTTPTimeStamper que agrega reintentos con backoff exponencial.
    Si todos los reintentos fallan, lanza PAdESTimestampError -> 503 al caller.

    NO hace failover entre TSAs (decision de diseno: YAGNI).
    Se mantiene 1 sola TSA (DigiCert) como hoy.
    """

    def __init__(self, url: str, timeout: int, retries: int):
        super().__init__()  # inicializa _dummy_response_cache, _certs, cert_registry
        self._client = timestamps.HTTPTimeStamper(url, timeout=timeout)
        self._url = url
        self._retries = retries  # numero de REINTENTOS (intentos totales = retries + 1)
        self.last_retry_count = 0  # expuesto para logging via P3.0
        self.last_url = url  # expuesto para logging via P3.0

    async def async_request_tsa_response(self, req):
        # --- Circuit breaker: fail-fast si el breaker está abierto ---
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
        for attempt in range(total_attempts):
            try:
                # HTTPTimeStamper ya tiene timeout explícito (TSA_TIMEOUT segundos)
                # configurado en get_timestamp_client() → RetryingTimestamper.__init__.
                resp = await self._client.async_request_tsa_response(req)
                self.last_retry_count = attempt
                if attempt > 0:
                    logger.info(
                        f"TSA respondio en intento {attempt + 1}/{total_attempts}"
                    )
                # Éxito: cerrar el breaker si estaba en HALF_OPEN
                _tsa_circuit_breaker.record_success()
                return resp
            except PAdESTimestampError:
                # Re-raise sin registrar como fallo del TSA (ya fue tratado arriba)
                raise
            except Exception as e:
                last_error = e
                logger.warning(
                    f"TSA {self._url} fallo intento {attempt + 1}/{total_attempts}: {e}"
                )
                if attempt < self._retries:
                    # Backoff exponencial: 200ms, 600ms
                    await asyncio.sleep(0.2 * (3 ** attempt))

        self.last_retry_count = self._retries

        # Todos los intentos fallaron: registrar en el circuit breaker
        _tsa_circuit_breaker.record_failure()

        raise PAdESTimestampError(
            f"TSA {self._url} fallo {total_attempts} intentos seguidos. "
            f"Ultimo error: {last_error}"
        )


def get_timestamp_client() -> RetryingTimestamper:
    """
    Crea un RetryingTimestamper con timeout y reintentos configurados.
    Nunca devuelve None: si TSA_URL no esta configurado lanza PAdESTimestampError.
    """
    if not TSA_URL:
        raise PAdESTimestampError("TSA_URL no configurado")
    return RetryingTimestamper(
        url=TSA_URL,
        timeout=TSA_TIMEOUT,
        retries=TSA_RETRIES,
    )


def count_pades_signatures(pdf_content: bytes) -> int:
    """Cuenta las firmas PAdES existentes en un PDF."""
    try:
        reader = PdfFileReader(io.BytesIO(pdf_content))
        return len(list(reader.embedded_signatures))
    except Exception:
        return 0


def calculate_pades_field_position(pades_index: int, base_y: float) -> tuple[float, float]:
    """
    Calcula la posición del campo de firma PAdES basado en el índice.

    Layout de 2 columnas:
    - Firma 0: columna izquierda (x=50)
    - Firma 1: columna derecha (x=270)
    - Firma 2: columna izquierda, fila siguiente (y - 100)
    - etc.

    Args:
        pades_index: Índice de la firma (0, 1, 2, ...)
        base_y: Posición Y base (de la primera firma)

    Returns:
        tuple (x, y): Posición del campo de firma
    """
    from .config import FIRST_SIGNATURE_X, SECOND_SIGNATURE_X, SIGNATURE_HEIGHT, ROW_SPACING

    # Columna: par = izquierda, impar = derecha
    if pades_index % 2 == 0:
        x = FIRST_SIGNATURE_X
    else:
        x = SECOND_SIGNATURE_X

    # Fila: cada 2 firmas baja una fila
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
) -> bytes:
    """
    Firma PAdES con layout automático de 2 columnas y diseño visual profesional.

    Usa pyHanko con TextStampStyle personalizado para mostrar
    información del firmante en un diseño limpio.

    Layout de 2 columnas:
    - Firma 1: columna izquierda (x=50)
    - Firma 2: columna derecha (x=270)
    - Firma 3: columna izquierda, siguiente fila
    - etc.

    Args:
        pdf_content: Contenido del PDF original
        cert: Certificado cargado con clave privada
        signature_params: Dict con name, seal, department, entity
        x: Coordenada X base (para primera firma)
        y: Coordenada Y base (para primera firma)
        existing_signature_count: Número de firmas existentes (para compatibilidad)

    Returns:
        bytes: PDF firmado con firma PAdES

    Raises:
        PAdESSigningError: Si hay error en la firma PAdES
    """
    try:
        name = signature_params.get('name', 'Firmante')
        seal = signature_params.get('seal', '')
        department = signature_params.get('department', '')
        entity = signature_params.get('entity', '')

        logger.info(f"Iniciando firma PAdES para: {name}")

        # Detectar firmas PAdES existentes para nombre único del campo
        pades_count = count_pades_signatures(pdf_content)
        logger.info(f"  - Firmas PAdES existentes: {pades_count}")

        # Usar posición calculada por layout.py (fuente única de verdad)
        sig_x, sig_y = x, y
        logger.info(f"  - Posición recibida de layout: ({sig_x}, {sig_y})")

        # Crear firmante
        signer = create_signer_from_certificate(cert)

        # Preparar PDF para escritura incremental
        pdf_writer = IncrementalPdfFileWriter(io.BytesIO(pdf_content))

        # Timestamp
        timestamper = get_timestamp_client()

        # Nombre único del campo
        sig_field_name = f"{PADES_SIGNATURE_FIELD_NAME}_{pades_count + 1}"

        # Obtener número de páginas (resolver IndirectObject por si /Pages está anidado)
        pdf_reader = PdfFileReader(io.BytesIO(pdf_content))
        page_count = pdf_reader.root['/Pages'].get_object()['/Count']
        last_page = page_count - 1

        # Campo de firma con posición calculada
        from .config import SIGNATURE_WIDTH, SIGNATURE_HEIGHT
        sig_field_spec = fields.SigFieldSpec(
            sig_field_name=sig_field_name,
            on_page=last_page,
            box=(sig_x, sig_y, sig_x + SIGNATURE_WIDTH, sig_y + SIGNATURE_HEIGHT),
        )

        # Metadata de firma (Adobe Reader muestra esta info)
        signature_meta = signers.PdfSignatureMetadata(
            field_name=sig_field_name,
            name=name,
            reason=f"{seal} - {department}",
            location=entity,
            subfilter=fields.SigSeedSubFilter.PADES,
            md_algorithm='sha256',
        )

        # Crear estilo de stamp profesional con información del firmante
        # Usa %(signer)s y %(ts)s interpolados por pyHanko, más parámetros custom
        # Nombre en MAYÚSCULAS para destacar, línea separadora ASCII

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

        # Parámetros personalizados para interpolación en el stamp
        appearance_text_params = {
            'signer_upper': name.upper(),
            'seal': seal,
            'department': department,
            'entity': entity,
        }

        logger.info(f"  - Campo de firma: {sig_field_name} en página {last_page + 1}")
        logger.info(f"  - Timestamp: Sí (TSA con reintentos)")

        # Crear PdfSigner con stamp_style para diseño visual profesional
        pdf_signer = signers.PdfSigner(
            signature_meta=signature_meta,
            signer=signer,
            timestamper=timestamper,
            stamp_style=stamp_style,
            new_field_spec=sig_field_spec,
        )

        # Firmar usando versión async con appearance_text_params
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
        # Subclases (incluida PAdESTsaUnavailableError) se propagan tal cual
        # para que el caller distinga TSA caído (503) de otros errores PAdES (500).
        raise
    except PyHankoSigningError as e:
        logger.error(f"Error de pyHanko: {e}")
        raise PAdESSigningError(f"Error al firmar PDF: {e}")
    except Exception as e:
        logger.error(f"Error inesperado: {e}")
        raise PAdESSigningError(f"Error inesperado: {e}")


def verify_pades_signature(pdf_content: bytes) -> dict:
    """
    Verifica las firmas PAdES de un PDF.

    Args:
        pdf_content: Contenido del PDF

    Returns:
        dict: Resultado de la verificación
    """
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko.pdf_utils.reader import PdfFileReader

    try:
        reader = PdfFileReader(io.BytesIO(pdf_content))
        sig_fields = reader.embedded_signatures

        results = []
        for sig in sig_fields:
            try:
                # Validación básica (sin verificar cadena de confianza completa)
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


def get_pades_signature_info() -> dict:
    """
    Retorna información sobre el sistema de firma PAdES.

    Returns:
        dict: Info del sistema
    """
    return {
        "type": "PAdES-B-T",
        "library": "pyHanko",
        "tsa_url": TSA_URL,
        "hash_algorithm": "SHA256",
        "signature_format": "PAdES (PDF Advanced Electronic Signature)",
        "timestamp_enabled": bool(TSA_URL),
    }
