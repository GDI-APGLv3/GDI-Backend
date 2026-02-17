# Configuración Railway: Backend → AgenteLANG

Variables de entorno necesarias para la integración en Railway.

## Backend (GDI-Backend)

### Variables Nuevas

Agregar estas variables en Railway dashboard para GDI-Backend:

```bash
# AgenteLANG Integration
AGENTE_URL=${{GDI-AgenteLANG.RAILWAY_PRIVATE_DOMAIN}}:8004
INTERNAL_API_KEY=your-internal-api-key
```

**IMPORTANTE**: Usar URL interna de Railway (`.railway.internal`) para comunicación entre servicios.

### URL Interna vs Externa

```bash
# ✓ CORRECTO (URL interna, sin latencia)
AGENTE_URL=http://your-service.railway.internal:8004

# ✗ INCORRECTO (URL pública, agrega latencia innecesaria)
AGENTE_URL=https://your-service-production.up.railway.app
```

### Verificar Configuración

En Railway dashboard:
1. Ir a servicio `GDI-Backend`
2. Variables → Raw Editor
3. Buscar `AGENTE_URL` y `INTERNAL_API_KEY`
4. Verificar que apuntan a URL interna

## AgenteLANG (GDI-AgenteLANG)

### Variables Existentes

Estas ya deben estar configuradas (no requiere cambios):

```bash
# OpenRouter (LLM + Embeddings)
OPENROUTER_API_KEY=sk-or-v1-xxxxx

# Database
DATABASE_URL=${{Postgres.DATABASE_URL}}

# Backend Integration (para tools)
GDI_BACKEND_URL=${{GDI-Backend.RAILWAY_PRIVATE_DOMAIN}}:8000

# Internal API Key (debe coincidir con Backend)
INTERNAL_API_KEY=your-internal-api-key
```

### Verificar Configuración

En Railway dashboard:
1. Ir a servicio `GDI-AgenteLANG`
2. Variables → Raw Editor
3. Verificar que `INTERNAL_API_KEY` coincide con Backend

## PostgreSQL (Base de Datos)

### Extensión pgvector

Verificar que la extensión pgvector está instalada:

```sql
-- Conectarse a PostgreSQL via Railway
CREATE EXTENSION IF NOT EXISTS vector;

-- Verificar instalación
SELECT * FROM pg_extension WHERE extname = 'vector';
```

### Tablas Requeridas

Verificar que existen las tablas:

```sql
-- Cola de indexación (schema public)
SELECT * FROM indexing_queue LIMIT 1;

-- Chunks por tenant (schema específico)
SELECT * FROM "100_test".document_chunks LIMIT 1;
```

Si faltan, ejecutar migraciones:
```bash
cd GDI-AgenteLANG
python -m app.db.migrations.create_tables
```

## Testing en Railway

### 1. Verificar Comunicación Interna

SSH a Backend:
```bash
railway run bash  # En proyecto GDI-Backend

# Verificar conectividad
curl -X POST http://your-service.railway.internal:8004/api/v1/index-document \
  -H "X-API-Key: your-internal-api-key" \
  -H "Content-Type: application/json" \
  -d '{"document_id": "test-123", "schema_name": "100_test"}'
```

### 2. Verificar Logs

Ver logs en tiempo real:
```bash
# Backend
railway logs --service=GDI-Backend --follow

# AgenteLANG
railway logs --service=GDI-AgenteLANG --follow
```

Buscar:
- Backend: `[AGENTE_API]` en logs de firma
- AgenteLANG: `[INDEXING]` en logs de worker

### 3. Verificar Health Check

```bash
# Backend health
curl https://your-service-production.up.railway.app/health

# AgenteLANG health
curl https://your-service-production.up.railway.app/health
```

Respuesta esperada de AgenteLANG:
```json
{
  "status": "ok",
  "database": "ok",
  "worker": "running",
  "llm_provider": "ok",
  "embeddings": "configured",
  "version": "1.0.0"
}
```

## Monitoreo

### Dashboard de Railway

1. **CPU/Memory**: Verificar que AgenteLANG no excede límites
2. **Network**: Ver tráfico interno entre servicios
3. **Logs**: Buscar errores de indexación

### Queries de Monitoreo

```sql
-- Jobs pendientes en cola
SELECT COUNT(*) as pending_jobs
FROM indexing_queue
WHERE status = 'pending';

-- Jobs fallidos hoy
SELECT COUNT(*) as failed_jobs
FROM indexing_queue
WHERE status = 'failed'
AND created_at > NOW() - INTERVAL '24 hours';

-- Documentos indexados hoy por tenant
SELECT
    schema_name,
    COUNT(DISTINCT document_id) as documents_indexed
FROM indexing_queue
WHERE status = 'completed'
AND created_at > NOW() - INTERVAL '24 hours'
GROUP BY schema_name;

-- Chunks indexados por documento
SELECT
    document_id,
    COUNT(*) as chunks_count
FROM "100_test".document_chunks
GROUP BY document_id
ORDER BY chunks_count DESC
LIMIT 10;
```

## Troubleshooting Railway

### Error: Connection Refused

**Síntoma**: Backend loguea `[AGENTE_API] [WARN] Error de conexión`

**Solución**:
1. Verificar que AgenteLANG está deployado y corriendo
2. Verificar `AGENTE_URL` usa URL interna (`.railway.internal`)
3. Verificar puerto (debe ser 8004)

```bash
# Correcto
AGENTE_URL=http://your-service.railway.internal:8004

# Incorrecto (falta puerto)
AGENTE_URL=http://your-service.railway.internal

# Incorrecto (URL pública en lugar de interna)
AGENTE_URL=https://your-service-production.up.railway.app
```

### Error: 401 Unauthorized

**Síntoma**: Backend loguea `[AGENTE_API] [WARN] AgenteLANG respondió 401`

**Solución**:
1. Verificar que `INTERNAL_API_KEY` coincide en ambos servicios
2. Re-deploy ambos servicios después de cambiar

```bash
# Backend
INTERNAL_API_KEY=your-internal-api-key

# AgenteLANG (debe coincidir exactamente)
INTERNAL_API_KEY=your-internal-api-key
```

### Error: Embeddings Not Configured

**Síntoma**: Jobs quedan en status `failed` con mensaje "OpenAI API key not configured"

**Solución**:
1. Verificar `OPENROUTER_API_KEY` en AgenteLANG
2. Verificar que la key es válida en OpenRouter

```bash
# En Railway, verificar variable
OPENROUTER_API_KEY=sk-or-v1-xxxxx

# Test manual
curl https://openrouter.ai/api/v1/auth/key \
  -H "Authorization: Bearer sk-or-v1-xxxxx"
```

### Worker No Procesa Jobs

**Síntoma**: Jobs quedan en `pending` indefinidamente

**Solución**:
1. Verificar logs de AgenteLANG
2. Verificar que el worker está corriendo

```bash
railway logs --service=GDI-AgenteLANG --follow | grep INDEXING

# Debería aparecer:
# [INDEXING] Worker started
# [INDEXING] Processing job: xxx
```

Si no aparece, re-deploy AgenteLANG.

## Rollback en Railway

Si hay problemas en producción:

### 1. Desactivar Notificación (Sin Revertir Código)

En Railway dashboard de Backend:
```bash
# Cambiar URL a inválida (soft-fail, no bloqueará firmas)
AGENTE_URL=http://localhost:9999
```

Save → Re-deploy Backend

### 2. Revertir Deployment

```bash
# Ver deployments anteriores
railway deployments list

# Rollback a deployment anterior
railway rollback <deployment-id>
```

### 3. Revertir Código

```bash
# Localmente
git revert <commit-hash>
git push origin main

# Railway auto-deploya el revert
```

## Seguridad

### API Key Rotation

Para rotar `INTERNAL_API_KEY`:

1. Generar nueva key:
```bash
openssl rand -hex 32
```

2. Actualizar en AMBOS servicios simultáneamente:
```bash
# Backend
INTERNAL_API_KEY=nueva-key-aqui

# AgenteLANG
INTERNAL_API_KEY=nueva-key-aqui
```

3. Re-deploy ambos al mismo tiempo

### Network Policy

Railway permite comunicación interna sin restricciones.
No se requiere configuración adicional de firewall.

## Costos

### Llamadas Internas (Gratis)

Backend → AgenteLANG usa red interna de Railway (sin cargo).

### Embeddings (OpenRouter)

Costo por documento indexado:
- Modelo: `openai/text-embedding-3-small`
- Precio: ~$0.00002 por 1K tokens
- Documento promedio: ~5K tokens = $0.0001
- 1000 documentos = ~$0.10

### Almacenamiento (PostgreSQL)

Chunks en pgvector:
- Cada chunk: ~1.5KB (texto + embedding)
- 1000 documentos × 15 chunks = 15K chunks = ~22MB
- Impacto mínimo en BD

## Referencias

- [Railway Docs: Private Networking](https://docs.railway.app/reference/private-networking)
- [Railway Docs: Environment Variables](https://docs.railway.app/develop/variables)
- [OpenRouter Pricing](https://openrouter.ai/docs#models)
