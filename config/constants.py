
import os


SYSTEM_TEST_USER_UUID = "00000000-0000-0000-0000-000074657374"

EXCLUDED_DOCUMENT_TYPES = ('CAEX', 'PV', 'TST')

SEMANTIC_SEARCH_EXCLUDED_TYPES = EXCLUDED_DOCUMENT_TYPES + ('IFRLM',)

EDITABLE_DOCUMENT_STATES = ['draft', 'rejected']
DRAFT_STATE = 'draft'
SIGNED_DOCUMENT_STATE = 'signed'
SENT_TO_SIGN_STATE = 'sent_to_sign'
REJECTED_STATE = 'rejected'

CLOUDFLARE_URL_EXPIRATION_SECONDS = int(os.getenv("CF_R2_SIGN_EXPIRATION", "60"))
def _plural_es(cantidad: int, singular: str, plural: str) -> str:
    return f"{cantidad} {singular if cantidad == 1 else plural}"


CLOUDFLARE_URL_EXPIRATION = (
    _plural_es(CLOUDFLARE_URL_EXPIRATION_SECONDS // 60, "minuto", "minutos")
    if CLOUDFLARE_URL_EXPIRATION_SECONDS % 60 == 0
    else _plural_es(CLOUDFLARE_URL_EXPIRATION_SECONDS, "segundo", "segundos")
)


CASE_STATUSES = ['active', 'inactive', 'archived']
CASE_STATUS_ACTIVE = 'active'
CASE_STATUS_INACTIVE = 'inactive'
CASE_STATUS_ARCHIVED = 'archived'

MOVEMENT_TYPES = {
    'CREATION': 'creation',
    'TRANSFER': 'transfer',
    'ASSIGNMENT': 'assignment',
    'DOCUMENT_LINK': 'document_link',
    'SUBSANACION': 'subsanacion',
    'DOCUMENT_PROPOSAL': 'document_proposal',
    'DOCUMENT_PROPOSAL_REJECT': 'document_proposal_reject',
    'CITIZEN_SHARE': 'citizen_share',
    'CITIZEN_UNSHARE': 'citizen_unshare',
}

CASE_ACCESS_REASONS = ['ADMINSECTOR', 'ASSIGNEDSECTOR', 'VIEW']
ACCESS_REASON_ADMIN = 'ADMINSECTOR'
ACCESS_REASON_ASSIGNED = 'ASSIGNEDSECTOR'
ACCESS_REASON_VIEW = 'VIEW'

DATE_FILTER_OPTIONS = ['hoy', 'ayer', 'ultimos_7_dias', 'ultimos_30_dias']

DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

CASE_SEARCH_MIN_CHARS = 3


CASE_NOT_FOUND_ERROR = "Expediente no encontrado"
CASE_PERMISSION_DENIED_ERROR = "No tiene permisos para acceder a este expediente"
CASE_INVALID_STATUS_ERROR = "Estado de expediente inválido: {status}"
CASE_INVALID_DATE_FILTER_ERROR = "Filtro de fecha debe ser: {options}"
CASE_LIST_SUCCESS_MESSAGE = "Se encontraron {total} expedientes"
CASE_SUMMARY_SUCCESS_MESSAGE = "Estadísticas obtenidas correctamente"
CASE_NO_CASES_MESSAGE = "Sin expedientes encontrados"
CASE_NO_USER_SECTORS_ERROR = "Usuario no tiene sectores asignados"

DOCUMENT_TYPE_CAEX = "CAEX"
CASE_COVER_REFERENCE_TEMPLATE = "Creación {case_number}"
DEFAULT_CITY = "LATAM"
CASE_COVER_CREATED_SUCCESS = "Carátula creada exitosamente: {official_number}"
CASE_COVER_CREATION_ERROR = "Error creando carátula: {error}"

CACHE_TTL_TEMPLATES = 600
CACHE_TTL_COUNTS = 30
CACHE_TTL_SECTORS = 300
CACHE_TTL_DOC_TYPES = 600


CASE_DETAIL_SUCCESS_MESSAGE = "Detalle del expediente {case_number} obtenido correctamente"
CASE_DETAIL_ERROR = "Error obteniendo detalle del expediente: {error}"
CASE_NOT_FOUND_ERROR = "Expediente no encontrado"
USER_NOT_FOUND_ERROR = "Usuario no encontrado en el sistema"
USER_UNAUTHENTICATED_ERROR = "Usuario no autenticado"

MOVEMENTS_SUCCESS_MESSAGE = "Movimientos obtenidos exitosamente"
MOVEMENTS_ERROR = "Error obteniendo movimientos del expediente"
MOVEMENTS_ACCESS_DENIED = "No tiene permisos para ver los movimientos de este expediente"

DOCUMENTS_SUCCESS_MESSAGE = "Documentos obtenidos exitosamente"
DOCUMENTS_ERROR = "Error obteniendo documentos del expediente"
DOCUMENTS_ACCESS_DENIED = "No tiene permisos para ver los documentos de este expediente"

PERMISSIONS_SUCCESS_MESSAGE = "Permisos obtenidos exitosamente"
PERMISSIONS_ERROR = "Error obteniendo permisos del expediente"
PERMISSIONS_ACCESS_DENIED = "No tiene permisos para ver este expediente"

OWNERSHIP_LEVEL_OWNER = "owner"
OWNERSHIP_LEVEL_CREATOR = "creator"
OWNERSHIP_LEVEL_DEPARTMENT = "department_member"
OWNERSHIP_LEVEL_PARTICIPANT = "participant"

CASE_HISTORY_SUCCESS_MESSAGE = "Historial obtenido exitosamente"
CASE_HISTORY_ERROR = "Error obteniendo historial del expediente"

MOVEMENT_TYPE_CREATION = "creation"
MOVEMENT_TYPE_TRANSFER = "transfer"
MOVEMENT_TYPE_ASSIGNMENT = "assignment"
MOVEMENT_TYPE_STATUS_CHANGE = "status_change"
MOVEMENT_TYPE_DOCUMENT_LINK = "document_link"
MOVEMENT_TYPE_SUBSANACION = "subsanacion"
MOVEMENT_TYPE_DOCUMENT_PROPOSAL = "document_proposal"
MOVEMENT_TYPE_DOCUMENT_PROPOSAL_REJECT = "document_proposal_reject"
MOVEMENT_TYPE_ASSIGNMENT_CLOSE = "assignment_close"
MOVEMENT_TYPE_COMMENT = "comment"
MOVEMENT_TYPE_TASK = "task"
MOVEMENT_TYPE_CITIZEN_SHARE = "citizen_share"
MOVEMENT_TYPE_CITIZEN_UNSHARE = "citizen_unshare"
MOVEMENT_TYPE_CITIZEN_NOTIFY = "citizen_notify"

TRANSFER_SUCCESS_MESSAGE = "Expediente transferido exitosamente"
TRANSFER_ERROR = "Error en transferencia del expediente"
TRANSFER_USER_NOT_FOUND = "Usuario no encontrado"
TRANSFER_CASE_NOT_FOUND = "Expediente o sector destino no encontrado"
TRANSFER_PERMISSION_DENIED = "Solo el sector administrador actual puede transferir la propiedad del expediente"
TRANSFER_ADMIN_SECTOR_NOT_FOUND = "No se encontró el sector administrador del expediente"
TRANSFER_TARGET_SECTOR_NOT_FOUND = "Sector destino no encontrado o inactivo"
TRANSFER_ASSIGNED_USER_INVALID = "El usuario asignado no pertenece al sector destino"
TRANSFER_DUPLICATE_ASSIGNMENT = "El sector ya tiene una asignación activa para este expediente"
TRANSFER_DOCUMENT_CREATION_ERROR = "Error creando documento oficial"

ASSIGN_SUCCESS_MESSAGE = "Tarea asignada exitosamente"
ASSIGN_ERROR = "Error asignando tarea"

CLOSE_ASSIGNMENT_SUCCESS = "Asignación cerrada exitosamente"
CLOSE_ASSIGNMENT_ERROR = "Error cerrando asignación"
CLOSE_ASSIGNMENT_USER_NO_SECTORS = "Usuario no pertenece a ningún sector activo"
CLOSE_ASSIGNMENT_MOVEMENT_NOT_FOUND = "Movimiento no encontrado o no pertenece a este expediente"
CLOSE_ASSIGNMENT_WRONG_TYPE = "Solo se pueden cerrar movimientos de tipo 'assignment'"
CLOSE_ASSIGNMENT_ALREADY_CLOSED = "El movimiento ya fue cerrado"
CLOSE_ASSIGNMENT_PERMISSION_DENIED = "No tiene permisos para cerrar esta asignación. Debe pertenecer al sector administrador del expediente o al sector asignado."

AVAILABLE_SECTORS_SUCCESS = "Sectores disponibles obtenidos exitosamente"
AVAILABLE_SECTORS_ERROR = "Error obteniendo sectores disponibles"
AVAILABLE_SECTORS_NO_ACCESS = "No tiene permisos para ver este expediente"

SECTOR_USERS_SUCCESS = "Usuarios del sector obtenidos exitosamente"
SECTOR_USERS_ERROR = "Error obteniendo usuarios del sector"

TRANSFER_CLOSING_REASON = "Transferencia Realizada"

CASE_CREATED_SUCCESS_MESSAGE = "Expediente y carátula creados exitosamente: {case_number} - {cover_number}"
CASE_CREATION_ERROR = "Error creando expediente: {error}"
CASE_TEMPLATES_FOUND_MESSAGE = "Se encontraron {total} plantillas disponibles"
CASE_TEMPLATES_ERROR = "Error obteniendo plantillas: {error}"


DELETION_SUCCESS_MESSAGE = "Documento eliminado exitosamente"
DELETION_PERMISSION_DENIED = "Solo el creador puede eliminar el documento"
DELETION_INVALID_STATE_ERROR = "Documento en estado '{status}' no puede eliminarse"
DELETION_ALREADY_DELETED_ERROR = "El documento ya fue eliminado previamente"
DELETABLE_DOCUMENT_STATES = ['draft', 'rejected']
DELETION_PDF_CLEANUP_NOTE = "PDF cleanup is best-effort (soft-fail)"
DELETION_NO_PDF_NOTE = "No PDF to cleanup"


REJECTION_SUCCESS_MESSAGE = "Documento rechazado exitosamente"
REJECTION_DISPLAY_STATUS = "En edición"
REJECTION_ALREADY_REJECTED_ERROR = "El documento ya fue rechazado"
REJECTION_OFFICIAL_DOCUMENT_ERROR = "No se puede rechazar un documento completado"
REJECTION_USER_CREATOR_REASON = "Es el creador del documento"
REJECTION_USER_SIGNER_REASON = "Es firmante del documento"
REJECTION_USER_NOT_AUTHORIZED = "Usuario no es creador ni firmante del documento"
REJECTION_PDF_CLEANUP_NOTE = "PDF cleanup is best-effort (soft-fail)"
REJECTION_NO_PDF_NOTE = "No PDF to cleanup"


SAVE_SUCCESS_MESSAGE = "Documento guardado exitosamente"
SAVE_NO_CHANGES_ERROR = "No se proporcionaron cambios para guardar"


SIGNATURE_DOCUMENT_FINALIZED_MESSAGE = "Documento finalizado"
SIGNATURE_IN_PROCESS_MESSAGE = "El documento se encuentra en proceso de firmas."
SIGNATURE_ALREADY_SIGNED_MESSAGE = "Ya realizaste la firma de este documento. Actualmente se encuentra aguardando la firma de los demás usuarios. El último firmante numerará el documento."
SIGNATURE_NUMERATOR_WAITING_MESSAGE = "Estás asignado como numerador. Cuando finalice el proceso te notificaremos para realizar la firma final y la numeración del documento."
SIGNATURE_USER_NOT_AUTHORIZED_ERROR = "Usuario no tiene permisos para ver este documento. Debe ser firmante, creador o quien envió a firma."
SIGNATURE_USER_NOT_FOUND_ERROR = "Usuario no encontrado en el sistema"


START_SIGNING_SUCCESS_MESSAGE = "Proceso de firma iniciado exitosamente"
START_SIGNING_ONLY_CREATOR_ERROR = "Solo el creador puede iniciar el proceso de firma"

START_SIGNING_ALREADY_DONE_MESSAGE = (
    "El documento ya habia sido enviado a firma por vos: no se hizo nada nuevo"
)
START_SIGNING_EN_CURSO_ERROR = (
    "Ya hay un proceso de firma en curso para este documento (se esta generando "
    "el PDF). Espera unos segundos y consulta el estado del documento; no hace "
    "falta reintentar el envio."
)
START_SIGNING_INVALID_STATE_ERROR = "Documento no puede iniciarse para firma en su estado actual"
START_SIGNING_PDF_GENERATION_ERROR = "Error al generar PDF: no se obtuvo document_generate_id válido"
START_SIGNING_NO_SIGNERS_ERROR = "El documento debe tener al menos un firmante asignado"
START_SIGNING_NO_NUMERATOR_ERROR = "El documento debe tener un numerador asignado"


UNIFIED_DETAILS_DOCUMENT_NOT_FOUND_MESSAGE = "Documento no encontrado"
UNIFIED_DETAILS_STATE_NOT_SUPPORTED_MESSAGE = "No se pueden obtener detalles de documentos en estado '{status}'"
UNIFIED_DETAILS_USER_ID_REQUIRED_MESSAGE = "El parámetro 'user_id' es requerido para obtener detalles de un documento"
UNIFIED_DETAILS_ERROR_MESSAGE = "Error al obtener detalles del documento"

STATE_CATEGORY_MAP = {
    'draft': 'editing',
    'rejected': 'editing',
    'sent_to_sign': 'signing',
    'signed': 'signing'
}


ONBOARDING_AUTH_ID_REQUIRED_ERROR = "auth_id es requerido y debe ser texto"
ONBOARDING_AUTH_ID_MIN_LENGTH_ERROR = "auth_id debe tener al menos 5 caracteres"
ONBOARDING_EMAIL_REQUIRED_ERROR = "email es requerido y debe ser texto"
ONBOARDING_EMAIL_FORMAT_ERROR = "email debe tener formato válido"
ONBOARDING_FULL_NAME_REQUIRED_ERROR = "full_name es requerido y debe ser texto"
ONBOARDING_FULL_NAME_MIN_LENGTH_ERROR = "full_name debe tener al menos 2 caracteres"
ONBOARDING_FULL_NAME_MAX_LENGTH_ERROR = "full_name no puede exceder 100 caracteres"
ONBOARDING_AUTH_ID_IN_USE_ERROR = "Auth ID ya está en uso por otro usuario: {email}"
ONBOARDING_USER_DEACTIVATED_ERROR = "Usuario {email} está desactivado. Contacte al administrador."
ONBOARDING_ACTIVATION_ERROR = "Error al activar usuario {user_id}"
ONBOARDING_NO_SECTORS_ERROR = "No hay sectores activos disponibles para asignación"
ONBOARDING_NO_SEALS_ERROR = "No hay sellos activos disponibles para asignación"


PROFILE_USER_NOT_FOUND_ERROR = "Usuario no encontrado"
PROFILE_NO_FIELDS_TO_UPDATE_ERROR = "No se proporcionaron campos para actualizar"
PROFILE_UPDATE_FAILED_ERROR = "Usuario no encontrado o no se pudo actualizar"
PROFILE_GET_ERROR = "Error al obtener perfil: {error}"
PROFILE_UPDATE_ERROR = "Error al actualizar perfil: {error}"
PROFILE_SECTOR_NOT_FOUND_ERROR = "El sector {sector_id} no existe"
PROFILE_SECTOR_INACTIVE_ERROR = "El sector {sector_id} está inactivo"
PROFILE_INVALID_FULL_NAME_ERROR = "El nombre completo no puede estar vacío ni contener solo espacios"
PROFILE_FULL_NAME_TOO_LONG_ERROR = "El nombre completo no puede exceder {max_length} caracteres"


LINK_DOCUMENT_SUCCESS = "Documento vinculado exitosamente al expediente"
LINK_DOCUMENT_ERROR = "Error vinculando documento al expediente"
LINK_DOCUMENT_USER_NOT_FOUND = "Usuario no encontrado en el sistema"


PROPOSED_DOCUMENT_NOT_FOUND = "Documento propuesto no encontrado"
PROPOSED_DOCUMENT_ALREADY_PROCESSED = "La propuesta ya fue procesada anteriormente"
PROPOSED_DOCUMENT_NOT_SIGNED = "Solo se pueden vincular documentos oficiales (firmados). Estado actual: {status}"
PROPOSED_DOCUMENT_ACCEPT_SUCCESS = "Documento propuesto aceptado y vinculado exitosamente"
PROPOSED_DOCUMENT_REJECT_SUCCESS = "Documento propuesto rechazado exitosamente"
PROPOSED_DOCUMENT_REJECT_NO_PERMISSION = "No tiene permisos para rechazar documentos propuestos en este expediente"


PREPARE_ASSIGNMENT_SUCCESS = "Tienes acceso a"
PREPARE_ASSIGNMENT_ERROR = "Error preparando asignación"
PREPARE_ASSIGNMENT_NO_PERMISSION = "No tiene permisos sobre este expediente"
PREPARE_ASSIGNMENT_NO_SECTORS = "Sin permisos para asignar. No hay sectores disponibles en este municipio."


PREPARE_TRANSFER_SUCCESS = "OK"
PREPARE_TRANSFER_ERROR = "Error preparando transferencia"
PREPARE_TRANSFER_NO_PERMISSION = "No tiene permisos sobre este expediente"
PREPARE_TRANSFER_NO_SECTORS = "Sin permisos para transferir. No hay sectores disponibles en este municipio."
PREPARE_TRANSFER_NOT_ADMIN = "Solo el sector administrador puede transferir este expediente"


CASE_BY_NUMBER_SUCCESS = "Expediente encontrado: {case_number}"
CASE_BY_NUMBER_NOT_FOUND = "Expediente con número '{case_number}' no encontrado"
CASE_BY_NUMBER_ERROR = "Error buscando expediente por número"


SUBSANAR_SUCCESS = "Documento subsanado exitosamente"
SUBSANAR_ERROR = "Error subsanando documento"
SUBSANAR_SAME_DOCUMENT_ERROR = "El documento erróneo y el que justifica no pueden ser el mismo"


SEARCH_QUERY_REQUIRED_ERROR = "El término de búsqueda es requerido y debe ser texto"
SEARCH_QUERY_MIN_LENGTH_ERROR = "El término de búsqueda debe tener al menos {min_length} caracteres"
SEARCH_QUERY_MAX_LENGTH_ERROR = "El término de búsqueda no puede exceder {max_length} caracteres"
SEARCH_LIMIT_INVALID_ERROR = "El límite debe ser un número entero positivo"
SEARCH_LIMIT_MAX_ERROR = "El límite máximo es {max_limit} usuarios"
SEARCH_EMAIL_INVALID_FORMAT_ERROR = "El formato del email no es válido"

SEARCH_MIN_LENGTH = 2
SEARCH_MAX_LENGTH = 100
SEARCH_MAX_LIMIT = 100


MAX_TST_PER_RUN: int = int(os.getenv("MAX_TST_PER_RUN", "50"))

TST_THROTTLE_SECONDS: float = float(os.getenv("TST_THROTTLE_SECONDS", "1.0"))

TST_SWEEP_HOUR_1:   int = int(os.getenv("TST_SWEEP_HOUR_1",   "20"))
TST_SWEEP_MINUTE_1: int = int(os.getenv("TST_SWEEP_MINUTE_1", "0"))
TST_SWEEP_HOUR_2:   int = int(os.getenv("TST_SWEEP_HOUR_2",   "2"))
TST_SWEEP_MINUTE_2: int = int(os.getenv("TST_SWEEP_MINUTE_2", "30"))


RECONCILE_ENABLED: bool = os.getenv("RECONCILE_ENABLED", "false").strip().lower() not in (
    "0", "false", "no",
)

MAX_RECONCILE_PER_RUN: int = int(os.getenv("MAX_RECONCILE_PER_RUN", "50"))

RECONCILE_TS_SWEEP_HOUR_1:   int = int(os.getenv("RECONCILE_TS_SWEEP_HOUR_1",   "19"))
RECONCILE_TS_SWEEP_MINUTE_1: int = int(os.getenv("RECONCILE_TS_SWEEP_MINUTE_1", "0"))
RECONCILE_TS_SWEEP_HOUR_2:   int = int(os.getenv("RECONCILE_TS_SWEEP_HOUR_2",   "2"))
RECONCILE_TS_SWEEP_MINUTE_2: int = int(os.getenv("RECONCILE_TS_SWEEP_MINUTE_2", "0"))

SYSTEM_RECONCILE_USER_UUID = "00000000-0000-0000-0000-000000222222"

SYSTEM_CITIZEN_DTS_ACTOR_UUID = "00000000-0000-0000-0000-000000130130"

CONFIRMING_ORPHAN_GRACE_MINUTES: float = float(
    os.getenv("SWEEPER_CONFIRMING_ORPHAN_GRACE_MINUTES", "10")
)


CB_FAILURE_THRESHOLD: int = int(os.getenv("NOTARY_CB_THRESHOLD", "5"))

CB_WINDOW_SECONDS: int = int(os.getenv("NOTARY_CB_WINDOW", "30"))

CB_COOLDOWN_SECONDS: int = int(os.getenv("NOTARY_CB_COOLDOWN", "30"))


TSA_DEFERRED_SEAL_ENABLED: bool = os.getenv("TSA_DEFERRED_SEAL_ENABLED", "false").strip().lower() in ("1", "true", "yes", "on")


DTS_MAX_PER_MINUTE: int = int(os.getenv("DTS_MAX_PER_MINUTE", "60"))

DTS_DEGRADED_DIVISOR: int = int(os.getenv("DTS_DEGRADED_DIVISOR", "4"))

SPECIAL_TSA_RESERVED_PCT: float = float(os.getenv("SPECIAL_TSA_RESERVED_PCT", "0.05"))

_SIGN_TSA_BACKOFF_DEFAULT: list[int] = [1, 2, 3, 5, 10, 20, 40, 60, 90, 120, 120]


def _parse_sign_tsa_backoff_override() -> list[int] | None:
    raw = os.getenv("SIGN_TSA_BACKOFF_MINUTES_OVERRIDE", "").strip()
    if not raw:
        return None
    try:
        parsed = [int(x.strip()) for x in raw.split(",") if x.strip()]
        return parsed or None
    except ValueError:
        return None


SIGN_TSA_BACKOFF_MINUTES: list[int] = _parse_sign_tsa_backoff_override() or _SIGN_TSA_BACKOFF_DEFAULT
SIGN_TSA_MAX_ATTEMPTS: int = len(SIGN_TSA_BACKOFF_MINUTES)

PUBLISH_PUBLIC_MAX_RETRIES: int = int(os.getenv("PUBLISH_PUBLIC_MAX_RETRIES", "3"))


DIGITAL_SIGNATURE_SESSION_TTL_SECONDS: int = int(
    os.getenv("DIGITAL_SIGNATURE_SESSION_TTL_SECONDS", "240")
)

DIGITAL_SIGNATURE_STORAGE_MAX_PER_MINUTE: int = int(
    os.getenv("DIGITAL_SIGNATURE_STORAGE_MAX_PER_MINUTE", "120")
)
DIGITAL_SIGNATURE_STORAGE_MAX_MISSES_PER_MINUTE: int = int(
    os.getenv("DIGITAL_SIGNATURE_STORAGE_MAX_MISSES_PER_MINUTE", "10")
)

FIRMADOR_VERSION_MINIMA: str = os.getenv("FIRMADOR_VERSION_MINIMA", "1.3.0")


def escri_worker_enabled() -> bool:
    return os.getenv("ESCRI_WORKER_ENABLED", "true").strip().lower() not in (
        "0", "false", "no", "off"
    )


ESCRI_CONCURRENCY: int = int(os.getenv("ESCRI_CONCURRENCY", "2"))

ESCRI_QUEUE_MAX_PER_TENANT: int = int(os.getenv("ESCRI_QUEUE_MAX_PER_TENANT", "30"))

ESCRI_QUEUE_MAX_GLOBAL: int = int(os.getenv("ESCRI_QUEUE_MAX_GLOBAL", "150"))

ESCRI_QUEUE_DEGRADED_THRESHOLD: int = int(os.getenv("ESCRI_QUEUE_DEGRADED_THRESHOLD", "100"))


ESCRI_QUEUE_SLA_SECONDS: int = int(os.getenv("ESCRI_QUEUE_SLA_SECONDS", "300"))

ESCRI_QUEUE_SIGNALS_CACHE_SECONDS: int = int(os.getenv("ESCRI_QUEUE_SIGNALS_CACHE_SECONDS", "8"))

ESCRI_QUEUE_DEAD_WORKER_MIN_AGE_SECONDS: int = int(os.getenv("ESCRI_QUEUE_DEAD_WORKER_MIN_AGE_SECONDS", "90"))

ESCRI_QUEUE_ALERT_COOLDOWN_SECONDS: int = int(os.getenv("ESCRI_QUEUE_ALERT_COOLDOWN_SECONDS", "1800"))

GUNICORN_WORKERS: int = int(os.getenv("GUNICORN_WORKERS", "2"))

ESCRI_JOB_SECONDS_ESTIMATE: float = float(os.getenv("ESCRI_JOB_SECONDS_ESTIMATE", "8"))

ESCRI_GUARD_MAX_ATTEMPTS: int = int(os.getenv("ESCRI_GUARD_MAX_ATTEMPTS", "5"))


def escri_queue_drain_estimate_minutes() -> float:
    concurrency = max(1, ESCRI_CONCURRENCY * GUNICORN_WORKERS)
    return (ESCRI_QUEUE_MAX_GLOBAL * ESCRI_JOB_SECONDS_ESTIMATE) / concurrency / 60.0


def check_escri_ttl_coherence() -> tuple[bool, str]:
    ttl_minutes = int(os.getenv("ESCRI_PENDING_TTL_MINUTES", "30"))
    drain_minutes = escri_queue_drain_estimate_minutes()
    concurrency = ESCRI_CONCURRENCY * GUNICORN_WORKERS
    detalle = (
        f"tope global={ESCRI_QUEUE_MAX_GLOBAL} jobs, "
        f"concurrencia real={ESCRI_CONCURRENCY}x{GUNICORN_WORKERS}={concurrency}, "
        f"~{ESCRI_JOB_SECONDS_ESTIMATE}s por job → drenar la cola llena "
        f"tarda ~{drain_minutes:.1f} min contra un TTL de {ttl_minutes} min"
    )
    if drain_minutes >= ttl_minutes:
        return False, (
            f"INCOHERENTE: {detalle}. Con esta configuración, una cola llena "
            f"expira antes de drenarse y el sweeper CANCELA los números de "
            f"firmas que estaban esperando su turno. Bajar ESCRI_QUEUE_MAX_GLOBAL, "
            f"subir la concurrencia o subir ESCRI_PENDING_TTL_MINUTES."
        )
    return True, f"OK: {detalle}"


DEFAULT_LOGO_URL = os.getenv("DEFAULT_LOGO_URL", "")
DEFAULT_ISOLOGO_URL = os.getenv("DEFAULT_ISOLOGO_URL", "")


MAX_EMBEDDED_FILE_SIZE = 50 * 1024 * 1024

MAX_SIGNABLE_PDF_SIZE = 64 * 1024 * 1024

MAX_TOTAL_EMBEDDED_SIZE = 60 * 1024 * 1024

EMBEDDED_FILE_ALLOWED_EXTENSIONS = {
    "pdf", "xls", "xlsx", "doc", "docx", "odt", "ods",
    "csv", "txt", "png", "jpg", "jpeg", "dxf",
}

MAX_EMBEDDED_FILES_PER_DOCUMENT = 10


EMBEDDED_FILE_NOT_EDITABLE_ERROR = "El documento no está en un estado que permita adjuntar archivos"
EMBEDDED_FILE_NOT_CREATOR_ERROR = "Solo el creador del documento puede adjuntar archivos"
EMBEDDED_FILE_TYPE_NOT_ALLOWED_ERROR = "Este tipo de documento no admite archivos adjuntos embebidos"
EMBEDDED_FILE_NOTA_MEMO_NOT_SUPPORTED_ERROR = "El tipo NOTA/MEMO no soporta adjuntos embebidos en esta versión"
EMBEDDED_FILE_MAX_COUNT_ERROR = "El documento ya tiene el máximo de {max_count} archivos adjuntos permitidos"
EMBEDDED_FILE_TOTAL_SIZE_ERROR = "La suma de los adjuntos supera el máximo permitido ({max_mb}MB)"
EMBEDDED_FILE_INDIVIDUAL_SIZE_ERROR = "El archivo supera el tamaño máximo permitido ({max_mb}MB)"
EMBEDDED_FILE_INVALID_TYPE_ERROR = "El archivo no es válido o no corresponde a la extensión declarada"
EMBEDDED_FILE_NOT_FOUND_ERROR = "Archivo adjunto no encontrado"
EMBEDDED_FILE_UPLOAD_SUCCESS = "Archivo adjunto subido exitosamente"
EMBEDDED_FILE_DELETE_SUCCESS = "Archivo adjunto eliminado exitosamente"
EMBEDDED_FILE_EMBED_ERROR = "No se pudieron recuperar los archivos adjuntos para incluirlos en la firma. Intente nuevamente."


ESCRI_SHUTDOWN_REQUEUE_TIMEOUT_SEC: float = float(os.getenv("ESCRI_SHUTDOWN_REQUEUE_TIMEOUT_SEC", "3"))

ESCRI_SHUTDOWN_GRACE_SECONDS: float = float(os.getenv("ESCRI_SHUTDOWN_GRACE_SECONDS", "8"))

ESCRI_HEARTBEAT_SEC: int = int(os.getenv("ESCRI_HEARTBEAT_SEC", "120"))


SWEEPER_SCHEMAS_CACHE_TTL_SEC: int = int(os.getenv("SWEEPER_SCHEMAS_CACHE_TTL_SEC", "600"))

