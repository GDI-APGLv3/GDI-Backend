# Resumen de Implementación: Backend → AgenteLANG

Conexión automática para indexación de documentos en RAG.

## Archivos Modificados

### 1. `services/documents/numerator.py`
**Cambio**: Agregado llamado a AgenteLANG después del commit exitoso (línea ~557)

```python
# Después del conn.commit()
from services.shared.agente_api import notify_document_indexing

indexing_result = await notify_document_indexing(
    document_id=document_id,
    schema_name=schema_name or "100_test"
)
```

**Características**:
- SOFT-FAIL: Si falla, solo loguea warning
- ASYNC: No bloquea el flujo de firma
- POST-COMMIT: Solo se ejecuta después de commit exitoso

### 2. `.env.example`
**Cambio**: Agregadas variables de entorno para AgenteLANG

```bash
AGENTE_URL=http://localhost:8004
INTERNAL_API_KEY=your-internal-api-key
```

## Archivos Nuevos

### 1. `services/shared/agente_api.py` (115 líneas)
Cliente HTTP para integración con GDI-AgenteLANG.

**Función principal**:
```python
async def notify_document_indexing(
    document_id: str,
    schema_name: str
) -> Dict[str, Any]
```

**Características**:
- Timeout: 10 segundos
- Sin reintentos (solo encola)
- Manejo completo de errores (timeout, conexión, servicio)
- Logging detallado
- Retorna dict con success/error/message

### 2. `tests/test_agente_integration.py` (145 líneas)
Tests unitarios con mocks para verificar integración.

**Tests incluidos**:
- `test_notify_document_indexing_success`: Éxito (202)
- `test_notify_document_indexing_service_error`: Error 500 (soft-fail)
- `test_notify_document_indexing_timeout`: Timeout (soft-fail)
- `test_notify_document_indexing_connection_error`: Conexión (soft-fail)
- `test_notify_document_indexing_unexpected_error`: Excepción (soft-fail)

**Ejecutar**:
```bash
pytest tests/test_agente_integration.py -v
```

### 3. `scripts/test_agente_connection.py` (75 líneas)
Script manual para probar conexión con AgenteLANG.

**Ejecutar**:
```bash
cd GDI-Backend
python scripts/test_agente_connection.py
```

### 4. `docs/AGENTE_INTEGRATION.md` (250 líneas)
Documentación completa de la integración.

**Contenido**:
- Flujo de integración
- Configuración de variables de entorno
- Contrato de API
- Logging
- Testing (unitario, manual, e2e)
- Troubleshooting
- Arquitectura

## Flujo Implementado

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Numerador firma documento                                 │
│    (services/documents/numerator.py)                         │
└────────────────────┬────────────────────────────────────────┘
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. Backend inserta en official_documents                     │
│    INSERT INTO {schema}.official_documents                   │
└────────────────────┬────────────────────────────────────────┘
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. Backend hace commit                                       │
│    conn.commit()                                             │
└────────────────────┬────────────────────────────────────────┘
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. Backend notifica a AgenteLANG (ASYNC, SOFT-FAIL)         │
│    POST /api/v1/index-document                               │
│    {"document_id": "...", "schema_name": "..."}              │
└────────────────────┬────────────────────────────────────────┘
                     ▼
                ┌────────┐
                │ SUCCESS │────► Documento encolado (job_id)
                └────────┘       └─► Worker procesa en background
                     │
                ┌────────┐
                │  FAIL  │────► Solo loguea warning
                └────────┘       └─► Firma exitosa igual
```

## Variables de Entorno Requeridas

### Backend (.env)
```bash
# AgenteLANG
AGENTE_URL=http://localhost:8004
INTERNAL_API_KEY=your-internal-api-key

# Existentes (no modificadas)
DATABASE_URL=postgresql://...
AUTH0_DOMAIN=...
# ... resto ...
```

### AgenteLANG (.env)
```bash
# Ya existentes, no requiere cambios
OPENROUTER_API_KEY=sk-or-...
DATABASE_URL=postgresql://...
GDI_BACKEND_URL=http://localhost:8000
INTERNAL_API_KEY=your-internal-api-key  # Debe coincidir con Backend
```

## Testing

### 1. Test Unitario (con mocks)
```bash
cd GDI-Backend
pytest tests/test_agente_integration.py -v
```

### 2. Test Manual (script Python)
```bash
# Terminal 1: Levantar AgenteLANG
cd GDI-AgenteLANG
uvicorn app.main:app --reload --port 8004

# Terminal 2: Probar conexión
cd GDI-Backend
python scripts/test_agente_connection.py
```

### 3. Test End-to-End (con curl)
```bash
# Simular notificación
curl -X POST http://localhost:8004/api/v1/index-document \
  -H "X-API-Key: your-internal-api-key" \
  -H "Content-Type: application/json" \
  -d '{"document_id": "123e4567-e89b-12d3-a456-426614174000", "schema_name": "100_test"}'

# Verificar cola
curl http://localhost:8004/api/v1/indexing/status \
  -H "X-API-Key: your-internal-api-key"
```

## Logging Esperado

### Éxito
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

### Soft-Fail (AgenteLANG no disponible)
```
[NUMERATOR] Notificando indexación al AgenteLANG...
[AGENTE_API] [WARN] Timeout llamando a AgenteLANG (>10s)
[NUMERATOR] [WARN] Indexación falló (non-blocking): Timeout llamando a AgenteLANG (>10s)
```

## Verificación en BD

Después de indexar, verificar chunks en pgvector:

```sql
-- Ver chunks indexados
SELECT
    document_id,
    chunk_index,
    LEFT(chunk_text, 100) as preview,
    created_at
FROM "100_test".document_chunks
WHERE document_id = '123e4567-e89b-12d3-a456-426614174000'
ORDER BY chunk_index;

-- Ver jobs en cola
SELECT * FROM indexing_queue
ORDER BY created_at DESC
LIMIT 10;
```

## Checklist de Deployment

### Backend (GDI-Backend)
- [ ] Agregar `AGENTE_URL` a variables de entorno en Railway
- [ ] Agregar `INTERNAL_API_KEY` a variables de entorno en Railway
- [ ] Verificar que apunta a URL interna de AgenteLANG
- [ ] Re-deploy Backend

### AgenteLANG (GDI-AgenteLANG)
- [ ] Verificar `INTERNAL_API_KEY` coincide con Backend
- [ ] Verificar `OPENROUTER_API_KEY` configurada
- [ ] Verificar `DATABASE_URL` configurada
- [ ] Re-deploy AgenteLANG

### Base de Datos
- [ ] Verificar tabla `indexing_queue` existe en schema `public`
- [ ] Verificar tabla `document_chunks` existe en cada schema de tenant
- [ ] Verificar extensión `pgvector` instalada

## Métricas de Éxito

1. **Firma de documento**: Debe completarse exitosamente (con o sin AgenteLANG)
2. **Notificación**: Debe aparecer en logs del Backend
3. **Encolado**: Job debe aparecer en `indexing_queue` con status=pending
4. **Procesamiento**: Worker debe procesar y cambiar status a completed
5. **Chunks**: Deben aparecer en `document_chunks` con embeddings

## Rollback Plan

Si hay problemas en producción:

1. **Desactivar notificación** (sin revertir código):
```bash
# En Railway, setear:
AGENTE_URL=http://localhost:9999  # URL inválida
# La integración fallará soft-fail y no bloqueará firmas
```

2. **Revertir commit**:
```bash
git revert <commit-hash>
git push origin main
```

3. **Re-deploy Backend** sin la integración

## Próximos Pasos

1. **Monitoreo**: Agregar métricas de indexación al dashboard
2. **Alertas**: Notificar si tasa de fallo > 50%
3. **Reintentos**: Auto-reintentar jobs failed
4. **Batch**: Indexar múltiples documentos en paralelo
5. **Priorización**: Queue con prioridad para documentos urgentes

## Referencias

- [Documentación completa](./AGENTE_INTEGRATION.md)
- [Tests unitarios](../tests/test_agente_integration.py)
- [Script de prueba](../scripts/test_agente_connection.py)
- [AgenteLANG CLAUDE.md](../../GDI-AgenteLANG/.claude/CLAUDE.md)
