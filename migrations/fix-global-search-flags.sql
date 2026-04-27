-- MIT-2: Fix insecure NULL defaults in global search flags
-- This migration must be applied to ALL tenant schemas
-- Date: 2026-03-06
-- Context: VULN-1 Authorization Bypass remediation
--
-- Ejecutar con: psql -v schema_name='100_test' -f fix-global-search-flags.sql
-- Repetir para cada tenant schema

SET search_path TO :'schema_name';

-- 1. Update NULLs to false
UPDATE users SET can_global_search_cases = false WHERE can_global_search_cases IS NULL;
UPDATE users SET can_global_search_documents = false WHERE can_global_search_documents IS NULL;

-- 2. Set column defaults to false
ALTER TABLE users ALTER COLUMN can_global_search_cases SET DEFAULT false;
ALTER TABLE users ALTER COLUMN can_global_search_documents SET DEFAULT false;

-- 3. Add NOT NULL constraints
ALTER TABLE users ALTER COLUMN can_global_search_cases SET NOT NULL;
ALTER TABLE users ALTER COLUMN can_global_search_documents SET NOT NULL;

-- Verification query (run after migration):
-- SELECT id, name, lastname, can_global_search_cases, can_global_search_documents
-- FROM users
-- WHERE can_global_search_cases = true OR can_global_search_documents = true;
