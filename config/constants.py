"""
Archivo para centralizar constantes utilizadas en la aplicación.
"""

# Tipos de documentos excluidos en las consultas
EXCLUDED_DOCUMENT_TYPES = ('CAEX', 'PV')

# Estados de documentos
EDITABLE_DOCUMENT_STATES = ['draft', 'rejected']
DRAFT_STATE = 'draft'
SIGNED_DOCUMENT_STATE = 'signed'
SENT_TO_SIGN_STATE = 'sent_to_sign'
REJECTED_STATE = 'rejected'

# Configuración de URLs de Cloudflare
CLOUDFLARE_URL_EXPIRATION = "15 minutes"

# ============================================================================
# CASE/EXPEDIENTE CONSTANTS
# ============================================================================

# Estados de casos/expedientes
CASE_STATUSES = ['active', 'inactive', 'archived']
CASE_STATUS_ACTIVE = 'active'
CASE_STATUS_INACTIVE = 'inactive'
CASE_STATUS_ARCHIVED = 'archived'

# Tipos de movimientos de casos
MOVEMENT_TYPES = {
    'CREATION': 'creation',
    'TRANSFER': 'transfer',
    'ASSIGNMENT': 'assignment',
    'DOCUMENT_LINK': 'document_link',
    'SUBSANACION': 'subsanacion',
    'DOCUMENT_PROPOSAL': 'document_proposal',
    'DOCUMENT_PROPOSAL_REJECT': 'document_proposal_reject'
}

# Razones de acceso a expedientes
CASE_ACCESS_REASONS = ['ADMINSECTOR', 'ASSIGNEDSECTOR', 'VIEW']
ACCESS_REASON_ADMIN = 'ADMINSECTOR'
ACCESS_REASON_ASSIGNED = 'ASSIGNEDSECTOR'
ACCESS_REASON_VIEW = 'VIEW'

# Filtros de fecha predefinidos
DATE_FILTER_OPTIONS = ['hoy', 'ayer', 'ultimos_7_dias', 'ultimos_30_dias']

# Paginación por defecto
DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

# ============================================================================
# CASE MESSAGES
# ============================================================================

CASE_NOT_FOUND_ERROR = "Expediente no encontrado"
CASE_PERMISSION_DENIED_ERROR = "No tiene permisos para acceder a este expediente"
CASE_INVALID_STATUS_ERROR = "Estado de expediente inválido: {status}"
CASE_INVALID_DATE_FILTER_ERROR = "Filtro de fecha debe ser: {options}"
CASE_LIST_SUCCESS_MESSAGE = "Se encontraron {total} expedientes"
CASE_SUMMARY_SUCCESS_MESSAGE = "Estadísticas obtenidas correctamente"
CASE_NO_CASES_MESSAGE = "Sin expedientes encontrados"
CASE_NO_USER_SECTORS_ERROR = "Usuario no tiene sectores asignados"

# Case Cover/Carátula
DOCUMENT_TYPE_CAEX = "CAEX"
CASE_COVER_REFERENCE_TEMPLATE = "Creación {case_number}"
DEFAULT_CITY = "LATAM"
CASE_COVER_CREATED_SUCCESS = "Carátula creada exitosamente: {official_number}"
CASE_COVER_CREATION_ERROR = "Error creando carátula: {error}"

# Cache TTL
CACHE_TTL_TEMPLATES = 600  # 10 minutos
CACHE_TTL_COUNTS = 30  # 30 segundos (conteos de listados - no necesitan ser exactos)
CACHE_TTL_SECTORS = 300  # 5 minutos (sectores/departamentos, datos cuasi-estáticos)
CACHE_TTL_DOC_TYPES = 600  # 10 minutos (tipos de documentos)

# Cache Keys

# Case Detail Messages
CASE_DETAIL_SUCCESS_MESSAGE = "Detalle del expediente {case_number} obtenido correctamente"
CASE_DETAIL_ERROR = "Error obteniendo detalle del expediente: {error}"
CASE_NOT_FOUND_ERROR = "Expediente no encontrado"
USER_NOT_FOUND_ERROR = "Usuario no encontrado en el sistema"
USER_UNAUTHENTICATED_ERROR = "Usuario no autenticado"

# Case movements messages
MOVEMENTS_SUCCESS_MESSAGE = "Movimientos obtenidos exitosamente"
MOVEMENTS_ERROR = "Error obteniendo movimientos del expediente"
MOVEMENTS_ACCESS_DENIED = "No tiene permisos para ver los movimientos de este expediente"

# Case documents messages
DOCUMENTS_SUCCESS_MESSAGE = "Documentos obtenidos exitosamente"
DOCUMENTS_ERROR = "Error obteniendo documentos del expediente"
DOCUMENTS_ACCESS_DENIED = "No tiene permisos para ver los documentos de este expediente"

# Case permissions messages
PERMISSIONS_SUCCESS_MESSAGE = "Permisos obtenidos exitosamente"
PERMISSIONS_ERROR = "Error obteniendo permisos del expediente"
PERMISSIONS_ACCESS_DENIED = "No tiene permisos para ver este expediente"

# Permission ownership levels
OWNERSHIP_LEVEL_OWNER = "owner"
OWNERSHIP_LEVEL_CREATOR = "creator"
OWNERSHIP_LEVEL_DEPARTMENT = "department_member"
OWNERSHIP_LEVEL_PARTICIPANT = "participant"

# Case status
CASE_STATUS_ACTIVE = "active"

# Case history messages
CASE_HISTORY_SUCCESS_MESSAGE = "Historial obtenido exitosamente"
CASE_HISTORY_ERROR = "Error obteniendo historial del expediente"

# Movement types for formatting
MOVEMENT_TYPE_CREATION = "creation"
MOVEMENT_TYPE_TRANSFER = "transfer"
MOVEMENT_TYPE_ASSIGNMENT = "assignment"
MOVEMENT_TYPE_STATUS_CHANGE = "status_change"
MOVEMENT_TYPE_DOCUMENT_LINK = "document_link"
MOVEMENT_TYPE_SUBSANACION = "subsanacion"
MOVEMENT_TYPE_DOCUMENT_PROPOSAL = "document_proposal"
MOVEMENT_TYPE_DOCUMENT_PROPOSAL_REJECT = "document_proposal_reject"
MOVEMENT_TYPE_ASSIGNMENT_CLOSE = "assignment_close"

# Transfer case messages
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

# Assign task messages
ASSIGN_SUCCESS_MESSAGE = "Tarea asignada exitosamente"
ASSIGN_ERROR = "Error asignando tarea"

# Close assignment messages
CLOSE_ASSIGNMENT_SUCCESS = "Asignación cerrada exitosamente"
CLOSE_ASSIGNMENT_ERROR = "Error cerrando asignación"
CLOSE_ASSIGNMENT_USER_NO_SECTORS = "Usuario no pertenece a ningún sector activo"
CLOSE_ASSIGNMENT_MOVEMENT_NOT_FOUND = "Movimiento no encontrado o no pertenece a este expediente"
CLOSE_ASSIGNMENT_WRONG_TYPE = "Solo se pueden cerrar movimientos de tipo 'assignment'"
CLOSE_ASSIGNMENT_ALREADY_CLOSED = "El movimiento ya fue cerrado"
CLOSE_ASSIGNMENT_PERMISSION_DENIED = "No tiene permisos para cerrar esta asignación. Debe pertenecer al sector administrador del expediente o al sector asignado."

# Available sectors messages  
AVAILABLE_SECTORS_SUCCESS = "Sectores disponibles obtenidos exitosamente"
AVAILABLE_SECTORS_ERROR = "Error obteniendo sectores disponibles"
AVAILABLE_SECTORS_NO_ACCESS = "No tiene permisos para ver este expediente"

# Sector users messages
SECTOR_USERS_SUCCESS = "Usuarios del sector obtenidos exitosamente"
SECTOR_USERS_ERROR = "Error obteniendo usuarios del sector"

# Transfer closing reason
TRANSFER_CLOSING_REASON = "Transferencia Realizada"

# Create case messages
CASE_CREATED_SUCCESS_MESSAGE = "Expediente y carátula creados exitosamente: {case_number} - {cover_number}"
CASE_CREATION_ERROR = "Error creando expediente: {error}"
CASE_TEMPLATES_FOUND_MESSAGE = "Se encontraron {total} plantillas disponibles"
CASE_TEMPLATES_ERROR = "Error obteniendo plantillas: {error}"

# ============================================================================
# DELETION MESSAGES
# ============================================================================

DELETION_SUCCESS_MESSAGE = "Documento eliminado exitosamente"
DELETION_PERMISSION_DENIED = "Solo el creador puede eliminar el documento"
DELETION_INVALID_STATE_ERROR = "Documento en estado '{status}' no puede eliminarse"
DELETION_ALREADY_DELETED_ERROR = "El documento ya fue eliminado previamente"
DELETABLE_DOCUMENT_STATES = ['draft', 'rejected']
DELETION_PDF_CLEANUP_NOTE = "PDF cleanup is best-effort (soft-fail)"
DELETION_NO_PDF_NOTE = "No PDF to cleanup"

# ============================================================================
# REJECTION MESSAGES
# ============================================================================

REJECTION_SUCCESS_MESSAGE = "Documento rechazado exitosamente"
REJECTION_DISPLAY_STATUS = "En edición"
REJECTION_ALREADY_REJECTED_ERROR = "El documento ya fue rechazado"
REJECTION_OFFICIAL_DOCUMENT_ERROR = "No se puede rechazar un documento completado"
REJECTION_USER_CREATOR_REASON = "Es el creador del documento"
REJECTION_USER_SIGNER_REASON = "Es firmante del documento"
REJECTION_USER_NOT_AUTHORIZED = "Usuario no es creador ni firmante del documento"
REJECTION_PDF_CLEANUP_NOTE = "PDF cleanup is best-effort (soft-fail)"
REJECTION_NO_PDF_NOTE = "No PDF to cleanup"

# ============================================================================
# SAVE/EDITING MESSAGES
# ============================================================================

SAVE_SUCCESS_MESSAGE = "Documento guardado exitosamente"
SAVE_NO_CHANGES_ERROR = "No se proporcionaron cambios para guardar"

# ============================================================================
# SIGNATURE DETAILS MESSAGES
# ============================================================================

SIGNATURE_DOCUMENT_FINALIZED_MESSAGE = "Documento finalizado"
SIGNATURE_IN_PROCESS_MESSAGE = "El documento se encuentra en proceso de firmas."
SIGNATURE_ALREADY_SIGNED_MESSAGE = "Ya realizaste la firma de este documento. Actualmente se encuentra aguardando la firma de los demás usuarios. El último firmante numerará el documento."
SIGNATURE_NUMERATOR_WAITING_MESSAGE = "Estás asignado como numerador. Cuando finalice el proceso te notificaremos para realizar la firma final y la numeración del documento."
SIGNATURE_USER_NOT_AUTHORIZED_ERROR = "Usuario no tiene permisos para ver este documento. Debe ser firmante, creador o quien envió a firma."
SIGNATURE_USER_NOT_FOUND_ERROR = "Usuario no encontrado en el sistema"

# ============================================================================
# START SIGNING MESSAGES
# ============================================================================

START_SIGNING_SUCCESS_MESSAGE = "Proceso de firma iniciado exitosamente"
START_SIGNING_ONLY_CREATOR_ERROR = "Solo el creador puede iniciar el proceso de firma"
START_SIGNING_INVALID_STATE_ERROR = "Documento no puede iniciarse para firma en su estado actual"
START_SIGNING_PDF_GENERATION_ERROR = "Error al generar PDF: Legal Orchestrator no devolvió document_generate_id válido"
START_SIGNING_NO_SIGNERS_ERROR = "El documento debe tener al menos un firmante asignado"
START_SIGNING_NO_NUMERATOR_ERROR = "El documento debe tener un numerador asignado"

# ============================================================================
# UNIFIED DETAILS MESSAGES
# ============================================================================

UNIFIED_DETAILS_DOCUMENT_NOT_FOUND_MESSAGE = "Documento no encontrado"
UNIFIED_DETAILS_STATE_NOT_SUPPORTED_MESSAGE = "No se pueden obtener detalles de documentos en estado '{status}'"
UNIFIED_DETAILS_USER_ID_REQUIRED_MESSAGE = "El parámetro 'user_id' es requerido para documentos en proceso de firma"
UNIFIED_DETAILS_ERROR_MESSAGE = "Error al obtener detalles del documento"

# UNIFIED DETAILS STATE MAPPING
# Mapeo de estados de documento a categorías de servicio
STATE_CATEGORY_MAP = {
    'draft': 'editing',
    'rejected': 'editing',
    'sent_to_sign': 'signing',
    'signed': 'signing'
}

# ============================================================================
# ONBOARDING MESSAGES
# ============================================================================

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

# ============================================================================
# PROFILE MESSAGES
# ============================================================================

PROFILE_USER_NOT_FOUND_ERROR = "Usuario no encontrado"
PROFILE_NO_FIELDS_TO_UPDATE_ERROR = "No se proporcionaron campos para actualizar"
PROFILE_UPDATE_FAILED_ERROR = "Usuario no encontrado o no se pudo actualizar"
PROFILE_GET_ERROR = "Error al obtener perfil: {error}"
PROFILE_UPDATE_ERROR = "Error al actualizar perfil: {error}"
PROFILE_SECTOR_NOT_FOUND_ERROR = "El sector {sector_id} no existe"
PROFILE_SECTOR_INACTIVE_ERROR = "El sector {sector_id} está inactivo"
PROFILE_INVALID_FULL_NAME_ERROR = "El nombre completo no puede estar vacío ni contener solo espacios"
PROFILE_FULL_NAME_TOO_LONG_ERROR = "El nombre completo no puede exceder {max_length} caracteres"

# ============================================================================
# LINK DOCUMENT MESSAGES
# ============================================================================

LINK_DOCUMENT_SUCCESS = "Documento vinculado exitosamente al expediente"
LINK_DOCUMENT_ERROR = "Error vinculando documento al expediente"
LINK_DOCUMENT_USER_NOT_FOUND = "Usuario no encontrado en el sistema"

# ============================================================================
# PROPOSED DOCUMENT ACTIONS
# ============================================================================

PROPOSED_DOCUMENT_NOT_FOUND = "Documento propuesto no encontrado"
PROPOSED_DOCUMENT_ALREADY_PROCESSED = "La propuesta ya fue procesada anteriormente"
PROPOSED_DOCUMENT_NOT_SIGNED = "Solo se pueden vincular documentos oficiales (firmados). Estado actual: {status}"
PROPOSED_DOCUMENT_ACCEPT_SUCCESS = "Documento propuesto aceptado y vinculado exitosamente"
PROPOSED_DOCUMENT_REJECT_SUCCESS = "Documento propuesto rechazado exitosamente"
PROPOSED_DOCUMENT_REJECT_NO_PERMISSION = "No tiene permisos para rechazar documentos propuestos en este expediente"

# ============================================================================
# PREPARE ASSIGNMENT MESSAGES
# ============================================================================

PREPARE_ASSIGNMENT_SUCCESS = "Tienes acceso a"
PREPARE_ASSIGNMENT_ERROR = "Error preparando asignación"
PREPARE_ASSIGNMENT_NO_PERMISSION = "No tiene permisos sobre este expediente"
PREPARE_ASSIGNMENT_NO_SECTORS = "Sin permisos para asignar. No hay sectores disponibles en este municipio."

# ============================================================================
# PREPARE TRANSFER MESSAGES
# ============================================================================

PREPARE_TRANSFER_SUCCESS = "OK"
PREPARE_TRANSFER_ERROR = "Error preparando transferencia"
PREPARE_TRANSFER_NO_PERMISSION = "No tiene permisos sobre este expediente"
PREPARE_TRANSFER_NO_SECTORS = "Sin permisos para transferir. No hay sectores disponibles en este municipio."
PREPARE_TRANSFER_NOT_ADMIN = "Solo el sector administrador puede transferir este expediente"

# ============================================================================
# CASE BY NUMBER MESSAGES
# ============================================================================

CASE_BY_NUMBER_SUCCESS = "Expediente encontrado: {case_number}"
CASE_BY_NUMBER_NOT_FOUND = "Expediente con número '{case_number}' no encontrado"
CASE_BY_NUMBER_ERROR = "Error buscando expediente por número"

# ============================================================================
# SUBSANAR DOCUMENT MESSAGES
# ============================================================================

SUBSANAR_SUCCESS = "Documento subsanado exitosamente"
SUBSANAR_ERROR = "Error subsanando documento"
SUBSANAR_SAME_DOCUMENT_ERROR = "El documento erróneo y el que justifica no pueden ser el mismo"

# ============================================================================
# SEARCH USERS MESSAGES
# ============================================================================

SEARCH_QUERY_REQUIRED_ERROR = "El término de búsqueda es requerido y debe ser texto"
SEARCH_QUERY_MIN_LENGTH_ERROR = "El término de búsqueda debe tener al menos {min_length} caracteres"
SEARCH_QUERY_MAX_LENGTH_ERROR = "El término de búsqueda no puede exceder {max_length} caracteres"
SEARCH_LIMIT_INVALID_ERROR = "El límite debe ser un número entero positivo"
SEARCH_LIMIT_MAX_ERROR = "El límite máximo es {max_limit} usuarios"
SEARCH_EMAIL_INVALID_FORMAT_ERROR = "El formato del email no es válido"

# Search configuration constants
SEARCH_MIN_LENGTH = 2
SEARCH_MAX_LENGTH = 100
SEARCH_MAX_LIMIT = 100

# ============================================================================
# LOGOS DEFAULT (R2 público - bucket gdi-assets)
# ============================================================================

DEFAULT_LOGO_URL = "https://pub-0b5b632ca7bd4f45b3ced5507e755b3a.r2.dev/500x150.png"
DEFAULT_ISOLOGO_URL = "https://pub-0b5b632ca7bd4f45b3ced5507e755b3a.r2.dev/150x150.png"

