"""
Endpoint para búsqueda de documentos oficiales por número exacto.
Permite buscar documentos oficializados sin restricciones de usuario.
Optimizado siguiendo principios de Clean Code.
"""

from shared.logging import get_logger
from fastapi import APIRouter, HTTPException, Path, status, Request, Depends
from typing import Dict, Any
from auth import get_current_user
from models.schemas import AuthenticatedUser
from models.documents.official_search import OfficialDocumentSearchResponse
from models.tags import Tags
from services.documents.retrieval.official_search import search_official_document_by_number
from shared.exceptions import ValidationError, DatabaseError, exception_to_http_exception
from shared.dependencies import get_tenant_schema
from shared.utils import get_authenticated_user, get_user_global_search_flags

# === CONFIGURACIÓN ===
logger = get_logger("search_official")

router = APIRouter(tags=[Tags.DOCUMENTOS])

@router.get(
    "/api/v1/documents/search-official/{doc_number}",
    response_model=OfficialDocumentSearchResponse,
    status_code=status.HTTP_200_OK,
    summary="Buscar documento oficial por número exacto",
    description="Busca un documento oficial por su número de oficialización exacto, sin restricciones de usuario",
    responses={
        200: {"description": "Búsqueda completada (puede o no haber encontrado el documento)"},
        400: {"description": "Número de documento inválido"},
        500: {"description": "Error interno del servidor"}
    }
)
async def search_official_document(
    request: Request,
    doc_number: str = Path(
        ...,
        description="Número oficial exacto del documento a buscar (ej: ANEXO-2025-00000002-SMG-ADGEN)",
        pattern=r'^[A-Z]{2,6}-\d{4}-\d{1,9}-[A-Z]{2,5}-[A-Z]{2,10}$'
    ),
    current_user: AuthenticatedUser = Depends(get_current_user),
    schema_name: str = Depends(get_tenant_schema)
) -> Dict[str, Any]:
    """
    Busca un documento oficial por su número de oficialización exacto.
    
    ## 🎯 Funcionalidad Principal:
    
    Este endpoint permite buscar **documentos oficiales** (ya numerados y firmados)
    por su número oficial exacto, **sin restricciones de usuario**.
    
    ## 🔍 Búsqueda Global:
    
    - **Acceso público**: No requiere que el usuario sea creador o firmante
    - **Solo documentos oficiales**: Busca únicamente en `official_documents`
    - **Coincidencia exacta**: El número debe coincidir exactamente
    - **Formato específico**: Debe seguir el patrón oficial (TIPO-AÑO-NUMERO-CIUDAD-SECTOR)
    
    ## 📊 Datos Devueltos:
    
    Misma estructura que `/users/{user_id}/documents` pero adaptada para documentos oficiales:
    
    - **Información básica**: ID, referencia, estado (siempre "Firmado")
    - **Tipo de documento**: Nombre y acrónimo
    - **Creador original**: Nombre y foto de perfil
    - **Numerador**: Quien oficializó el documento
    - **Fechas**: Última modificación y fecha de firma
    - **Número oficial**: Número de oficialización completo
    
    ## 🔄 Casos de Uso:
    
    1. **Consulta pública**: Ciudadanos verificando documentos oficiales
    2. **Referencias cruzadas**: Otros sistemas consultando documentos
    3. **Auditoría**: Verificación de existencia y validez de documentos
    4. **Integración**: APIs externas accediendo a documentos oficiales
    
    ## ✅ Validaciones:
    
    - **Formato del número**: Debe seguir patrón oficial
    - **No vacío**: El número no puede estar en blanco
    - **Longitud**: Cumple límites de cada segmento
    
    ## 📄 Formato del Número Oficial:
    
    ```
    ANEXO-2025-00000002-SMG-ADGEN
    │     │    │        │   │
    │     │    │        │   └─ Sector/Departamento (2-10 chars)
    │     │    │        └───── Ciudad (2-5 chars)
    │     │    └────────────── Número consecutivo (1-9 dígitos)
    │     └─────────────────── Año (4 dígitos)
    └───────────────────────── Tipo de documento (2-6 chars)
    ```
    
    ## 🎛️ Respuesta:
    
    ```json
    {
        "found": true,
        "document": {
            "id": "uuid-del-documento",
            "reference": "ANEXO-2025-00000002-SMG-ADGEN",
            "display_status": "Firmado",
            "updated_at": "2025-01-15T10:35:00",
            "document_type": {
                "name": "Anexo",
                "acronym": "ANEXO"
            },
            "user_role": "public",
            "creator_name": "Juan Pérez",
            "creator_profile_picture_id": "uuid-foto",
            "official_number": "ANEXO-2025-00000002-SMG-ADGEN",
            "signed_at": "2025-01-15T10:35:00",
            "numerator_name": "María González"
        },
        "search_term": "ANEXO-2025-00000002-SMG-ADGEN"
    }
    ```
    
    Si no se encuentra:
    ```json
    {
        "found": false,
        "document": null,
        "search_term": "ANEXO-2025-00000999-SMG-ADGEN"
    }
    ```
    
    ## ⚠️ Importante:
    
    - Solo busca en documentos **ya oficializados** (tabla `official_documents`)
    - No busca en borradores ni documentos en proceso
    - El estado siempre será "Firmado" para documentos encontrados
    - El rol siempre será "public" (sin restricciones de acceso)
    
    ## 🔧 Integración:
    
    Este endpoint está diseñado para integrarse fácilmente con sistemas externos
    y permitir consultas públicas de documentos oficiales sin autenticación.
    
    ## Parámetros:
    - **doc_number**: Número oficial exacto del documento a buscar
    
    ## Respuesta:
    - **found**: Boolean indicando si se encontró el documento
    - **document**: Información completa del documento (null si no se encuentra)
    - **search_term**: Término de búsqueda utilizado para confirmar
    """

    logger.info(f"[SEARCH OFFICIAL] Búsqueda iniciada para: {doc_number}")

    try:
        # Validar usuario y obtener flags de busqueda global
        db_user_id = get_authenticated_user(request.state.tenant_user_id, schema_name=schema_name)
        flags = get_user_global_search_flags(db_user_id, schema_name=schema_name)

        # Llamar al servicio de búsqueda con flag de restriccion
        user_id_for_filter = None if flags["can_global_search_documents"] else db_user_id
        result = search_official_document_by_number(doc_number, user_id=user_id_for_filter, schema_name=schema_name)
        
        logger.info(f"[SEARCH OFFICIAL] Resultado: {'Encontrado' if result['found'] else 'No encontrado'}")
        
        # Retornar respuesta usando el modelo Pydantic
        return OfficialDocumentSearchResponse(
            found=result["found"],
            document=result["document"],  # Ya tiene la estructura correcta de UserDocumentInfo
            search_term=result["search_term"]
        )
        
    except ValidationError as e:
        # Errores de validación (400 Bad Request)
        logger.info(f"[SEARCH OFFICIAL] Error de validación: {str(e)}")
        raise exception_to_http_exception(e)
    
    except Exception as e:
        # Errores inesperados (500 Internal Server Error)
        logger.info(f"[SEARCH OFFICIAL] Error interno: {str(e)}")
        from shared.exceptions import DatabaseError
        raise exception_to_http_exception(
            DatabaseError(f"Error interno del servidor: {str(e)}")
        )