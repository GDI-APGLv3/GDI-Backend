
SET search_path TO :'schema_name';

UPDATE users SET can_global_search_cases = false WHERE can_global_search_cases IS NULL;
UPDATE users SET can_global_search_documents = false WHERE can_global_search_documents IS NULL;

ALTER TABLE users ALTER COLUMN can_global_search_cases SET DEFAULT false;
ALTER TABLE users ALTER COLUMN can_global_search_documents SET DEFAULT false;

ALTER TABLE users ALTER COLUMN can_global_search_cases SET NOT NULL;
ALTER TABLE users ALTER COLUMN can_global_search_documents SET NOT NULL;

