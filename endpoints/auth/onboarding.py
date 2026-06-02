"""
Endpoint GET /api/auth/onboarding - Información de acceso multi-tenant.
Retorna tenants disponibles y perfil del usuario autenticado.
"""

from shared.logging import get_logger
from fastapi import APIRouter, Request, HTTPException, status
from auth import decode_jwt_from_request
from shared.tenant_validation import get_user_tenants, invalidate_user_cache
from models.tenant_models import OnboardingResponse, OnboardingUser, TenantAccess, UserProfile
from models.tags import Tags
from database import fetch_one, execute, DEMO_MODE

router = APIRouter(prefix="/api/auth", tags=[Tags.USERS])
logger = get_logger(__name__)


@router.get(
    "/onboarding",
    response_model=OnboardingResponse,
    summary="Obtener información de onboarding multi-tenant",
    description="""
    **Endpoint para obtener lista de municipalidades (tenants) disponibles para el usuario autenticado.**

    **Funcionalidades:**
    - Extrae email del JWT sin requerir header X-Tenant-Schema
    - Consulta tabla `user_registry` para obtener tenants del usuario
    - Retorna lista de municipalidades con acceso y perfil por defecto
    - Implementa cache de 30 minutos en tenant validation

    **Autenticación:**
    - Requiere token JWT válido en header `Authorization: Bearer <token>`
    - El email se extrae automáticamente del token de Auth0

    **Respuesta:**
    - `user`: Información básica (email, nombre, foto)
    - `tenants`: Lista de municipalidades accesibles
    - `default_tenant`: Schema name de municipalidad por defecto
    - `default_profile`: Perfil del usuario en la municipalidad por defecto
    """,
    responses={
        200: {
            "description": "Información de onboarding obtenida correctamente",
            "content": {
                "application/json": {
                    "example": {
                        "user": {
                            "email": "juan.perez@example.com",
                            "full_name": "Juan Pérez",
                            "profile_picture_url": "https://s.gravatar.com/avatar/123.jpg"
                        },
                        "tenants": [
                            {
                                "municipality_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                                "display_name": "Municipalidad del Futuro",
                                "is_default": True
                            },
                            {
                                "municipality_id": "b2c3d4e5-f6a7-8901-bcde-f01234567891",
                                "display_name": "Municipalidad del Futuro",
                                "is_default": False
                            }
                        ],
                        "default_tenant": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                        "default_profile": {
                            "user_id": "550e8400-e29b-41d4-a716-446655440000",
                            "email": "juan.perez@example.com",
                            "sector_id": "770e8400-e29b-41d4-a716-446655440222",
                            "department_id": "880e8400-e29b-41d4-a716-446655440333",
                            "estado": 1
                        }
                    }
                }
            }
        },
        401: {"description": "Token JWT inválido o expirado"},
        403: {"description": "Usuario sin acceso a ninguna municipalidad"},
        500: {"description": "Error interno del servidor"}
    }
)
async def get_onboarding(request: Request) -> OnboardingResponse:
    """
    Obtiene información de onboarding para usuario multi-tenant.

    **Pasos:**
    1. Extrae email del JWT usando decode_jwt_from_request()
    2. Obtiene Auth0 metadata: name y picture del JWT
    3. Consulta user_registry para tenants del usuario
    4. Valida que tenga al menos 1 tenant
    5. Construye respuesta con tenants y tenant por defecto

    **Validaciones:**
    - JWT válido (raises 401 si es inválido)
    - Usuario con al menos 1 tenant (raises 403 si no)
    """
    try:
        # 1. Extraer email del JWT con fallback chain
        logger.info("Extrayendo información del JWT")
        jwt_payload = decode_jwt_from_request(request)
        email = (
            jwt_payload.get("email")
            or jwt_payload.get("https://gdilatam.com/email")
        )

        if email:
            source = "JWT" if jwt_payload.get("email") else "namespace claim"
            logger.info(f"Email obtenido de {source} para auth_id={jwt_payload.get('sub')}")

        if not email:
            logger.error("No se pudo obtener email del JWT ni del namespace claim")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No se pudo identificar el email del usuario"
            )

        # 2. Obtener Auth0 metadata (del JWT o de headers enviados por frontend)
        full_name = jwt_payload.get("name")
        picture = jwt_payload.get("picture")

        # Si el JWT no tiene name, usar header X-User-Name (enviado por frontend)
        if not full_name:
            full_name = request.headers.get("X-User-Name")

        # Fallback final: usar parte del email antes del @
        if not full_name:
            full_name = email.split("@")[0]

        # Si el JWT no tiene picture, usar header X-User-Picture (enviado por frontend)
        if not picture:
            picture = request.headers.get("X-User-Picture")

        logger.info(f"Procesando onboarding para auth_id={jwt_payload.get('sub')}")
        logger.info(f"Auth0 metadata - name: {full_name}, picture: {picture or 'NULL'}")

        # 3. Obtener tenants del usuario
        tenants_data = await get_user_tenants(email)

        # 4. Si no tiene tenants, decidir según DEMO_MODE
        if not tenants_data:
            if not DEMO_MODE:
                logger.warning(f"auth_id={jwt_payload.get('sub')} sin acceso a ninguna municipalidad")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="No tiene acceso a ninguna municipalidad. Contacte al administrador."
                )

            # --- DEMO_MODE: auto-crear usuario SOLO en 100_test ---
            # Verificar si el schema 100_test existe en la BD
            schema_check = await fetch_one(
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = '100_test'",
                schema_name="public",
            )
            if not schema_check:
                logger.warning(f"auth_id={jwt_payload.get('sub')} sin acceso a ninguna municipalidad (schema 100_test no existe)")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="No tiene acceso a ninguna municipalidad. Contacte al administrador."
                )

            logger.info(f"auth_id={jwt_payload.get('sub')} sin tenants - creando en 100_test automáticamente")

            real_auth_id = jwt_payload.get("sub")

            # 4.1 Crear usuario en 100_test.users
            # - Sector random de los activos
            # - Incluye profile_picture_url de Auth0
            # NOTA: sellos se manejan en tabla user_seals separada
            DEFAULT_TENANT_SCHEMA = "100_test"
            create_user_query = """
                INSERT INTO users (email, full_name, auth_id, estado, sector_id, profile_picture_url)
                SELECT
                    $1,
                    $2,
                    $3,
                    1,
                    (SELECT id FROM sectors WHERE is_active = true ORDER BY RANDOM() LIMIT 1),
                    $4
                WHERE NOT EXISTS (SELECT 1 FROM users WHERE email = $5)
            """
            try:
                await execute(
                    create_user_query,
                    email.lower(), full_name, real_auth_id, picture, email.lower(),
                    schema_name=DEFAULT_TENANT_SCHEMA,
                )
                logger.info(f"auth_id={real_auth_id} creado en {DEFAULT_TENANT_SCHEMA}.users")
            except Exception as e:
                logger.warning(f"auth_id={real_auth_id} ya existe o error creando: {e}")

            # 4.2 Registrar en user_registry (foto de perfil se obtiene de {schema}.users)
            register_query = """
                INSERT INTO public.user_registry (email, schema_name, is_default)
                VALUES ($1, '100_test', true)
                ON CONFLICT (email, schema_name) DO NOTHING
            """
            try:
                await execute(register_query, email.lower(), schema_name="public")
                logger.info(f"auth_id={real_auth_id} registrado en user_registry para 100_test")
            except Exception as e:
                logger.warning(f"Error registrando en user_registry: {e}")

            # 4.3 Asignar un sello aleatorio al usuario en user_seals
            assign_seal_query = """
                INSERT INTO user_seals (user_id, city_seal_id)
                SELECT u.id, cs.id
                FROM users u, city_seals cs
                WHERE u.email = $1
                ORDER BY RANDOM()
                LIMIT 1
                ON CONFLICT DO NOTHING;
            """
            try:
                await execute(assign_seal_query, email.lower(), schema_name=DEFAULT_TENANT_SCHEMA)
                logger.info(f"Sello asignado al usuario auth_id={real_auth_id}")
            except Exception as e:
                logger.warning(f"Error asignando sello: {e}")

            # 4.3.5 Asignar permisos VIEW y EDIT a todos los sectores activos (excepto el principal)
            # Esto permite que usuarios de prueba puedan VER y EDITAR expedientes/documentos de cualquier sector
            assign_view_query = """
                INSERT INTO user_sector_permissions (user_id, sector_id, can_view, can_edit)
                SELECT u.id, s.id, true, true
                FROM users u
                CROSS JOIN sectors s
                WHERE u.email = $1
                  AND s.is_active = true
                  AND s.id != u.sector_id
                ON CONFLICT (user_id, sector_id) DO NOTHING
            """
            try:
                await execute(assign_view_query, email.lower(), schema_name=DEFAULT_TENANT_SCHEMA)
                logger.info(f"Permisos VIEW y EDIT asignados a todos los sectores para auth_id={real_auth_id}")
            except Exception as e:
                logger.warning(f"Error asignando permisos VIEW: {e}")

            # 4.4 Invalidar cache y re-obtener tenants después de crear
            invalidate_user_cache(email)
            tenants_data = await get_user_tenants(email)

            if not tenants_data:
                logger.error(f"Falló crear usuario auth_id={real_auth_id} en 100_test")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Error creando usuario en tenant de pruebas"
                )

        # 5. Completar datos faltantes del usuario (auth_id y profile_picture_url)
        auth_id_from_jwt = jwt_payload.get("sub")
        if tenants_data and (auth_id_from_jwt or picture):
            for tenant in tenants_data:
                try:
                    await execute(
                        """UPDATE users
                           SET auth_id = COALESCE(auth_id, $1),
                               profile_picture_url = COALESCE(profile_picture_url, $2)
                           WHERE LOWER(email) = $3
                             AND (auth_id IS NULL OR profile_picture_url IS NULL)""",
                        auth_id_from_jwt, picture, email.lower(),
                        schema_name=tenant["schema_name"],
                    )
                except Exception as e:
                    logger.warning(f"Error actualizando datos de usuario en {tenant['schema_name']}: {e}")

        # 6. Construir respuesta
        tenants = [TenantAccess(**t) for t in tenants_data]

        # SEC-31 (revertido por hotfix): default_tenant vuelve a ser schema_name,
        # que es lo que el frontend usa como X-Tenant-Schema y el middleware valida.
        default_tenant = next(
            (t.schema_name for t in tenants if t.is_default),
            tenants[0].schema_name if tenants else None
        )

        logger.info(f"auth_id={jwt_payload.get('sub')} tiene acceso a {len(tenants)} tenant(s). Default schema: {default_tenant}")

        return OnboardingResponse(
            user=OnboardingUser(
                email=email,
                full_name=full_name,
                profile_picture_url=picture
            ),
            tenants=tenants,
            default_tenant=default_tenant,
            default_profile=None  # Se puede popullar desde user profile en el servicio si se necesita
        )

    except HTTPException:
        # Re-lanzar excepciones HTTP (401, 403, etc.)
        raise
    except Exception as e:
        logger.error(f"Error inesperado en onboarding: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error interno del servidor"
        )
