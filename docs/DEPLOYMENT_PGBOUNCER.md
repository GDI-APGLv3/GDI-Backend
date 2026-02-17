# Deployment PgBouncer Transaction Mode - Railway

## Objetivo

Escalar GDI-Backend a 300-500 conexiones concurrentes usando PgBouncer en transaction mode, manteniendo seguridad multi-tenant.

## Checklist de Deployment

### 1. Actualizar Código (Ya hecho)

- ✅ `database.py` modificado para soportar SET LOCAL
- ✅ ContextVar para reutilizar conexiones
- ✅ Reset explícito de search_path
- ✅ Variable de entorno `PGBOUNCER_TRANSACTION_MODE`

### 2. Configurar Railway

#### Variables de Entorno

Agregar en Railway → Variables:

```bash
PGBOUNCER_TRANSACTION_MODE=true
```

#### Configuración PgBouncer (Railway)

Railway detecta automáticamente PgBouncer por puerto 6432. Verificar:

```bash
DB_PORT=6432
```

Si usas PostgreSQL directo (puerto 5432), NO habilites transaction mode:

```bash
# PostgreSQL directo
DB_PORT=5432
PGBOUNCER_TRANSACTION_MODE=false
```

### 3. Testing Pre-Deployment

#### Local (desarrollo)

```bash
# 1. Activar modo en .env
echo 'PGBOUNCER_TRANSACTION_MODE="true"' >> .env

# 2. Ejecutar tests
python scripts/test_pgbouncer_transaction_mode.py

# 3. Verificar logs
uvicorn main:app --reload

# Debe ver:
# [DB_CONNECTION] SET LOCAL search_path TO "100_test", public
# [DB_CONNECTION] Reutilizando conexión existente (llamada anidada)
# [DB_CONNECTION] RESET search_path (antes de putconn)
```

#### Railway (staging)

```bash
# 1. Configurar variable
railway variables set PGBOUNCER_TRANSACTION_MODE=true

# 2. Restart
railway restart

# 3. Ver logs
railway logs

# 4. Test endpoint
curl https://gdi-backend.railway.app/health
```

### 4. Rollback Plan

Si algo sale mal, rollback inmediato:

```bash
# Railway
railway variables set PGBOUNCER_TRANSACTION_MODE=false
railway restart

# Verificar que vuelve a usar SET
railway logs | grep "SET search_path"
# Debe ver: [DB_CONNECTION] SET search_path TO ...
```

### 5. Monitoreo Post-Deployment

#### Métricas a monitorear

| Métrica | Comando | Esperado |
|---------|---------|----------|
| Conexiones activas | Ver en Railway DB | < 50 (antes: 50+) |
| Response time /health | `curl -w "%{time_total}\n"` | < 100ms |
| Errores multi-tenant | Logs "search_path" | 0 errores |
| Memory usage | Railway dashboard | Estable |

#### Queries de Verificación

```sql
-- Ver conexiones activas (Railway DB)
SELECT count(*) as active_connections
FROM pg_stat_activity
WHERE datname = 'railway';

-- Ver search_path actual de todas las conexiones
SELECT pid, usename, application_name, state,
       current_setting('search_path') as search_path
FROM pg_stat_activity
WHERE datname = 'railway';
```

## Validación de Seguridad Multi-Tenant

### Test Manual (Crítico)

```bash
# Terminal 1: Usuario de municipio A
curl -H "Authorization: Bearer $TOKEN_MUNICIPIO_A" \
     https://gdi-backend.railway.app/api/v1/cases/search

# Terminal 2: Usuario de municipio B (simultáneo)
curl -H "Authorization: Bearer $TOKEN_MUNICIPIO_B" \
     https://gdi-backend.railway.app/api/v1/cases/search

# Verificar que cada uno ve SOLO sus datos
```

### Logs a Revisar

```bash
railway logs | grep "SET LOCAL"
# Debe ver una línea por cada request con schema correcto:
# [DB_CONNECTION] SET LOCAL search_path TO "100_municipio_a", public
# [DB_CONNECTION] SET LOCAL search_path TO "200_municipio_b", public
```

## Performance Esperado

| Métrica | Antes | Después | Mejora |
|---------|-------|---------|--------|
| Conexiones por request | 3-5 | 1 | 70-80% |
| Max conexiones concurrentes | 50 | 300-500 | 6-10x |
| Pool exhaustion errors | Frecuentes | Raros | -95% |
| Response time P95 | 500ms | 200ms | -60% |

## Troubleshooting

### Error: "SET LOCAL can only be used in transaction blocks"

**Causa:** Variable `PGBOUNCER_TRANSACTION_MODE=true` con PostgreSQL directo.

**Solución:**
```bash
railway variables set PGBOUNCER_TRANSACTION_MODE=false
railway restart
```

### Error: "Connection pool exhausted"

**Causa:** Pool muy pequeño para carga actual.

**Solución temporal:**
```python
# database.py - Aumentar maxconn
connection_pool = SimpleConnectionPool(
    minconn=5,
    maxconn=100,  # Aumentar de 50 a 100
    dsn=DATABASE_URL
)
```

### Datos de otro tenant aparecen

**CRITICAL - SECURITY BUG**

**Verificar:**
1. Logs deben mostrar `SET LOCAL` y no `SET`:
   ```bash
   railway logs | grep "SET search_path"
   ```

2. Verificar variable de entorno:
   ```bash
   railway variables
   # Debe estar: PGBOUNCER_TRANSACTION_MODE=true
   ```

3. Rollback inmediato si persiste:
   ```bash
   railway variables set PGBOUNCER_TRANSACTION_MODE=false
   railway restart
   ```

## Configuración Railway Recomendada

### Variables de Entorno Mínimas

```bash
# Database
DB_HOST=<railway-pgbouncer-host>
DB_PORT=6432
DB_USER=postgres
DB_PASSWORD=<railway-password>
DB_NAME=railway

# PgBouncer
PGBOUNCER_TRANSACTION_MODE=true

# Auth0
AUTH0_DOMAIN=gdilatam.us.auth0.com
AUTH0_AUDIENCE=https://gdilatam.us.auth0.com/api/v2/
# ... resto de variables ...
```

### Configuración de Railway PgBouncer

En Railway → Database → PgBouncer Config:

```ini
[databases]
railway = host=<postgres-host> port=5432 dbname=railway

[pgbouncer]
pool_mode = transaction
max_client_conn = 500
default_pool_size = 50
server_reset_query = RESET ALL
server_check_query = SELECT 1
```

**CRITICAL:** `server_reset_query = RESET ALL` garantiza que search_path se limpie entre requests.

## Referencias

- [PgBouncer Transaction Mode](https://www.pgbouncer.org/features.html)
- [Railway PgBouncer Docs](https://docs.railway.app/databases/postgresql#pgbouncer)
- [PostgreSQL SET LOCAL](https://www.postgresql.org/docs/current/sql-set.html)

## Changelog

### 2026-01-31
- ✅ Implementado soporte para PgBouncer transaction mode
- ✅ Agregado ContextVar para reutilizar conexiones
- ✅ Cambiado SET → SET LOCAL cuando `PGBOUNCER_TRANSACTION_MODE=true`
- ✅ Agregado reset explícito de search_path
- ✅ Documentación completa de deployment
- ✅ Script de testing automático

---

**Next Steps:**
1. ✅ Código listo para deployment
2. ⏳ Configurar variable en Railway
3. ⏳ Deploy y monitoreo
4. ⏳ Validación de seguridad multi-tenant
5. ⏳ Confirmar reducción de conexiones
