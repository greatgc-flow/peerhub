PRAGMA foreign_keys = OFF;

BEGIN IMMEDIATE;

DROP TABLE IF EXISTS session_context_projections;

CREATE TABLE session_context_projections (
    projection_id TEXT PRIMARY KEY,
    workspace_scope_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    conversation_scope TEXT NOT NULL DEFAULT 'global',
    generation_id INTEGER NOT NULL,
    observed_tokens INTEGER NOT NULL,
    window_tokens INTEGER NOT NULL,
    source TEXT NOT NULL,
    observed_at INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE (workspace_scope_id, instance_id, profile_id, conversation_scope, generation_id)
);

INSERT INTO schema_migrations(version, name)
VALUES (13, '0013_session_context_conversation_scope');

PRAGMA user_version = 13;

COMMIT;

PRAGMA foreign_keys = ON;
