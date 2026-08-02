-- Slice 5 Step 4: artifact lifecycle metadata for the dispatch-intent
-- commitment chain.
--
-- Two tables: dispatch_artifact_manifests (one row per attempt, FK to
-- dispatch_attempts) and dispatch_artifacts (one row per artifact item,
-- FK to the manifest row).  outbox_events remains the sole event journal.
--
-- Lifecycle states: DECLARED -> STAGED -> VERIFIED -> RESERVED -> CONSUMED,
-- plus ORPHANED / CLEANED for the crash/cleanup tail.
-- VERIFIED -> RESERVED is atomic with DISPATCH_INTENT outbox insertion.
-- RESERVED -> CONSUMED is atomic with the terminal outbox event.
-- Physical cleanup (CLEANED) only ever applies to CONSUMED artifacts.
--
-- workspace_scope_id is denormalized onto dispatch_artifacts for the
-- UNIQUE (workspace_scope_id, staging_ref) constraint -- SQLite cannot
-- enforce cross-table uniqueness natively.

PRAGMA foreign_keys = OFF;

BEGIN IMMEDIATE;

CREATE TABLE dispatch_artifact_manifests (
    attempt_id TEXT PRIMARY KEY
        REFERENCES dispatch_attempts(attempt_id),
    workspace_scope_id TEXT NOT NULL,
    staging_root_ref TEXT NOT NULL,
    manifest_digest TEXT NOT NULL,
    item_count INTEGER NOT NULL CHECK (item_count >= 0),
    intent_event_id TEXT
        REFERENCES outbox_events(event_id),
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    consumed_at INTEGER CHECK (
        consumed_at IS NULL OR consumed_at >= 0
    ),
    revision INTEGER NOT NULL CHECK (revision >= 1)
);

CREATE TABLE dispatch_artifacts (
    attempt_id TEXT NOT NULL
        REFERENCES dispatch_artifact_manifests(attempt_id),
    artifact_id TEXT NOT NULL,
    placeholder TEXT NOT NULL,
    workspace_scope_id TEXT NOT NULL,
    staging_ref TEXT NOT NULL,
    access_mode TEXT NOT NULL,
    declared_lifecycle TEXT NOT NULL,
    expected_sha256_hex TEXT,
    expected_length INTEGER CHECK (
        expected_length IS NULL OR expected_length >= 0
    ),
    verified_sha256_hex TEXT,
    verified_length INTEGER CHECK (
        verified_length IS NULL OR verified_length >= 0
    ),
    verified_object_identity_json TEXT,
    state TEXT NOT NULL CHECK (
        state IN (
            'DECLARED',
            'STAGED',
            'VERIFIED',
            'RESERVED',
            'CONSUMED',
            'ORPHANED',
            'CLEANED'
        )
    ),
    failure_code TEXT,
    declared_at INTEGER NOT NULL CHECK (declared_at >= 0),
    staged_at INTEGER CHECK (
        staged_at IS NULL OR staged_at >= 0
    ),
    verified_at INTEGER CHECK (
        verified_at IS NULL OR verified_at >= 0
    ),
    reserved_at INTEGER CHECK (
        reserved_at IS NULL OR reserved_at >= 0
    ),
    consumed_at INTEGER CHECK (
        consumed_at IS NULL OR consumed_at >= 0
    ),
    cleaned_at INTEGER CHECK (
        cleaned_at IS NULL OR cleaned_at >= 0
    ),
    orphaned_at INTEGER CHECK (
        orphaned_at IS NULL OR orphaned_at >= 0
    ),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    PRIMARY KEY (attempt_id, artifact_id)
);

CREATE UNIQUE INDEX dispatch_artifacts_placeholder
ON dispatch_artifacts(attempt_id, placeholder);

CREATE UNIQUE INDEX dispatch_artifacts_staging_ref
ON dispatch_artifacts(workspace_scope_id, staging_ref);

CREATE INDEX dispatch_artifacts_attempt_state
ON dispatch_artifacts(attempt_id, state);

INSERT INTO schema_migrations(version, name)
VALUES (8, '0008_dispatch_artifact_metadata');

PRAGMA user_version = 8;

COMMIT;

PRAGMA foreign_keys = ON;
