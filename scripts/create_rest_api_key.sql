-- =============================================================================
-- Script: Crear API Key para REST API
-- Fecha: 2026-01-28
-- Autor: claude-code
--
-- Este script crea una API Key para la REST API del MCP Server.
-- La API Key se usa con el header X-API-Key en los endpoints /api/v1/*
-- =============================================================================

-- Verificar si ya existe una API Key activa para desarrollo
SELECT id, api_key, name, is_active, expires_at
FROM public.api_keys
WHERE name = 'REST API Key - Desarrollo';

-- Si no existe, crear la API Key
INSERT INTO public.api_keys (
    id,
    api_key,
    municipality_id,
    name,
    description,
    is_active,
    created_at,
    expires_at,
    rate_limit_per_minute,
    created_by
)
SELECT
    gen_random_uuid(),
    'gdi-rest-api-2026-' || md5(random()::text || clock_timestamp()::text),
    '70bcc20d-9820-4656-bdbd-368b89000b71',  -- Muni Del Futuro
    'REST API Key - Desarrollo',
    'API Key para integraciones REST en ambiente de desarrollo',
    true,
    NOW(),
    NOW() + INTERVAL '1 year',
    100,
    'claude-code'
WHERE NOT EXISTS (
    SELECT 1 FROM public.api_keys WHERE name = 'REST API Key - Desarrollo'
)
RETURNING id, api_key, name, expires_at;

-- Mostrar la API Key creada (guardarla de forma segura)
SELECT
    id,
    api_key,
    municipality_id,
    name,
    is_active,
    expires_at,
    rate_limit_per_minute
FROM public.api_keys
WHERE name = 'REST API Key - Desarrollo';
