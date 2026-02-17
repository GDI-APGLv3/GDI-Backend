# PgBouncer Transaction Mode - GDI Backend

## Resumen

Modificación de `database.py` para soportar PgBouncer en **transaction mode**, permitiendo escalar a 300-500 conexiones concurrentes manteniendo seguridad multi-tenant.

## Problema

PgBouncer en **transaction mode** reutiliza conexiones entre diferentes requests. Esto causa un problema crítico de seguridad multi-tenant:

```sql
-- Request 1 (tenant: 100_municipio_a)
SET search_path TO "100_municipio_a", public;
SELECT * FROM cases;  -- OK, ve datos de municipio A
-- Transaction termina, conexión vuelve al pool

-- Request 2 (tenant: 200_municipio_b) reutiliza la MISMA conexión
-- PROBLEMA: search_path sigue siendo "100_municipio_a"
SELECT * FROM cases;  -- SECURITY BUG: Ve datos de municipio A!!
```

## Solución

### 1. SET LOCAL en lugar de SET

`SET LOCAL` se resetea automáticamente al final de la transacción:

```python
# ANTES (inseguro en transaction mode)
cursor.execute('SET search_path TO "100_municipio_a", public')

# AHORA (seguro en transaction mode)
cursor.execute('SET LOCAL search_path TO "100_municipio_a", public')
```

### 2. ContextVar para reutilizar conexiones en llamadas anidadas

Evita obtener múltiples conexiones en el mismo request:

```python
# ANTES: Cada llamada anidada obtiene nueva conexión
with get_db_connection(schema) as conn1:  # Conexión #1
    service_a()
        with get_db_connection(schema) as conn2:  # Conexión #2
            service_b()
                with get_db_connection(schema) as conn3:  # Conexión #3
                    ...

# AHORA: Reutiliza la misma conexión
with get_db_connection(schema) as conn1:  # Conexión #1
    service_a()
        with get_db_connection(schema) as conn1:  # Reutiliza #1
            service_b()
                with get_db_connection(schema) as conn1:  # Reutiliza #1
                    ...
```

### 3. Reset explícito de search_path antes de putconn()

Defense in depth - reset manual antes de devolver conexión al pool:

```python
finally:
    if connection and not connection.closed:
        cursor.execute("RESET search_path")
    connection_pool.putconn(connection)
```

## Cambios en Código

### database.py

**Imports:**
```python
from contextvars import ContextVar
```

**ContextVar global:**
```python
_current_connection: ContextVar[Optional[Any]] = ContextVar('db_conn', default=None)
```

**Variable de entorno:**
```python
PGBOUNCER_TRANSACTION_MODE = os.getenv("PGBOUNCER_TRANSACTION_MODE", "false").lower() == "true"
```

**get_db_connection() modificado:**
- Reutiliza conexión si existe en ContextVar
- Usa `SET LOCAL` si `PGBOUNCER_TRANSACTION_MODE=true`
- Hace commit después del yield exitoso
- Reset explícito de search_path en finally

## Configuración

### Variables de Entorno

```bash
# .env
PGBOUNCER_TRANSACTION_MODE="true"
```

### Railway

En Railway variables:
```
PGBOUNCER_TRANSACTION_MODE=true
```

## Testing

### Verificar que funciona correctamente

```python
# Test 1: Llamadas anidadas reutilizan conexión
with get_db_connection("100_test") as conn1:
    print(id(conn1))  # ej: 140123456789
    with get_db_connection("100_test") as conn2:
        print(id(conn2))  # DEBE SER IGUAL: 140123456789
```

### Verificar aislamiento multi-tenant

```python
# Test 2: Diferentes tenants NO comparten datos
with get_db_connection("100_municipio_a"):
    cursor.execute("SELECT current_setting('search_path')")
    # Debe retornar: "100_municipio_a", public

with get_db_connection("200_municipio_b"):
    cursor.execute("SELECT current_setting('search_path')")
    # Debe retornar: "200_municipio_b", public
```

## Beneficios

| Métrica | Antes | Después |
|---------|-------|---------|
| Conexiones por request complejo | 3-5 | 1 |
| Máximo conexiones concurrentes | 50 | 300-500 |
| Seguridad multi-tenant | ✅ (SET) | ✅ (SET LOCAL) |
| Compatible con session mode | ✅ | ✅ |
| Compatible con transaction mode | ❌ | ✅ |

## Compatibilidad

| Modo | SET | SET LOCAL |
|------|-----|-----------|
| PostgreSQL directo | ✅ | ✅ |
| PgBouncer session mode | ✅ | ✅ |
| PgBouncer transaction mode | ❌ INSEGURO | ✅ SEGURO |

**Recomendación:** Habilitar `PGBOUNCER_TRANSACTION_MODE=true` siempre que uses PgBouncer en transaction mode.

## Migración

### Paso 1: Actualizar código
```bash
git pull origin main
```

### Paso 2: Habilitar variable de entorno
```bash
# .env local
echo 'PGBOUNCER_TRANSACTION_MODE="true"' >> .env

# Railway
railway variables set PGBOUNCER_TRANSACTION_MODE=true
```

### Paso 3: Restart
```bash
# Local
uvicorn main:app --reload

# Railway
railway restart
```

### Paso 4: Verificar logs
```bash
# Debes ver:
[DB_CONNECTION] SET LOCAL search_path TO "100_test", public
[DB_CONNECTION] Reutilizando conexión existente (llamada anidada)
[DB_CONNECTION] RESET search_path (antes de putconn)
```

## Troubleshooting

### Error: "SET LOCAL can only be used in transaction blocks"

**Causa:** Estás usando `PGBOUNCER_TRANSACTION_MODE=true` con PostgreSQL directo o PgBouncer en session mode.

**Solución:** Desactivar el modo:
```bash
PGBOUNCER_TRANSACTION_MODE="false"
```

### Conexiones siguen siendo altas

**Causa:** ContextVar no está funcionando (probablemente código async mal configurado).

**Debug:**
```python
# Agregar en get_db_connection()
existing_conn = _current_connection.get()
print(f"[DEBUG] ContextVar tiene conexión: {existing_conn is not None}")
```

### Datos de otro tenant aparecen

**Causa:** search_path no se está configurando correctamente.

**Debug:**
```python
# Verificar search_path actual
with get_db_connection(schema) as conn:
    with conn.cursor() as cursor:
        cursor.execute("SELECT current_setting('search_path')")
        print(f"[DEBUG] search_path actual: {cursor.fetchone()}")
```

## Referencias

- [PgBouncer Pool Modes](https://www.pgbouncer.org/features.html)
- [PostgreSQL SET LOCAL](https://www.postgresql.org/docs/current/sql-set.html)
- [Python contextvars](https://docs.python.org/3/library/contextvars.html)

## Changelog

### 2026-01-31
- Agregado ContextVar `_current_connection` para reutilizar conexiones en llamadas anidadas
- Cambiado `SET` → `SET LOCAL` cuando `PGBOUNCER_TRANSACTION_MODE=true`
- Agregado reset explícito de search_path en finally
- Agregado commit después del yield exitoso
- Agregada variable de entorno `PGBOUNCER_TRANSACTION_MODE`
- Actualizado `.env.example` con nueva variable
