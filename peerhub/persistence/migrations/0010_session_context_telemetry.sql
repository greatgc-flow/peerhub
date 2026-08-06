-- Slice 4 migration: session context telemetry.

CREATE TABLE IF NOT EXISTS session_context_observations (
    observation_id TEXT PRIMARY KEY,
    workspace_scope_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    conversation_scope TEXT NOT NULL DEFAULT 'global',
    generation_id INTEGER NOT NULL,
    observed_tokens INTEGER NOT NULL,
    window_tokens INTEGER NOT NULL,
    source TEXT NOT NULL,
    observed_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS session_context_projections (
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
    UNIQUE (workspace_scope_id, instance_id, profile_id, generation_id)
);
