"""
Servicios para la gestión de documentos que contienen la lógica de negocio.
Estos servicios implementan las consultas a la base de datos y la manipulación de datos.
"""

from typing import List, Dict, Any, Optional
from database import get_conn
from datetime import datetime, timedelta, timezone
import json
import logging
import uuid
import os
import unicodedata
from shared.logging import get_logger

logger = get_logger(__name__)


def remove_accents(text: str) -> str:
    """Remueve acentos/tildes de un texto para búsqueda."""
    if not text:
        return ""
    # NFD descompone caracteres acentuados, luego filtramos los diacríticos
    return ''.join(
        c for c in unicodedata.normalize('NFD', text)
        if unicodedata.category(c) != 'Mn'
    )


def _get_display_status_priority(display_status: str) -> int:
    """Prioridad de ordenamiento por estado de documento.

    Orden:
    1. Firmar ahora - Requiere acción inmediata del usuario
    2. En edición - Borradores del usuario
    3. En proceso de firma - Esperando otros firmantes
    4. Firmado - Documentos completados
    """
    priority = {
        "Firmar ahora": 1,
        "En edición": 2,
        "En proceso de firma": 3,
        "Firmado": 4
    }
    return priority.get(display_status, 5)


async def generate_final_document_pdf(document_id: str, document_data: Dict[str, Any], signers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Genera el PDF final del documento llamando a la API externa de generación.

    Args:
        document_id: UUID del documento
        document_data: Datos del documento (referencia, contenido, etc.)
        signers: Lista de firmantes del documento

    Returns:
        Diccionario con el document_generate_id y metadata del archivo generado
    """
    # URL de la API de generación PDF (configurable por environment variable)
    pdf_api_url = os.getenv('PDF_GENERATION_API_URL')

    if not pdf_api_url:
        # Modo MOCK - Simular generación exitosa
        logger.info(f"MODO MOCK: Simulando generación de PDF para documento {document_id}")

        # Generar un UUID mock para simular el file_id devuelto por la API
        mock_document_generate_id = str(uuid.uuid4())

        logger.info(f"Documento: {document_data.get('reference', 'Sin referencia')}")
        logger.info(f"Firmantes: {len(signers)}")
        logger.info(f"Mock document_generate_id: {mock_document_generate_id}")

        return {
            "status": "success",
            "document_generate_id": mock_document_generate_id,
            "message": "PDF generado exitosamente (MOCK)",
            "file_size": 1024768,  # Tamaño mock en bytes
            "generated_at": datetime.now().isoformat()
        }

    # Modo REAL - Llamar a la API externa
    try:
        import httpx

        # Preparar datos para enviar a la API
        api_payload = {
            "document_id": document_id,
            "reference": document_data.get('reference'),
            "content": document_data.get('content'),
            "document_type": document_data.get('document_type_name'),
            "signers": [
                {
                    "user_id": signer['user_id'],
                    "full_name": signer['full_name'],
                    "is_numerator": signer['is_numerator']
                }
                for signer in signers
            ]
        }

        logger.info(f"Llamando API de generación PDF: {pdf_api_url}")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{pdf_api_url}/generate-final-pdf",
                json=api_payload,
                timeout=30.0
            )

            if response.status_code == 200:
                result = response.json()
                logger.info(f"PDF generado exitosamente: {result.get('document_generate_id')}")
                return result
            else:
                logger.error(f"Error en API de generación: {response.status_code} - {response.text}")
                raise Exception(f"Error en API de generación PDF: {response.status_code}")

    except ImportError:
        logger.warning("httpx no está instalado, usando modo MOCK")
        # Fallback a mock si httpx no está disponible
        mock_document_generate_id = str(uuid.uuid4())
        return {
            "status": "success",
            "document_generate_id": mock_document_generate_id,
            "message": "PDF generado exitosamente (MOCK - httpx no disponible)"
        }
    except Exception as e:
        logger.error(f"Error al generar PDF: {e}")
        # En caso de error, usar mock como fallback
        mock_document_generate_id = str(uuid.uuid4())
        return {
            "status": "fallback",
            "document_generate_id": mock_document_generate_id,
            "message": f"PDF generado en modo fallback debido a error: {str(e)}"
        }

# Función movida a services/documents/states.py para evitar duplicación
from services.documents.catalog.states import get_display_state_name

def map_display_status(status: str, rol_usuario: str, usuario_ya_firmo: bool,
                       todos_firmantes_comunes_firmaron: bool, usuario_es_firmante: bool = False, document_id: str = None) -> str:
    """
    Transforma el estado de base de datos a un estado visual para el frontend
    basado en el rol del usuario y el estado actual del documento.

    Args:
        status: Estado actual del documento en base de datos
        rol_usuario: Rol del usuario (creador, firmante, numerador, otro)
        usuario_ya_firmo: Si el usuario ya firmó el documento
        todos_firmantes_comunes_firmaron: Si todos los firmantes comunes ya firmaron
        usuario_es_firmante: Si el usuario también es firmante (independiente del rol)

    Returns:
        Estado para mostrar en el frontend
    """
    # NUEVA LÓGICA SIMPLIFICADA: Usar misma lógica que signature-details

    # Para documentos firmados
    if status == "signed":
        state_code = "SIGNED"

    # Para documentos rechazados
    elif status == "rejected":
        state_code = "EDITING"

    # Para documentos en borrador
    elif status == "draft":
        state_code = "EDITING"

    # Para documentos enviados a firmar - LÓGICA PRINCIPAL
    elif status == "sent_to_sign":
        # Si el usuario es firmante y no ha firmado
        if usuario_es_firmante and not usuario_ya_firmo:
            if rol_usuario == "numerador":
                # Numerador: solo puede firmar si todos los firmantes comunes ya firmaron
                if todos_firmantes_comunes_firmaron:
                    state_code = "SIGN_NOW"
                else:
                    state_code = "SIGNING_PROCESS"
            else:
                # Firmante común o creador-firmante: siempre puede firmar si no ha firmado
                state_code = "SIGN_NOW"
        else:
            # Ya firmó o no es firmante
            state_code = "SIGNING_PROCESS"

    # Default para cualquier otro estado
    else:
        state_code = "SIGNING_PROCESS"

    # Obtener el nombre del estado desde la base de datos - simulado
    if state_code == "SIGN_NOW":
        return "Firmar ahora"
    elif state_code == "SIGNING_PROCESS":
        return "En proceso de firma"
    elif state_code == "SIGNED":
        return "Firmado"
    elif state_code == "EDITING":
        return "En edición"
    else:
        return "Estado desconocido"

async def get_user_documents(
    user_id: str,
    status_filter: Optional[str] = None,
    date_filter: Optional[str] = None,
    date_from_str: Optional[str] = None,
    date_to_str: Optional[str] = None,
    document_type: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    doc_number: Optional[str] = None,
    search: Optional[str] = None,
    min_signers: Optional[int] = None,
    sector_filter: Optional[str] = None,
    case_id: Optional[str] = None,
    *,
    schema_name: str
) -> Dict[str, Any]:
    """
    Obtiene los documentos de un usuario (tanto draft como official) con paginación SQL.

    Todos los filtros y la paginación (LIMIT/OFFSET) se ejecutan en PostgreSQL.
    Se realiza un SELECT COUNT(*) separado con los mismos filtros para el total del
    paginador. No se cargan registros innecesarios en memoria.

    Args:
        user_id: ID del usuario
        status_filter: Filtro por estado visual (opcional)
        date_filter: Filtro por fecha predefinido (opcional)
        date_from_str: Fecha inicial en formato YYYY-MM-DD (opcional)
        date_to_str: Fecha final en formato YYYY-MM-DD (opcional)
        document_type: Filtro por tipo de documento (opcional)
        page: Página para paginación (1-based)
        page_size: Tamaño de página (máx 100)
        doc_number: Búsqueda por número de documento (opcional)
        search: Búsqueda parcial en referencia, número, contenido y firmantes (opcional)
        min_signers: Cantidad mínima de firmantes (opcional)
        sector_filter: "mine" = solo propios, "sector" = solo del sector (opcional)
        case_id: UUID del expediente para filtrar documentos vinculados (opcional)
        schema_name: Nombre del schema (para multi-tenant)

    Returns:
        Diccionario con documentos paginados y metadata
    """
    # Cap de page_size para evitar LIMIT gigante
    page_size = min(max(page_size, 1), 100)
    offset = (page - 1) * page_size

    # Obtener sectores donde el usuario puede ver (para filtro por sector)
    from services.case_service import CaseService
    user_viewable_sectors = await CaseService.get_user_viewable_sector_ids(user_id, schema_name=schema_name)
    user_sectors_list = user_viewable_sectors if user_viewable_sectors else []

    # -------------------------------------------------------------------------
    # UNION ALL: draft + oficial, mismos $1/$2 en ambas ramas.
    # Todos los campos necesarios para filtrar en el outer WHERE se exponen aquí.
    # Nota: content_html y signers_names se calculan para la búsqueda de texto.
    # -------------------------------------------------------------------------
    union_cte = """
    SELECT
        d.id AS document_id,
        d.reference,
        d.status::text AS status,
        d.last_modified_at,
        u.full_name::text AS last_editor_full_name,
        u.profile_picture_url AS last_editor_profile_picture_url,
        dt.acronym::text AS acronym,
        NULL::text AS official_number,
        d.short_resume,
        d.resume,
        NULL::jsonb AS linked_cases,
        NULL::jsonb AS linked_records,
        creator_s.acronym AS creator_sector_acronym,
        creator_d.acronym AS creator_dept_acronym,
        u_sender.full_name AS sent_by_name,
        COALESCE(d.content->>'html', '') AS content_html,
        -- BACKEND-05: signers_names, signers_count y signers calculados UNA SOLA VEZ
        -- via LATERAL JOIN (antes eran 3 subqueries separadas sobre document_signers).
        COALESCE(signer_agg.signers_names, '') AS signers_names,
        COALESCE(signer_agg.signers_count, 0) AS signers_count,
        signer_agg.signers AS signers,
        CASE
          WHEN ds.is_numerator = true THEN 'numerador'
          WHEN d.created_by = $1::uuid THEN 'creador'
          WHEN ds.is_numerator = false AND ds.user_id IS NOT NULL THEN 'firmante'
          WHEN EXISTS (SELECT 1 FROM users u2 WHERE u2.id = d.created_by AND u2.sector_id = ANY($2::uuid[])) THEN 'sector'
          ELSE 'otro'
        END AS rol_usuario,
        ds.signed_at IS NOT NULL AS usuario_ya_firmo,
        ds.user_id IS NOT NULL AS usuario_es_firmante,
        COALESCE(
          (SELECT bool_and(s2.signed_at IS NOT NULL)
           FROM document_signers s2
           WHERE s2.document_id = d.id AND (s2.is_numerator = false OR s2.is_numerator IS NULL)),
          true
        ) AS todos_firmantes_comunes_firmaron
    FROM document_draft d
    JOIN document_types dt ON d.document_type_id = dt.id
    LEFT JOIN users u ON d.created_by = u.id
    LEFT JOIN sectors creator_s ON u.sector_id = creator_s.id
    LEFT JOIN departments creator_d ON creator_s.department_id = creator_d.id
    LEFT JOIN users u_sender ON d.sent_by = u_sender.id
    LEFT JOIN document_signers ds ON ds.document_id = d.id AND ds.user_id = $1::uuid
    -- BACKEND-05: calcular todos los agregados de firmantes una sola vez
    LEFT JOIN LATERAL (
        SELECT
            string_agg(signer_u.full_name, ' ' ORDER BY signer_ds.signing_order NULLS LAST, signer_ds.user_id) AS signers_names,
            COUNT(*) AS signers_count,
            jsonb_agg(jsonb_build_object(
                'user_id', signer_ds.user_id,
                'full_name', signer_u.full_name,
                'profile_picture_url', signer_u.profile_picture_url,
                'signed', signer_ds.signed_at IS NOT NULL,
                'is_numerator', signer_ds.is_numerator
            ) ORDER BY signer_ds.signing_order NULLS LAST, signer_ds.user_id) AS signers
        FROM document_signers signer_ds
        JOIN users signer_u ON signer_u.id = signer_ds.user_id
        WHERE signer_ds.document_id = d.id
    ) signer_agg ON true
    WHERE dt.is_active = true
      AND d.is_deleted = false
      AND NOT EXISTS (SELECT 1 FROM official_documents WHERE id = d.id AND signed_at IS NOT NULL)
      AND (
        d.created_by = $1::uuid
        OR (EXISTS (
            SELECT 1 FROM document_signers s
            WHERE s.document_id = d.id
              AND s.user_id = $1::uuid
              AND d.created_by != $1::uuid
              AND d.status IN ('sent_to_sign', 'signed')
        ))
        OR (
            EXISTS (SELECT 1 FROM users u2 WHERE u2.id = d.created_by AND u2.sector_id = ANY($2::uuid[]))
            AND d.created_by != $1::uuid
            AND d.status IN ('sent_to_sign', 'signed')
            AND dt.acronym NOT IN ('NOTA', 'MEMO')
        )
      )

    UNION ALL

    SELECT
        o.id AS document_id,
        o.reference,
        'signed'::text AS status,
        o.signed_at AS last_modified_at,
        u.full_name::text AS last_editor_full_name,
        u.profile_picture_url AS last_editor_profile_picture_url,
        dt.acronym::text AS acronym,
        o.official_number::text AS official_number,
        o.short_resume,
        o.resume,
        (SELECT jsonb_agg(jsonb_build_object('case_id', c.id, 'case_number', c.case_number))
         FROM case_official_documents cod
         JOIN cases c ON c.id = cod.case_id
         WHERE cod.official_document_id = o.id AND cod.is_active = true) AS linked_cases,
        (SELECT jsonb_agg(jsonb_build_object('record_id', r.id, 'record_number', r.record_number))
         FROM record_document_links rdl
         JOIN records r ON r.id = rdl.record_id
         WHERE rdl.document_id = o.id) AS linked_records,
        creator_s.acronym AS creator_sector_acronym,
        creator_d.acronym AS creator_dept_acronym,
        u_sender.full_name AS sent_by_name,
        COALESCE(o.content->>'html', '') AS content_html,
        -- BACKEND-05: signers_names, signers_count y signers calculados UNA SOLA VEZ
        -- via LATERAL JOIN (antes eran 3 subqueries separadas sobre document_signers).
        COALESCE(signer_agg.signers_names, '') AS signers_names,
        COALESCE(signer_agg.signers_count, 0) AS signers_count,
        signer_agg.signers AS signers,
        CASE
          WHEN ds.is_numerator THEN 'numerador'
          WHEN ds.user_id IS NOT NULL THEN 'firmante'
          WHEN d.created_by = $1::uuid THEN 'creador'
          WHEN o.signer_sector_ids && $2::uuid[] THEN 'sector'
          ELSE 'otro'
        END AS rol_usuario,
        ds.signed_at IS NOT NULL AS usuario_ya_firmo,
        ds.user_id IS NOT NULL AS usuario_es_firmante,
        (SELECT bool_and(s2.signed_at IS NOT NULL)
         FROM document_signers s2
         WHERE s2.document_id = o.id AND (s2.is_numerator = false OR s2.is_numerator IS NULL)
        ) AS todos_firmantes_comunes_firmaron
    FROM official_documents o
    JOIN document_draft d ON o.id = d.id
    JOIN document_types dt ON o.document_type_id = dt.id
    LEFT JOIN users u ON o.numerator_id = u.id
    LEFT JOIN users creator_u ON d.created_by = creator_u.id
    LEFT JOIN sectors creator_s ON creator_u.sector_id = creator_s.id
    LEFT JOIN departments creator_d ON creator_s.department_id = creator_d.id
    LEFT JOIN users u_sender ON d.sent_by = u_sender.id
    LEFT JOIN document_signers ds ON ds.document_id = o.id AND ds.user_id = $1::uuid
    -- BACKEND-05: calcular todos los agregados de firmantes una sola vez
    LEFT JOIN LATERAL (
        SELECT
            string_agg(signer_u.full_name, ' ' ORDER BY signer_ds.signing_order NULLS LAST, signer_ds.user_id) AS signers_names,
            COUNT(*) AS signers_count,
            jsonb_agg(jsonb_build_object(
                'user_id', signer_ds.user_id,
                'full_name', signer_u.full_name,
                'profile_picture_url', signer_u.profile_picture_url,
                'signed', signer_ds.signed_at IS NOT NULL,
                'is_numerator', signer_ds.is_numerator
            ) ORDER BY signer_ds.signing_order NULLS LAST, signer_ds.user_id) AS signers
        FROM document_signers signer_ds
        JOIN users signer_u ON signer_u.id = signer_ds.user_id
        WHERE signer_ds.document_id = o.id
    ) signer_agg ON true
    WHERE dt.is_active = true
      AND d.is_deleted = false
      AND o.signed_at IS NOT NULL
      AND (
        d.created_by = $1::uuid
        OR (EXISTS (
            SELECT 1 FROM document_signers s
            WHERE s.document_id = o.id AND s.user_id = $1::uuid
        ) AND d.created_by != $1::uuid)
        OR (
            o.signer_sector_ids && $2::uuid[]
            AND d.created_by != $1::uuid
            AND dt.acronym NOT IN ('NOTA', 'MEMO')
        )
      )
    """

    # -------------------------------------------------------------------------
    # Construir filtros dinámicos para el outer WHERE.
    # $1 y $2 están fijos (user_id, user_sectors_list).
    # Los parámetros dinámicos empiezan en $3.
    # -------------------------------------------------------------------------
    filter_conditions: List[str] = []
    filter_params: List[Any] = []

    # Índice del próximo parámetro dinámico (empieza en 3)
    next_param = [3]

    def add_param(value: Any) -> str:
        """Registra un parámetro y devuelve su placeholder $n."""
        placeholder = f"${next_param[0]}"
        filter_params.append(value)
        next_param[0] += 1
        return placeholder

    # -- Filtro status_filter (display_status → condiciones sobre campos SQL) --
    # La lógica replica exactamente map_display_status():
    #   "En edición"          → status IN ('draft', 'rejected')  — rechazados vuelven a edición
    #   "Firmado"             → status = 'signed'  (official_documents ya filtrado por signed_at IS NOT NULL)
    #   "Firmar ahora"        → sent_to_sign + es firmante + no firmó + (no numerador OR todos_comunes_firmaron)
    #   "En proceso de firma" → sent_to_sign + NOT (condición Firmar ahora)
    if status_filter == "En edición":
        filter_conditions.append("status IN ('draft', 'rejected')")
    elif status_filter == "Firmado":
        filter_conditions.append("status = 'signed'")
    elif status_filter == "Firmar ahora":
        filter_conditions.append("""(
            status = 'sent_to_sign'
            AND usuario_es_firmante = true
            AND usuario_ya_firmo = false
            AND (rol_usuario != 'numerador' OR todos_firmantes_comunes_firmaron = true)
        )""")
    elif status_filter == "En proceso de firma":
        # Todo lo que está en sent_to_sign pero NO cumple "Firmar ahora"
        filter_conditions.append("""(
            status = 'sent_to_sign'
            AND NOT (
                usuario_es_firmante = true
                AND usuario_ya_firmo = false
                AND (rol_usuario != 'numerador' OR todos_firmantes_comunes_firmaron = true)
            )
        )""")

    # -- Filtro sector_filter --
    # "mine"   → excluye documentos cuyo acceso es solo por sector (no propios)
    # "sector" → solo documentos a los que se accede por sector
    if sector_filter == "mine":
        filter_conditions.append("rol_usuario != 'sector'")
    elif sector_filter == "sector":
        filter_conditions.append("rol_usuario = 'sector'")

    # -- Filtro date_filter (predefinido) --
    # Anclado a medianoche UTC (DATE_TRUNC) para paridad con el Python original
    # que usaba today = datetime.now(utc).replace(hour=0, minute=0, ...).
    if date_filter == "hoy":
        filter_conditions.append(
            "last_modified_at >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC')"
            " AND last_modified_at < DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC') + INTERVAL '1 day'"
        )
    elif date_filter == "ayer":
        filter_conditions.append(
            "last_modified_at >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC') - INTERVAL '1 day'"
            " AND last_modified_at < DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC')"
        )
    elif date_filter == "ultimos_7_dias":
        filter_conditions.append(
            "last_modified_at >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC') - INTERVAL '7 days'"
        )
    elif date_filter == "ultimos_30_dias":
        filter_conditions.append(
            "last_modified_at >= DATE_TRUNC('day', NOW() AT TIME ZONE 'UTC') - INTERVAL '30 days'"
        )

    # -- Filtro date_from_str / date_to_str (rango personalizado) --
    if date_from_str:
        try:
            datetime.strptime(date_from_str, "%Y-%m-%d")  # Validar formato
            p = add_param(date_from_str)
            filter_conditions.append(f"last_modified_at >= {p}::date")
        except ValueError:
            pass

    if date_to_str:
        try:
            datetime.strptime(date_to_str, "%Y-%m-%d")  # Validar formato
            p = add_param(date_to_str)
            filter_conditions.append(f"last_modified_at < ({p}::date + INTERVAL '1 day')")
        except ValueError:
            pass

    # -- Filtro document_type --
    if document_type:
        p = add_param(document_type)
        filter_conditions.append(f"acronym = {p}")

    # -- Filtro min_signers --
    if min_signers and min_signers > 0:
        p = add_param(min_signers)
        filter_conditions.append(f"signers_count >= {p}")

    # -- Filtro case_id (documentos vinculados a un expediente) --
    # Busca en case_official_documents (oficiales) y case_proposed_documents (borradores).
    if case_id:
        p = add_param(case_id)
        filter_conditions.append(f"""(
            EXISTS (
                SELECT 1 FROM case_official_documents cod
                WHERE cod.official_document_id = document_id
                  AND cod.case_id = {p}::uuid
                  AND cod.is_active = true
            )
            OR EXISTS (
                SELECT 1 FROM case_proposed_documents cpd
                WHERE cpd.document_draft_id = document_id
                  AND cpd.case_id = {p}::uuid
                  AND cpd.is_active = true
            )
        )""")

    # -- Filtro de búsqueda de texto (search o doc_number) --
    # Usa unaccent() en PostgreSQL sin alias de tabla (los campos vienen del outer SELECT *).
    # Busca en: reference, official_number, content_html, signers_names.
    search_text = search or doc_number
    if search_text and len(search_text) >= 2:
        p1 = add_param(f"%{search_text}%")
        p2 = add_param(f"%{search_text}%")
        p3 = add_param(f"%{search_text}%")
        p4 = add_param(f"%{search_text}%")
        filter_conditions.append(f"""(
            unaccent(LOWER(reference)) LIKE unaccent(LOWER({p1}))
            OR unaccent(LOWER(COALESCE(official_number, ''))) LIKE unaccent(LOWER({p2}))
            OR unaccent(LOWER(content_html)) LIKE unaccent(LOWER({p3}))
            OR unaccent(LOWER(signers_names)) LIKE unaccent(LOWER({p4}))
        )""")

    # Construir cláusula WHERE para el outer query
    where_clause = ("WHERE " + " AND ".join(filter_conditions)) if filter_conditions else ""

    # -------------------------------------------------------------------------
    # ORDER BY: replica exactamente la lógica Python de _get_display_status_priority.
    # Prioridad: Firmar ahora (1) > En edición (2) > En proceso de firma (3) > Firmado (4)
    # Luego por fecha descendente.
    # -------------------------------------------------------------------------
    order_by = """
    ORDER BY
        CASE
            WHEN status = 'sent_to_sign'
                 AND usuario_es_firmante = true
                 AND usuario_ya_firmo = false
                 AND (rol_usuario != 'numerador' OR todos_firmantes_comunes_firmaron = true)
            THEN 1
            WHEN status IN ('draft', 'rejected') THEN 2
            WHEN status = 'sent_to_sign' THEN 3
            WHEN status = 'signed' THEN 4
            ELSE 5
        END ASC,
        last_modified_at DESC NULLS LAST
    """

    # -------------------------------------------------------------------------
    # PASO 1: COUNT total con los mismos filtros (sin LIMIT/OFFSET)
    # -------------------------------------------------------------------------
    count_query = f"""
    SELECT COUNT(*) FROM (
        {union_cte}
    ) AS docs
    {where_clause}
    """

    # -------------------------------------------------------------------------
    # PASO 2: Query de datos con LIMIT y OFFSET en SQL
    # Los placeholders de LIMIT y OFFSET vienen después de los filter_params
    # -------------------------------------------------------------------------
    limit_placeholder = f"${next_param[0]}"
    offset_placeholder = f"${next_param[0] + 1}"

    data_query = f"""
    SELECT * FROM (
        {union_cte}
    ) AS docs
    {where_clause}
    {order_by}
    LIMIT {limit_placeholder} OFFSET {offset_placeholder}
    """

    # Parámetros base fijos ($1, $2) + filtros dinámicos ($3...) + LIMIT + OFFSET
    base_params = [user_id, user_sectors_list]
    count_final_params = base_params + filter_params
    data_final_params = base_params + filter_params + [page_size, offset]

    async with get_conn(schema_name=schema_name) as conn:
        # COUNT
        count_row = await conn.fetchrow(count_query, *count_final_params)
        total_docs = int(count_row[0]) if count_row else 0

        # Si no hay resultados, evitar la segunda query
        if total_docs == 0:
            return {
                "total": 0,
                "page": page,
                "page_size": page_size,
                "total_pages": 0,
                "documents": []
            }

        # DATA (solo la página pedida)
        raw_docs = await conn.fetch(data_query, *data_final_params)

    # Convertir Records a dicts y calcular display_status en Python
    # (map_display_status() necesita lógica de rol que ya viene de SQL)
    paginated_docs = []
    for row in raw_docs:
        doc_dict = dict(row)
        doc_dict["display_status"] = map_display_status(
            doc_dict["status"],
            doc_dict["rol_usuario"],
            doc_dict["usuario_ya_firmo"],
            doc_dict["todos_firmantes_comunes_firmaron"],
            doc_dict["usuario_es_firmante"],
            doc_dict["document_id"]
        )
        paginated_docs.append(doc_dict)

    total_pages = (total_docs + page_size - 1) // page_size

    return {
        "total": total_docs,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "documents": paginated_docs
    }
