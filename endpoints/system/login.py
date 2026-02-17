"""
Endpoint para login de usuarios con integración Auth0
"""
import random
import uuid
import requests
import json
from datetime import datetime
from typing import Dict, Any, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Header, Request
from pydantic import BaseModel, EmailStr
from jose import jwt
from database import get_db_connection
from auth import get_current_user, security
from models.schemas import AuthenticatedUser
from shared.logging import get_logger
import os

# Configurar logger
logger = get_logger(__name__)

router = APIRouter(prefix="/api/auth", tags=["🔐 Authentication"])


class LoginRequest(BaseModel):
    """Modelo para la solicitud de login con Auth0 token"""
    access_token: str
    email: EmailStr
    full_name: str
    picture_profile_url: Optional[str] = None
    cuit: Optional[str] = None


class LoginResponse(BaseModel):
    """Modelo para la respuesta de login"""
    success: bool
    user: Dict[str, Any]
    message: str
    is_new_user: bool
    assigned_sector: Optional[Dict[str, Any]] = None
    assigned_department: Optional[Dict[str, Any]] = None
    assigned_seal: Optional[Dict[str, Any]] = None
    deprecated: Optional[bool] = True
    deprecation_message: Optional[str] = "Este endpoint será removido en v3.0.0. Usar POST /api/auth/onboarding"


def validate_auth0_token(access_token: str) -> Dict[str, Any]:
    """
    Valida el access token con Auth0 y devuelve la información del usuario
    """
    try:
        # Configuración de Auth0
        auth0_domain = os.getenv('AUTH0_DOMAIN')
        auth0_audience = os.getenv('AUTH0_AUDIENCE')
        
        if not auth0_domain or not auth0_audience:
            raise Exception("Auth0 no configurado correctamente")
        
        # Obtener información del usuario usando el access token
        userinfo_url = f"https://{auth0_domain}/userinfo"
        headers = {
            'Authorization': f'Bearer {access_token}',
            'Content-Type': 'application/json'
        }
        
        response = requests.get(userinfo_url, headers=headers, timeout=10)
        
        if response.status_code != 200:
            raise Exception(f"Token inválido: {response.status_code}")
        
        user_info = response.json()
        logger.info(f"Token validado exitosamente para usuario: {user_info.get('email')}")
        
        return user_info
        
    except requests.exceptions.Timeout:
        logger.error("Timeout validando token con Auth0")
        raise HTTPException(status_code=status.HTTP_408_REQUEST_TIMEOUT, detail="Timeout validando token")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error de conexión validando token: {e}")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Error conectando con Auth0")
    except Exception as e:
        logger.error(f"Error validando token: {e}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")


def get_user_by_email(email: str, *, schema_name: str) -> Optional[Dict[str, Any]]:
    """Busca un usuario por email"""
    try:
        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM users WHERE LOWER(email) = LOWER(%s)",
                    [email]
                )
                result = cursor.fetchone()
                return dict(result) if result else None
    except Exception as e:
        logger.error(f"Error buscando usuario por email {email}: {e}")
        return None


def get_user_by_auth_id(auth_id: str, *, schema_name: str) -> Optional[Dict[str, Any]]:
    """Busca un usuario por auth_id"""
    try:
        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM users WHERE auth_id = %s",
                    [auth_id]
                )
                result = cursor.fetchone()
                return dict(result) if result else None
    except Exception as e:
        logger.error(f"Error buscando usuario por auth_id {auth_id}: {e}")
        return None


def get_random_sector_and_department(*, schema_name: str) -> tuple:
    """Obtiene un sector y departamento aleatorio activos"""
    try:
        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                # Obtener todos los sectores activos con sus departamentos
                cursor.execute("""
                    SELECT s.id as sector_id, s.department_id, s.acronym as sector_acronym,
                           d.name as department_name, d.acronym as department_acronym
                    FROM sectors s
                    JOIN departments d ON s.department_id = d.id
                    WHERE s.is_active = true AND d.is_active = true
                """)

                sectors_departments = cursor.fetchall()

                if not sectors_departments:
                    logger.error("No hay sectores/departamentos activos disponibles")
                    return None, None

                # Seleccionar uno aleatorio
                selected = random.choice(sectors_departments)

                sector = {
                    "sector_id": selected["sector_id"],
                    "department_id": selected["department_id"],
                    "acronym": selected["sector_acronym"]
                }

                department = {
                    "department_id": selected["department_id"],
                    "name": selected["department_name"],
                    "acronym": selected["department_acronym"]
                }

                return sector, department

    except Exception as e:
        logger.error(f"Error obteniendo sector/departamento aleatorio: {e}")
        return None, None


def get_random_seal(*, schema_name: str) -> Optional[Dict[str, Any]]:
    """Obtiene un sello aleatorio disponible"""
    try:
        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT id, name FROM city_seals ORDER BY RANDOM() LIMIT 1")
                result = cursor.fetchone()
                return dict(result) if result else None
    except Exception as e:
        logger.error(f"Error obteniendo sello aleatorio: {e}")
        return None


def create_user_seal_assignment(user_id: str, seal_id: int, schema_name: str) -> bool:
    """Crea la asignación de sello para un usuario"""
    try:
        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                # Verificar si ya existe una asignación
                cursor.execute(
                    "SELECT id FROM user_seals WHERE user_id = %s",
                    [user_id]
                )
                existing = cursor.fetchone()

                if existing:
                    logger.info(f"Usuario {user_id} ya tiene sellos asignados")
                    return True

                # Generar UUID para la asignación
                assignment_id = str(uuid.uuid4())

                # Crear nueva asignación
                cursor.execute(
                    "INSERT INTO user_seals (id, user_id, city_seal_id, created_at) VALUES (%s, %s, %s, %s)",
                    (assignment_id, user_id, seal_id, datetime.utcnow())
                )

                logger.info(f"Sello {seal_id} asignado exitosamente a usuario {user_id}")
                return True

    except Exception as e:
        logger.error(f"Error creando asignación de sello: {e}")
        return False


def update_user_last_access(user_id: int, schema_name: str) -> None:
    """Actualiza el último acceso del usuario"""
    try:
        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "UPDATE users SET last_access = %s WHERE id = %s",
                    [datetime.utcnow(), user_id]
                )
    except Exception as e:
        logger.error(f"Error actualizando último acceso: {e}")


def create_new_user(email: str, full_name: str, auth_id: str, schema_name: str, picture_profile_url: Optional[str] = None, cuit: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Crea un nuevo usuario en la base de datos"""
    try:
        # Obtener sector y departamento aleatorio
        sector, department = get_random_sector_and_department(schema_name=schema_name)
        if not sector:
            raise Exception("No se pudo asignar sector/departamento")

        # Obtener sello aleatorio
        seal = get_random_seal(schema_name=schema_name)
        if not seal:
            raise Exception("No se pudo asignar sello")

        # Generar UUID para el nuevo usuario
        user_id = str(uuid.uuid4())

        # Usar get_db_connection() centralizado para transacción atómica
        from database import get_db_connection

        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                # Crear el usuario
                cursor.execute("""
                    INSERT INTO users (
                        user_id, auth_id, full_name, email, cuit, profile_picture_url,
                        sector_id, last_access, created_at, estado, default_seal_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                """, [
                    user_id,
                    auth_id,
                    full_name,
                    email,
                    cuit,
                    picture_profile_url,
                    sector["sector_id"],
                    datetime.utcnow(),
                    datetime.utcnow(),
                    1,  # 1 = ACTIVO
                    seal["id"]
                ])

                new_user = cursor.fetchone()

                if not new_user:
                    raise Exception("Error creando usuario - no se devolvieron datos")

                user = dict(new_user)
                logger.info(f"Usuario creado exitosamente en BD: {user['user_id']}")

                # Asignar sello al usuario en la MISMA transacción (atomicidad)
                seal_assignment_id = str(uuid.uuid4())
                cursor.execute("""
                    INSERT INTO user_seals (id, user_id, city_seal_id, created_at)
                    VALUES (%s, %s, %s, %s)
                """, [
                    seal_assignment_id,
                    user["user_id"],
                    seal["id"],
                    datetime.utcnow()
                ])

                logger.info(f"Sello {seal['id']} asignado exitosamente a usuario {user['user_id']}")

                # Commit automático al salir del context manager

        return {
            "user": user,
            "sector": sector,
            "department": department,
            "seal": seal
        }

    except Exception as e:
        logger.error(f"Error creando nuevo usuario: {e}")
        return None


def update_pending_user(user: Dict[str, Any], auth_id: str, schema_name: str, picture_profile_url: Optional[str] = None, full_name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Actualiza un usuario pendiente con el auth_id"""
    try:
        # Obtener sector y departamento aleatorio si no los tiene
        sector, department = None, None
        seal = None

        if not user.get("sector_id"):
            sector, department = get_random_sector_and_department(schema_name=schema_name)
            if not sector:
                raise Exception("No se pudo asignar sector/departamento")

        # Asignar sello si no tiene
        if not user.get("default_seal_id"):
            seal = get_random_seal(schema_name=schema_name)
            if not seal:
                raise Exception("No se pudo asignar sello")

        # Usar get_db_connection() centralizado para transacción atómica
        from database import get_db_connection

        with get_db_connection(schema_name) as conn:
            with conn.cursor() as cursor:
                # Actualizar todos los campos en una sola query
                cursor.execute("""
                    UPDATE users
                    SET auth_id = %s,
                        profile_picture_url = %s,
                        full_name = %s,
                        sector_id = %s,
                        default_seal_id = %s,
                        estado = 1,
                        last_access = %s
                    WHERE user_id = %s
                """, (
                    auth_id,
                    picture_profile_url,
                    full_name if full_name is not None else user.get("full_name"),
                    sector["sector_id"] if sector else user.get("sector_id"),
                    seal["id"] if seal else user.get("default_seal_id"),
                    datetime.utcnow(),
                    user["user_id"]
                ))

                # Crear asignación de sello en la MISMA transacción (atomicidad)
                if seal:
                    seal_assignment_id = str(uuid.uuid4())
                    cursor.execute("""
                        INSERT INTO user_seals (id, user_id, city_seal_id, created_at)
                        VALUES (%s, %s, %s, %s)
                    """, [
                        seal_assignment_id,
                        user["user_id"],
                        seal["id"],
                        datetime.utcnow()
                    ])
                    logger.info(f"Sello {seal['id']} asignado exitosamente a usuario {user['user_id']}")

                # Commit automático al salir del context manager

        # Actualizar el objeto user con los nuevos valores
        user["auth_id"] = auth_id
        user["profile_picture_url"] = picture_profile_url
        if full_name is not None:
            user["full_name"] = full_name
        user["estado"] = 1
        user["last_access"] = datetime.utcnow()

        if sector:
            user["sector_id"] = sector["sector_id"]
        if seal:
            user["default_seal_id"] = seal["id"]

        return {
            "user": user,
            "sector": sector,
            "department": department,
            "seal": seal
        }

    except Exception as e:
        logger.error(f"Error actualizando usuario pendiente: {e}")
        return None


@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    http_request: Request,
    x_user_id: str = Header(None, alias="X-User-ID", description="ID de usuario para modo testing"),
    x_tenant_schema: str = Header(None, alias="X-Tenant-Schema", description="Schema del tenant para multi-tenant")
):
    """
    🔴🔴🔴 **DEPRECATED - SERÁ ELIMINADO EL 30 DE JUNIO 2026** 🔴🔴🔴
    
    ⚠️ **MIGRAR A:** `POST /api/auth/onboarding`
    
    **Razones de deprecación:**
    1. Arquitectura legacy con lógica mezclada en endpoint (580 líneas)
    2. No sigue principios Clean Code ni SOLID
    3. Bugs no corregidos (default_seal_id, profile_picture_url)
    4. Reemplazado por endpoint refactorizado con mejor arquitectura
    
    **Guía de migración:**
    ```
    ANTES (login):                    DESPUÉS (onboarding):
    - URL: /api/auth/login           → /api/auth/onboarding
    - access_token: "..."            → auth_id: user.sub
    - picture_profile_url: "..."     → profile_picture_url: "..."
    - is_new_user: true              → status: "created" + is_first_time: true
    ```
    
    **Cambios en response:**
    - `is_new_user` → `status` ("created"/"activated"/"existing_active")
    - Nuevo campo: `is_first_time` (boolean)
    - Nuevo campo: `was_invited` (boolean)
    - Mejor manejo de usuarios invitados
    
    **Timeline de eliminación:**
    - ✅ Diciembre 2025: Marcado como deprecated
    - ⚠️ Marzo 2026: Warnings en logs para usuarios que lo usen
    - 🔴 Junio 30, 2026: **ELIMINACIÓN DEFINITIVA**
    
    ---
    
    Endpoint legacy para login con Auth0 - Con validación de token.
    
    Flujo:
    1. Valida access_token con Auth0
    2. Maneja tres casos: usuario existente/pendiente/nuevo
    """
    try:
        # MULTI-TENANT: Obtener schema del header o del middleware
        schema_name = x_tenant_schema or getattr(http_request.state, 'schema_name', None)

        if not schema_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Header X-Tenant-Schema requerido para multi-tenant"
            )

        logger.info(f"[LOGIN] Usando schema: {schema_name}")

        # Verificar si estamos en modo testing
        is_testing_mode = os.getenv('TESTING_MODE', 'false').lower() == 'true'

        if is_testing_mode and x_user_id:
            # Modo testing - usar datos sin validación de token
            logger.warning("MODO TESTING ACTIVADO - Sin validación de token")
            auth_id = f"testing|{x_user_id}"
            validated_user_info = {
                'sub': auth_id,
                'email': request.email,
                'name': request.full_name
            }
        else:
            # Modo producción - validar token con Auth0
            validated_user_info = validate_auth0_token(request.access_token)
            auth_id = validated_user_info.get('sub')
            
            if not auth_id:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token no contiene identificador de usuario válido"
                )
            
            # Verificar que el email del token coincida con el enviado
            token_email = validated_user_info.get('email', '').lower()
            request_email = request.email.lower()
            
            if token_email != request_email:
                logger.warning(f"Email del token ({token_email}) no coincide con request ({request_email})")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Email del token no coincide con los datos enviados"
                )
        
        email = request.email.lower()
        full_name = request.full_name
        picture_profile_url = request.picture_profile_url
        
        logger.info(f"Intento de login validado para email: {email}, auth_id: {auth_id}")
        
        # LOGICA CORRECTA: Buscar por EMAIL primero
        user = get_user_by_email(email, schema_name=schema_name)
        
        logger.info(f"Búsqueda por email '{email}': {'Encontrado' if user else 'No encontrado'}")
        if user:
            logger.info(f"Usuario encontrado - auth_id: {user.get('auth_id', 'None')}, estado: {user.get('estado', 'None')}")
        
        # Caso 1: Email no existe - Usuario completamente nuevo
        if not user:
            logger.info(f"Creando nuevo usuario para email: {email} (email no existe)")
            
            result = create_new_user(email, full_name, auth_id, schema_name, picture_profile_url, request.cuit)
            if not result:
                logger.error(f"FALLO: create_new_user devolvió None para email {email}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Error creando nuevo usuario"
                )
            
            logger.info(f"ÉXITO: Nuevo usuario creado con ID {result['user']['user_id']}")
            return LoginResponse(
                success=True,
                user=dict(result["user"]),
                message="Usuario creado exitosamente",
                is_new_user=True,
                assigned_sector=result["sector"],
                assigned_department=result["department"],
                assigned_seal=result["seal"]
            )
        
        # Caso 2: Email existe + auth_id PENDING - Usuario pendiente a activar
        if user.get("auth_id", "").startswith("PENDING_"):
            logger.info(f"Usuario pendiente encontrado para email {email}: {user['user_id']}")
            
            result = update_pending_user(user, auth_id, schema_name, picture_profile_url, full_name)
            if not result:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Error actualizando usuario pendiente"
                )
            
            return LoginResponse(
                success=True,
                user=dict(result["user"]),
                message="Usuario pendiente activado exitosamente",
                is_new_user=False,
                assigned_sector=result["sector"],
                assigned_department=result["department"],
                assigned_seal=result["seal"]
            )
        
        # Caso 3: Email existe + auth_id real - Usuario existente
        logger.info(f"Usuario existente encontrado para email {email}: {user['user_id']}")
        
        # Actualizar último acceso y profile_picture_url si cambió
        if user.get('profile_picture_url') != picture_profile_url:
            # Usar context manager para UPDATE
            try:
                with get_db_connection(schema_name) as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE users SET last_access = %s, profile_picture_url = %s WHERE id = %s",
                        (datetime.utcnow(), picture_profile_url, user["user_id"])
                    )
                    conn.commit()
                user["profile_picture_url"] = picture_profile_url
                logger.info(f"Actualizado profile_picture_url para usuario {user['user_id']}")
            except Exception as e:
                logger.error(f"Error actualizando usuario existente: {e}")
                raise HTTPException(status_code=500, detail=f"Error actualizando usuario: {e}")
        else:
            update_user_last_access(user["user_id"], schema_name)
        
        return LoginResponse(
            success=True,
            user=dict(user),
            message="Login exitoso - usuario existente",
            is_new_user=False
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error en login: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error interno del servidor: {str(e)}"
        )


# @router.get("/user-info")
# async def get_user_info(current_user: AuthenticatedUser = Depends(get_current_user)):
#     """
#     ❌ ENDPOINT NO USADO - Duplica funcionalidad de /api/auth/me
#     
#     Obtiene información del usuario autenticado
#     """
#     try:
#         auth_id = current_user.auth_id
#         if not auth_id:
#             raise HTTPException(
#                 status_code=status.HTTP_400_BAD_REQUEST,
#                 detail="Usuario actual inválido"
#             )
#         
#         user = get_user_by_auth_id(auth_id)
#         if not user:
#             raise HTTPException(
#                 status_code=status.HTTP_404_NOT_FOUND,
#                 detail="Usuario no encontrado"
#             )
#         
#         return {
#             "success": True,
#             "user": dict(user),
#             "message": "Información de usuario obtenida exitosamente"
#         }
#         
#     except HTTPException:
#         raise
#     except Exception as e:
#         logger.error(f"Error obteniendo info de usuario: {e}")
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail=f"Error interno del servidor: {str(e)}"
#         )