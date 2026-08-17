PRAGMA foreign_keys = OFF;
BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS usage_observations (
    observation_id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    evidence_state TEXT NOT NULL,
    source_tag TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    provider_version TEXT NOT NULL,
    observed_at INTEGER CHECK (observed_at IS NULL OR observed_at >= 0),
    captured_at INTEGER NOT NULL CHECK (captured_at >= 0),
    freshness_ttl INTEGER NOT NULL CHECK (freshness_ttl >= 0),
    evidence_ref TEXT NOT NULL,
    quota_pool_scope TEXT,
    used_fraction REAL,
    remaining_fraction REAL,
    window_started_at INTEGER CHECK (window_started_at IS NULL OR window_started_at >= 0),
    resets_at INTEGER CHECK (resets_at IS NULL OR resets_at >= 0)
);

CREATE TABLE IF NOT EXISTS usage_projections (
    projection_id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    quota_pool_scope TEXT NOT NULL,
    used_fraction REAL NOT NULL,
    remaining_fraction REAL NOT NULL,
    window_started_at INTEGER NOT NULL CHECK (window_started_at >= 0),
    resets_at INTEGER NOT NULL CHECK (resets_at >= 0),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    updated_at INTEGER NOT NULL CHECK (updated_at >= 0),
    UNIQUE (instance_id, profile_id, quota_pool_scope)
);

CREATE TABLE IF NOT EXISTS readiness_projections (
    projection_id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    runtime_revision TEXT NOT NULL,
    issued_at INTEGER NOT NULL CHECK (issued_at >= 0),
    valid_until INTEGER NOT NULL CHECK (valid_until >= 0),
    integrity_verified INTEGER NOT NULL CHECK (integrity_verified IN (0, 1)),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    updated_at INTEGER NOT NULL CHECK (updated_at >= 0),
    UNIQUE (instance_id, profile_id)
);

INSERT INTO schema_migrations(version, name)
VALUES (24, '0024_telemetry_quota_tracking');

PRAGMA user_version = 24;

PRAGMA foreign_key_check;

COMMIT;
PRAGMA foreign_keys = ON;
