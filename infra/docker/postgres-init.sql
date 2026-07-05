-- ==============================================================================
-- Postgres init script — runs ONCE on first container start
-- ==============================================================================

-- pgvector: vector embeddings (used by feedback_billing for NOS retrieval)
CREATE EXTENSION IF NOT EXISTS vector;

-- pg_trgm: trigram search for fuzzy text matching
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- pgcrypto: secure random + hashing utilities
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- uuid-ossp: UUID generation
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
