import os
from pathlib import Path


PAGE_WIDTH = 595
PAGE_HEIGHT = 842

LETTER_WIDTH = PAGE_WIDTH
LETTER_HEIGHT = PAGE_HEIGHT


STAMP_X = 400
STAMP_Y = 700
STAMP_WIDTH = 150
STAMP_HEIGHT = 40

STAMP_DOC_NUMBER_X = int(os.getenv("STAMP_DOC_NUMBER_X", "28"))
STAMP_DOC_NUMBER_Y = int(os.getenv("STAMP_DOC_NUMBER_Y", "740"))
STAMP_CITY_DATE_X = int(os.getenv("STAMP_CITY_DATE_X", "28"))
STAMP_CITY_DATE_Y = int(os.getenv("STAMP_CITY_DATE_Y", "755"))

STAMP_INCREMENTAL_FONT_PATH = Path(
    os.getenv(
        "STAMP_INCREMENTAL_FONT_PATH",
        str(Path(__file__).parent.parent / "fonts" / "Roboto-Bold.ttf"),
    )
)


END_TEXT_SEARCH = "end-text"

DEFAULT_FIRST_Y = 400
SIGNATURE_OFFSET_BELOW = 15

FIRST_SIGNATURE_X = 50
SECOND_SIGNATURE_X = 270
SIGNATURE_WIDTH = 200
SIGNATURE_HEIGHT = 80
ROW_SPACING = 20

MIN_SIGNATURE_Y = 100

SIGNATURE_DETECTION_TEXT = "Digitally Signed by"

SIGNATURE_MARGIN_LEFT = 20
SIGNATURE_MARGIN_RIGHT = 20
SIGNATURE_MARGIN_TOP = 20
SIGNATURE_MARGIN_BOTTOM = 20

SIGNATURE_X_POSITION = FIRST_SIGNATURE_X
SIGNATURE_Y_START = DEFAULT_FIRST_Y
SIGNATURE_SPACING = SIGNATURE_HEIGHT + ROW_SPACING


MAX_SIGNABLE_PDF_SIZE_MB = 64
MAX_SIGNABLE_PDF_SIZE = MAX_SIGNABLE_PDF_SIZE_MB * 1024 * 1024
REQUEST_TIMEOUT = 30

SIGNATURE_DETECTION_TEXT = "Digitally Signed by"


API_KEY = os.getenv("API_KEY", "")
if not API_KEY:
    raise RuntimeError(
        "FATAL: API_KEY environment variable is not set or is empty. "
        "Set it with: flyctl secrets set API_KEY=<value> -a <app-name>"
    )

TIMEZONE = "UTC"


FONT_NAME = "Montserrat"

FONT_SIZE_SIGNATURE = 10
FONT_SIZE_STAMP = 11
FONT_SIZE_IDENTIFIER = 8


SIGNATURE_IDENTIFIER = "Digitally Signed by TEST SERVER"

SERVICE_NAME = "Notary"


SPECIAL_CHARS = r'/\:*?"<>|'


ENVIRONMENT = os.getenv("ENVIRONMENT", "test").lower()


CERTS_DIR = Path(os.getenv("CERTS_DIR", Path(__file__).parent.parent / "certs"))

TSA_URL = os.getenv("TSA_URL", "http://timestamp.digicert.com")

TSA_TIMEOUT = int(os.getenv("TSA_TIMEOUT", "3"))

TSA_RETRIES = int(os.getenv("TSA_RETRIES", "2"))

_default_fallback = "false"
FALLBACK_TO_VISUAL = os.getenv("FALLBACK_TO_VISUAL", _default_fallback).lower() in ("true", "1", "yes")

PASSWORDS_FILE = CERTS_DIR / "passwords.json"

PADES_SIGNATURE_FIELD_NAME = "GDI_Signature"
PADES_SIGNATURE_REASON = "Documento firmado digitalmente"
PADES_SIGNATURE_LOCATION = "Sistema GDI"


NOTARY_INTERNAL_HMAC_SECRET = os.getenv("NOTARY_INTERNAL_HMAC_SECRET", "")

_HMAC_LEGACY_DEFAULT = "false" if ENVIRONMENT == "prd" else "true"
HMAC_ALLOW_LEGACY_FORMAT = os.getenv("HMAC_ALLOW_LEGACY_FORMAT", _HMAC_LEGACY_DEFAULT).lower() in ("true", "1", "yes")

if ENVIRONMENT == "prd" and not NOTARY_INTERNAL_HMAC_SECRET:
    raise RuntimeError(
        "FATAL: NOTARY_INTERNAL_HMAC_SECRET no está configurado en ambiente prd. "
        "Sin este secreto la validación HMAC inter-servicio queda desactivada "
        "(fail-open), lo cual es inaceptable en producción. Configurar con: "
        "flyctl secrets set NOTARY_INTERNAL_HMAC_SECRET=<valor> -a <app-name> "
        "(mismo valor en la app de Backend correspondiente)."
    )


REQUIRE_EXPECTED_SHA256 = os.getenv("REQUIRE_EXPECTED_SHA256", "false").lower() in ("true", "1", "yes")


MAX_REQUEST_BODY_SIZE_MB = int(os.getenv("MAX_REQUEST_BODY_SIZE_MB", str(MAX_SIGNABLE_PDF_SIZE_MB + 4)))
MAX_REQUEST_BODY_SIZE = MAX_REQUEST_BODY_SIZE_MB * 1024 * 1024