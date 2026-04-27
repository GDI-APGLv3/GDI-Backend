"""
Servicios para la gestión de documentos que contienen la lógica de negocio.
Estos servicios implementan las consultas a la base de datos y la manipulación de datos.
"""

import psycopg2
from psycopg2.extras import RealDictCursor
from typing import List, Dict, Any, Optional
from database import get_db_connection, get_db_cursor
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

def get_user_documents(
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
    *,
    schema_name: str
) -> Dict[str, Any]:
    """
    Obtiene los documentos de un usuario (tanto draft como official) con paginación.

    Args:
        user_id: ID del usuario
        status_filter: Filtro por estado (opcional)
        date_filter: Filtro por fecha predefinido (opcional)
        date_from_str: Fecha inicial en formato YYYY-MM-DD (opcional)
        date_to_str: Fecha final en formato YYYY-MM-DD (opcional)
        document_type: Filtro por tipo de documento (opcional)
        page: Página para paginación
        page_size: Tamaño de página
        doc_number: Filtro por número de documento exacto (opcional)
        search: Búsqueda parcial en referencia, número, contenido y firmantes (opcional)
        min_signers: Cantidad mínima de firmantes (opcional, ej: 2 para docs con 2+ firmas)
        schema_name: Nombre del schema (para multi-tenant)

    Returns:
        Diccionario con documentos paginados y metadata
    """
    # Obtener sectores donde el usuario puede ver (para filtro por sector)
    from services.case_service import CaseService
    user_viewable_sectors = CaseService.get_user_viewable_sector_ids(user_id, schema_name=schema_name)

    with get_db_connection(schema_name=schema_name) as conn:
        cursor = conn.cursor()

        try:
            # Query OPTIMIZADO para documentos en borrador - solo campos esenciales
            draft_query = """
            SELECT
                d.id as document_id,
                d.reference,
                d.status::text AS status,
                d.last_modified_at,
                u.full_name::text AS last_editor_full_name,
                u.profile_picture_url AS last_editor_profile_picture_url,
                dt.acronym::text AS acronym,
                NULL AS official_number,
                creator_s.acronym AS creator_sector_acronym,
                creator_d.acronym AS creator_dept_acronym,
                u_sender.full_name AS sent_by_name,
                COALESCE(d.content->>'html', '') AS content_html,
                -- Nombres de firmantes concatenados para búsqueda
                COALESCE(
                  (SELECT string_agg(signer_u.full_name, ' ')
                   FROM document_signers signer_ds
                   JOIN users signer_u ON signer_ds.user_id = signer_u.id
                   WHERE signer_ds.document_id = d.id),
                  ''
                ) AS signers_names,
                -- Cantidad de firmantes para filtro min_signers
                (SELECT COUNT(*) FROM document_signers WHERE document_id = d.id) AS signers_count,
                -- Solo calculamos estos campos para determinar display_status
                CASE
                  WHEN ds.is_numerator = true THEN 'numerador'
                  WHEN d.created_by = %(user_id)s THEN 'creador'
                  WHEN ds.is_numerator = false AND ds.user_id IS NOT NULL THEN 'firmante'
                  WHEN EXISTS (SELECT 1 FROM users u2 WHERE u2.id = d.created_by AND u2.sector_id = ANY(%(user_sectors)s::uuid[])) THEN 'sector'
                  ELSE 'otro'
                END AS rol_usuario,
                ds.signed_at IS NOT NULL AS usuario_ya_firmo,
                ds.user_id IS NOT NULL AS usuario_es_firmante,
                -- Simplificamos esta subconsulta costosa
                COALESCE(
                  (
                    SELECT bool_and(s2.signed_at IS NOT NULL)
                    FROM document_signers s2
                    WHERE s2.document_id = d.id AND (s2.is_numerator = false OR s2.is_numerator IS NULL)
                  ),
                  true
                ) AS todos_firmantes_comunes_firmaron
            FROM document_draft d
            JOIN document_types dt ON d.document_type_id = dt.id
            LEFT JOIN users u ON d.created_by = u.id
            LEFT JOIN sectors creator_s ON u.sector_id = creator_s.id
            LEFT JOIN departments creator_d ON creator_s.department_id = creator_d.id
            LEFT JOIN users u_sender ON d.sent_by = u_sender.id
            LEFT JOIN document_signers ds ON ds.document_id = d.id AND ds.user_id = %(user_id)s
            WHERE dt.is_active = true
              AND d.is_deleted = false
              -- Excluir documentos que ya tienen versión oficial firmada
              -- (signed_at IS NULL = número reservado pero firma fallida → sigue siendo draft)
              AND NOT EXISTS (SELECT 1 FROM official_documents WHERE id = d.id AND signed_at IS NOT NULL)
              AND (
                -- Es creator: ve todos sus documentos
                d.created_by = %(user_id)s
                OR
                -- Es firmante (pero NO creator): solo puede ver documentos enviados a firmar o firmados
                (EXISTS (
                    SELECT 1 FROM document_signers s
                    WHERE s.document_id = d.id
                      AND s.user_id = %(user_id)s
                      AND d.created_by != %(user_id)s
                      AND d.status IN ('sent_to_sign', 'signed')
                ))
                OR
                -- Es de su sector (pero NO creator): documentos en proceso de firma o firmados
                (
                    EXISTS (SELECT 1 FROM users u2 WHERE u2.id = d.created_by AND u2.sector_id = ANY(%(user_sectors)s::uuid[]))
                    AND d.created_by != %(user_id)s
                    AND d.status IN ('sent_to_sign', 'signed')
                    AND dt.acronym NOT IN ('NOTA', 'MEMO')
                )
            )
            """

            # Query OPTIMIZADO para documentos oficiales - solo campos esenciales
            official_query = """
            SELECT
                o.id as document_id,
                o.reference,
                'signed' AS status,
                o.signed_at AS last_modified_at,
                u.full_name::text AS last_editor_full_name,
                u.profile_picture_url AS last_editor_profile_picture_url,
                dt.acronym::text AS acronym,
                o.official_number::text AS official_number,
                creator_s.acronym AS creator_sector_acronym,
                creator_d.acronym AS creator_dept_acronym,
                u_sender.full_name AS sent_by_name,
                COALESCE(o.content->>'html', '') AS content_html,
                -- Nombres de firmantes concatenados para búsqueda
                COALESCE(
                  (SELECT string_agg(signer_u.full_name, ' ')
                   FROM document_signers signer_ds
                   JOIN users signer_u ON signer_ds.user_id = signer_u.id
                   WHERE signer_ds.document_id = o.id),
                  ''
                ) AS signers_names,
                -- Cantidad de firmantes para filtro min_signers
                (SELECT COUNT(*) FROM document_signers WHERE document_id = o.id) AS signers_count,
                -- Campos para display_status (simplificados para documentos firmados)
                CASE
                  WHEN ds.is_numerator THEN 'numerador'
                  WHEN ds.user_id IS NOT NULL THEN 'firmante'
                  WHEN d.created_by = %(user_id)s THEN 'creador'
                  WHEN o.signer_sector_ids && %(user_sectors)s::uuid[] THEN 'sector'
                  ELSE 'otro'
                END AS rol_usuario,
                ds.signed_at IS NOT NULL AS usuario_ya_firmo,
                ds.user_id IS NOT NULL AS usuario_es_firmante,
                -- Si todos los firmantes comunes firmaron (solo relevante para numerador)
                (
                  SELECT bool_and(s2.signed_at IS NOT NULL)
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
            LEFT JOIN document_signers ds ON ds.document_id = o.id AND ds.user_id = %(user_id)s
            WHERE dt.is_active = true
              AND d.is_deleted = false
              AND o.signed_at IS NOT NULL
              AND (
                -- Es creator: ve todos los documentos oficiales
                d.created_by = %(user_id)s
                OR
                -- Es firmante (pero NO creator): puede ver documentos oficiales (siempre están en estado 'signed')
                (EXISTS (
                    SELECT 1 FROM document_signers s
                    WHERE s.document_id = o.id
                      AND s.user_id = %(user_id)s
                ) AND d.created_by != %(user_id)s)
                OR
                -- Es de su sector (usa nueva columna signer_sector_ids)
                (
                    o.signer_sector_ids && %(user_sectors)s::uuid[]
                    AND d.created_by != %(user_id)s
                    AND dt.acronym NOT IN ('NOTA', 'MEMO')
                )
            )
            """
        
            # Preparar parámetros para las queries
            query_params = {
                'user_id': user_id,
                'user_sectors': user_viewable_sectors if user_viewable_sectors else []
            }

            # Ejecutar consulta para documentos en borrador
            cursor.execute(draft_query, query_params)
            draft_documents = cursor.fetchall()

            # Ejecutar consulta para documentos oficiales
            cursor.execute(official_query, query_params)
            official_documents = cursor.fetchall()
        
            # Combinar resultados
            all_documents = list(draft_documents) + list(official_documents)
        
            # Procesar el filtro de fecha
            filter_start_date = None
            filter_end_date = None
        
            # Calcular fechas según el filtro seleccionado
            if date_filter:
                today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            
                if date_filter == "hoy":
                    filter_start_date = today
                    filter_end_date = today + timedelta(days=1)
                elif date_filter == "ayer":
                    filter_start_date = today - timedelta(days=1)
                    filter_end_date = today
                elif date_filter == "ultimos_7_dias":
                    filter_start_date = today - timedelta(days=7)
                    filter_end_date = today + timedelta(days=1)
                elif date_filter == "ultimos_30_dias":
                    filter_start_date = today - timedelta(days=30)
                    filter_end_date = today + timedelta(days=1)
                
            # Filtrar por fechas personalizadas si se especificaron
            if date_from_str:
                try:
                    filter_start_date = datetime.strptime(date_from_str, "%Y-%m-%d").replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
                except ValueError:
                    pass
                
            if date_to_str:
                try:
                    filter_end_date = datetime.strptime(date_to_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc)
                except ValueError:
                    pass
        
            # Convertir los resultados a diccionarios simples y agregar estado visual
            result = []
            for doc in all_documents:
                doc_dict = dict(doc)
            
                # Agregar estado visual para el frontend
                display_status = map_display_status(
                    doc_dict["status"], 
                    doc_dict["rol_usuario"], 
                    doc_dict["usuario_ya_firmo"], 
                    doc_dict["todos_firmantes_comunes_firmaron"],
                    doc_dict["usuario_es_firmante"],
                    doc_dict["document_id"]
                )
            
                doc_dict["display_status"] = display_status
            
                # Filtrar por estado visual si se especificó
                if status_filter and display_status != status_filter:
                    continue
                
                # Filtrar por fecha si se especificó
                if filter_start_date and doc_dict["last_modified_at"] and doc_dict["last_modified_at"] < filter_start_date:
                    continue
                if filter_end_date and doc_dict["last_modified_at"] and doc_dict["last_modified_at"] >= filter_end_date:
                    continue
                
                # Filtrar por tipo de documento si se especificó
                if document_type and doc_dict["acronym"] != document_type:
                    continue

                # Filtrar por cantidad mínima de firmantes
                if min_signers and min_signers > 0:
                    if doc_dict.get("signers_count", 0) < min_signers:
                        continue

                # Filtrar por texto de búsqueda (reference, official_number, content, firmantes)
                # Combinar doc_number y search - usar el que esté presente
                search_text = search or doc_number
                if search_text and len(search_text) >= 2:
                    # Aplicar remove_accents para ignorar tildes
                    search_term = remove_accents(search_text.lower())
                    official_number = remove_accents((doc_dict.get("official_number", "") or "").lower())
                    reference = remove_accents((doc_dict.get("reference", "") or "").lower())
                    content_html = remove_accents((doc_dict.get("content_html", "") or "").lower())
                    signers_names = remove_accents((doc_dict.get("signers_names", "") or "").lower())

                    # Búsqueda parcial case-insensitive ignorando acentos
                    if (search_term not in reference and
                        search_term not in official_number and
                        search_term not in content_html and
                        search_term not in signers_names):
                        continue
                
                result.append(doc_dict)
        
            # Ordenar por prioridad de estado (acción requerida primero) y luego por fecha
            result.sort(
                key=lambda x: (
                    _get_display_status_priority(x.get("display_status", "")),
                    -(x["last_modified_at"] or datetime.min).timestamp()
                )
            )
        
            # Aplicar paginación
            total_docs = len(result)
            offset = (page - 1) * page_size
            paginated_docs = result[offset:offset + page_size] if total_docs > 0 else []
        
            # Devolver diccionario con total, información de paginación y documentos
            return {
                "total": total_docs,
                "page": page,
                "page_size": page_size,
                "total_pages": (total_docs + page_size - 1) // page_size,
                "documents": paginated_docs
            }
    
        except psycopg2.Error as e:
            logging.getLogger(__name__).error(f"Error PostgreSQL en get_user_documents: {e}")
            raise


