# Integración GDI-Backend ↔ GDI-AgenteLANG

Documentación de la integración automática para indexación de documentos en RAG.

## Flujo de Integración

```
1. Numerador firma documento
   ↓
2. Backend guarda en official_documents
   ↓
3. Backend hace commit en BD
   ↓
4. Backend notifica a AgenteLANG (ASYNC, SOFT-FAIL)
   ↓
5. AgenteLANG encola documento
   ↓
6. Worker procesa:
   - Obtiene content de official_documents
   - Chunker fragmenta HTML
   - Genera embeddings (OpenAI)
   - Guarda en document_chunks (pgvector)
```

## Punto de Integración

**Archivo**: `services/documents/numerator.py`
**Función**: `sign_document_as_numerator()`
**Línea**: Después del `conn.commit()` (línea ~557)

## Características

### Soft-Fail
La integración es **no bloqueante**:
- Si AgenteLANG no responde → Solo loguea warning
- Si la cola falla → Documento firmado igual
- Si embeddings fallan → Job marcado como failed

### Async
La llamada es asíncrona:
- No bloquea el flujo de firma
- Timeout de 10 segundos
- Sin reintentos (solo encola)

### Idempotente
Múltiples llamadas con mismo document_id no duplican:
- Worker verifica si ya existe en document_chunks
- Actualiza si ya existe

## Configuración

### Variables de Entorno (Backend)

```bash
# GDI-AgenteLANG URL
AGENTE_URL=http://localhost:8004  # Local
# AGENTE_URL=http://gdi-agente.railway.internal:8004  # Production

# API Key interna (compartida con PDFComposer, Notary)
INTERNAL_API_KEY=your-internal-api-key
```

### Variables de Entorno (AgenteLANG)

```bash
# OpenRouter API Key (para LLM + embeddings)
OPENROUTER_API_KEY=sk-or-...

# Database (para guardar chunks)
DATABASE_URL=postgresql://...

# Backend URL (para tools)
GDI_BACKEND_URL=http://localhost:8000

# Internal API Key (debe coincidir con Backend)
INTERNAL_API_KEY=your-internal-api-key
```

## Contrato de API

### Endpoint AgenteLANG

**POST** `/api/v1/index-document`

**Headers**:
```json
{
  "X-API-Key": "your-internal-api-key",
  "Content-Type": "application/json"
}
```

**Body**:
```json
{
  "document_id": "123e4567-e89b-12d3-a456-426614174000",
  "schema_name": "100_test"
}
```

**Response** (202 Accepted):
```json
{
  "status": "queued",
  "job_id": "abc-def-123"
}
```

**Response** (500 Error):
```json
{
  "detail": "Failed to queue document: ..."
}
```

## Logging

### Backend
```
[NUMERATOR] Notificando indexación al AgenteLANG...
[AGENTE_API] Notificando indexación a AgenteLANG
[AGENTE_API]   URL: http://localhost:8004/api/v1/index-document
[AGENTE_API]   Document ID: 123e4567...
[AGENTE_API]   Schema: 100_test
[AGENTE_API] [OK] Documento encolado para indexación
[AGENTE_API]   Job ID: abc-def-123
[NUMERATOR] [OK] Indexación encolada: abc-def-123
```

### Backend (error soft-fail)
```
[NUMERATOR] Notificando indexación al AgenteLANG...
[AGENTE_API] [WARN] Timeout llamando a AgenteLANG (>10s)
[NUMERATOR] [WARN] Indexación falló (non-blocking): Timeout llamando a AgenteLANG (>10s)
```

### AgenteLANG Worker
```
[INDEXING] Processing job: abc-def-123
[INDEXING]   Document: 123e4567...
[INDEXING]   Schema: 100_test
[INDEXING] Fetching document from official_documents...
[INDEXING] Document fetched: 12345 chars
[INDEXING] Chunking HTML content...
[INDEXING] Created 15 chunks
[INDEXING] Generating embeddings...
[INDEXING] Saving chunks to document_chunks...
[INDEXING] [OK] Document indexed successfully (15 chunks)
```

## Testing

### Test Unitario
```bash
cd GDI-Backend
pytest tests/test_agente_integration.py -v
```

### Test Manual (con curl)

1. **Levantar AgenteLANG**:
```bash
cd GDI-AgenteLANG
uvicorn app.main:app --reload --port 8004
```

2. **Simular notificación desde Backend**:
```bash
curl -X POST http://localhost:8004/api/v1/index-document \
  -H "X-API-Key: your-internal-api-key" \
  -H "Content-Type: application/json" \
  -d '{"document_id": "123e4567-e89b-12d3-a456-426614174000", "schema_name": "100_test"}'
```

3. **Verificar cola**:
```bash
curl http://localhost:8004/api/v1/indexing/status \
  -H "X-API-Key: your-internal-api-key"
```

### Test End-to-End

1. Levantar Backend + AgenteLANG + PostgreSQL
2. Firmar documento como numerador
3. Verificar logs de ambos servicios
4. Verificar tabla `document_chunks` en BD

```sql
-- Verificar chunks indexados
SELECT document_id, chunk_index, content_preview, created_at
FROM "100_test".document_chunks
WHERE document_id = '123e4567-e89b-12d3-a456-426614174000'
ORDER BY chunk_index;
```

## Troubleshooting

### Error: "Connection refused"
- Verificar que AgenteLANG esté corriendo en puerto 8004
- Verificar `AGENTE_URL` en .env del Backend

### Error: "401 Unauthorized"
- Verificar que `INTERNAL_API_KEY` coincida en ambos servicios

### Error: "Document not found in official_documents"
- Verificar que el documento exista en `{schema_name}.official_documents`
- Verificar permisos de lectura del schema

### Warning: "Embeddings not configured"
- Verificar `OPENROUTER_API_KEY` en AgenteLANG
- Verificar modelo de embeddings en config

### Job en estado "failed"
- Revisar logs del worker de AgenteLANG
- Verificar tabla `indexing_queue` en BD:
```sql
SELECT * FROM indexing_queue WHERE status = 'failed' ORDER BY created_at DESC;
```

## Arquitectura

### Backend (GDI-Backend)
```
services/documents/numerator.py
  ↓ (después de commit)
services/shared/agente_api.py
  ↓ (HTTP POST async)
AgenteLANG /api/v1/index-document
```

### AgenteLANG (GDI-AgenteLANG)
```
app/api/routes/indexing.py (endpoint)
  ↓
app/indexing/queue.py (enqueue)
  ↓
app/indexing/worker.py (background)
  ↓ (SQL query)
official_documents (read content)
  ↓
app/indexing/chunker.py (HTML → chunks)
  ↓
app/indexing/embeddings.py (OpenAI via OpenRouter)
  ↓
app/db/vectors.py (insert chunks)
  ↓
document_chunks (pgvector)
```

## Próximos Pasos

1. **Monitoreo**: Dashboard para ver estado de indexación
2. **Reintentos**: Re-encolar jobs failed automáticamente
3. **Priorización**: Queue con prioridad para documentos urgentes
4. **Batch**: Indexar múltiples documentos en paralelo
5. **Webhooks**: Notificar al Backend cuando termina indexación

## Referencias

- [GDI-AgenteLANG CLAUDE.md](../../GDI-AgenteLANG/.claude/CLAUDE.md)
- [Indexing Queue](../../GDI-AgenteLANG/app/indexing/queue.py)
- [Indexing Worker](../../GDI-AgenteLANG/app/indexing/worker.py)
