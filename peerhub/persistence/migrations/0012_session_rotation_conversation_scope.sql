PRAGMA foreign_keys = OFF;

BEGIN IMMEDIATE;

DROP TABLE IF EXISTS session_binding_generations;

CREATE TABLE session_binding_generations (
    workspace_scope_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    conversation_scope TEXT NOT NULL,
    generation_id INTEGER NOT NULL CHECK (generation_id >= 1),
    conversation_id TEXT NOT NULL,
    state TEXT NOT NULL,
    claim_token TEXT,
    claim_expiry INTEGER,
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    updated_at INTEGER NOT NULL CHECK (updated_at >= 0),
    PRIMARY KEY (workspace_scope_id, instance_id, profile_id, conversation_scope, generation_id)
);

INSERT INTO schema_migrations(version, name)
VALUES (12, '0012_session_rotation_conversation_scope');

PRAGMA user_version = 12;

COMMIT;

PRAGMA foreign_keys = ON;
