PRAGMA foreign_keys = OFF;
BEGIN IMMEDIATE;

-- 1. Create table
CREATE TABLE evidence_artifacts (
    artifact_id TEXT PRIMARY KEY,
    source_tool_name TEXT NOT NULL,
    content_length INTEGER NOT NULL,
    sha256_hex TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);

-- 4. Recreate any indexes
CREATE INDEX evidence_artifacts_expires_at_idx ON evidence_artifacts(expires_at);

-- 5. Record migration in schema_migrations table
INSERT INTO schema_migrations(version, name)
VALUES (23, '0023_evidence_artifacts');
PRAGMA user_version = 23;

-- 6. Fail-closed verification: check foreign keys BEFORE commit
PRAGMA foreign_key_check;

COMMIT;
PRAGMA foreign_keys = ON;
