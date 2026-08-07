-- Slice 3 migration: generation-based session bindings for CAS rotation.

CREATE TABLE IF NOT EXISTS session_binding_generations (
    workspace_scope_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    generation_id INTEGER NOT NULL,
    conversation_id TEXT NOT NULL,
    state TEXT NOT NULL,
    claim_token TEXT,
    claim_expiry INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (workspace_scope_id, instance_id, profile_id, generation_id)
);

INSERT INTO schema_migrations(version, name)
VALUES (9, '0009_session_binding_generations');

PRAGMA user_version = 9;
