# Sistema Multi-Tenant - GDI Backend

## Introducción

El backend de GDI implementa un sistema multi-tenant que permite que múltiples municipalidades compartan la misma infraestructura mientras mantienen sus datos aislados en schemas de PostgreSQL separados.

## Arquitectura

### Componentes

1. **TenantMiddleware** (`middleware/tenant_middleware.py`)
   - Intercepta cada request
   - Valida acceso del usuario al schema solicitado
   - Configura el `search_path` de PostgreSQL
   - Valida estado del usuario

2. **Tenant Validation** (`shared/tenant_validation.py`)
   - Cache de accesos (30 minutos TTL)
   - Validación contra whitelist de schemas
   - Prevención de SQL injection

3. **Tenant Models** (`models/tenant_models.py`)
   - `TenantAccess`: Acceso a una municipalidad
   - `OnboardingUser`: Usuario básico
   - `OnboardingResponse`: Response multi-tenant
   - `UserProfile`: Perfil en municipalidad

## Flujo de Request

```
1. Request → OPTIONS? → Skip (CORS preflight)
2. Request → Ruta pública (/health, /docs)? → Skip
3. Request → Extraer JWT del header Authorization
4. JWT → Obtener email del usuario
5. Header → Leer X-Tenant-Schema
6. Cache → Validar acceso usuario a schema (cache 30min)
7. Whitelist → Validar schema contra municipalities
8. PostgreSQL → SET search_path TO {schema}, public
9. PostgreSQL → SELECT users WHERE email = $1
10. Validar → estado = 1 (activo)
11. Request.state → Guardar tenant_user_id, schema_name
12. Continuar → Endpoint
```

## Uso en Endpoints

### Acceder a datos del tenant

```python
from fastapi import APIRouter, Request

router = APIRouter()

@router.get("/api/example")
async def example_endpoint(request: Request):
    # Datos del tenant (seteados por middleware)
    user_id = request.state.tenant_user_id
    schema_name = request.state.schema_name
    email = request.state.tenant_email
    correlation_id = request.state.correlation_id

    # Tu lógica aquí
    return {"user_id": user_id, "schema": schema_name}
```

### Rutas públicas (sin tenant)

Para excluir rutas de la validación multi-tenant, agregar en `TenantMiddleware.EXCLUDED_PATHS`:

```python
EXCLUDED_PATHS = {
    "/health",
    "/api/auth/onboarding",
    "/api/public/something",
}
```

## Headers Requeridos

### Request

```http
Authorization: Bearer <JWT_TOKEN>
X-Tenant-Schema: <schema_name>
```

### Response

```http
X-Correlation-ID: <uuid>
```

## Configuración del Cliente (Frontend)

```typescript
// Ejemplo de configuración de axios
axios.interceptors.request.use((config) => {
  const token = getAuthToken();
  const schema = getCurrentTenantSchema(); // "san_miguel", "public", etc.

  config.headers.Authorization = `Bearer ${token}`;
  config.headers["X-Tenant-Schema"] = schema;

  return config;
});
```

## Estados HTTP

| Código | Descripción |
|--------|-------------|
| 400 | Header X-Tenant-Schema faltante o schema inválido |
| 401 | JWT faltante o inválido |
| 403 | Usuario sin acceso al schema o inactivo |
| 404 | Usuario no existe en la municipalidad |
| 500 | Error configurando search_path o validando usuario |

## Cache

### Tenant Access Cache

- **TTL**: 30 minutos
- **Key**: Email del usuario
- **Value**: Lista de TenantAccess
- **Invalidación manual**: `invalidate_user_cache(email)`

### Cuándo invalidar cache

```python
from shared.tenant_validation import invalidate_user_cache

# Cuando se modifican permisos de usuario
invalidate_user_cache("user@example.com")
```

## Seguridad

### SQL Injection Prevention

El middleware valida `schema_name` contra la tabla `municipalities`:

```python
def is_valid_schema(schema_name: str) -> bool:
    """Valida contra whitelist de municipalities"""
    valid_schemas = get_valid_schemas()  # Query a municipalities
    return schema_name in valid_schemas
```

### Validación de Estado

El middleware valida en **CADA request** que el usuario esté activo (`estado = 1`):

```python
if user["estado"] != 1:
    raise HTTPException(status_code=403, detail="Usuario inactivo")
```

Esto NO se cachea para garantizar que usuarios desactivados pierdan acceso inmediatamente.

## Logging

Todos los requests incluyen un `correlation_id` para trazabilidad:

```
[12345678-1234-1234-1234-123456789abc] Request de user@example.com a /api/documents
[12345678-1234-1234-1234-123456789abc] Tenant validation OK - user_id=..., schema=san_miguel
```

## Migraciones Futuras

### Implementar tabla user_registry

Actualmente el sistema está hardcoded para usar `schema=public`. Para implementar multi-tenant real:

1. Crear tabla `user_registry`:
```sql
CREATE TABLE user_registry (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email VARCHAR(100) NOT NULL,
    municipality_id UUID NOT NULL REFERENCES municipalities(id_municipality),
    is_default BOOLEAN DEFAULT false,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW()
);
```

2. Actualizar `get_user_tenants()` en `shared/tenant_validation.py`:
```python
query = """
    SELECT
        m.schema_name,
        m.name as display_name,
        ur.is_default
    FROM user_registry ur
    INNER JOIN municipalities m ON ur.municipality_id = m.id_municipality
    WHERE ur.email = %s AND ur.is_active = true AND m.is_active = true
    ORDER BY ur.is_default DESC, m.name ASC
"""
tenants = execute_query(query, (email.lower(),), fetch=True) or []
```

3. Crear endpoint `/api/tenants/switch` para cambiar de municipalidad.

## Testing

### Testing Mode

En testing mode, el middleware puede omitirse usando header `X-User-ID`:

```python
# En database.py
TESTING_MODE = os.getenv("TESTING_MODE", "true").lower() == "true"
```

### Test de middleware

```python
import pytest
from fastapi.testclient import TestClient

def test_tenant_middleware_missing_header(client: TestClient):
    response = client.get("/api/documents", headers={
        "Authorization": "Bearer valid_token"
        # Falta X-Tenant-Schema
    })
    assert response.status_code == 400

def test_tenant_middleware_invalid_schema(client: TestClient):
    response = client.get("/api/documents", headers={
        "Authorization": "Bearer valid_token",
        "X-Tenant-Schema": "invalid_schema"
    })
    assert response.status_code == 400

def test_tenant_middleware_unauthorized_access(client: TestClient):
    response = client.get("/api/documents", headers={
        "Authorization": "Bearer valid_token",
        "X-Tenant-Schema": "other_municipality"  # Usuario sin acceso
    })
    assert response.status_code == 403
```

## Troubleshooting

### Error: "Header 'X-Tenant-Schema' es requerido"

**Causa**: Cliente no envía header X-Tenant-Schema

**Solución**: Agregar header en todas las requests

### Error: "No tiene permisos para acceder a la municipalidad"

**Causa**: Usuario no tiene acceso al schema en user_registry

**Solución**: Verificar registros en user_registry

### Error: "Usuario no encontrado en municipalidad"

**Causa**: Usuario existe en Auth0 pero no en la tabla users del schema

**Solución**: Crear usuario en el schema correspondiente

### Error: "Usuario inactivo en esta municipalidad"

**Causa**: `users.estado = 0`

**Solución**: Activar usuario: `UPDATE users SET estado = 1 WHERE email = $1`

## Performance

### Optimizaciones implementadas

1. **Cache de tenant access**: 30 min TTL reduce queries a user_registry
2. **Cache de valid schemas**: Reduce queries a municipalities
3. **Connection pooling**: Reutiliza conexiones PostgreSQL
4. **Index en users.email**: Query rápida de validación

### Métricas esperadas

- Validación con cache hit: < 5ms
- Validación con cache miss: < 50ms
- SET search_path: < 2ms
- Query de usuario: < 10ms (con index)

## Roadmap

- [ ] Implementar tabla user_registry
- [ ] Endpoint /api/tenants/switch
- [ ] Endpoint /api/tenants/list (tenants del usuario)
- [ ] Métricas de uso por tenant
- [ ] Logs centralizados por tenant
- [ ] Rate limiting por tenant
