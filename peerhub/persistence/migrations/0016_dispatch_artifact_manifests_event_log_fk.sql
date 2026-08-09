PRAGMA foreign_keys = OFF;
BEGIN IMMEDIATE;

-- Preflight fail-closed check: verify no orphaned intent_event_id exists before rebuilding table
CREATE TEMPORARY TABLE IF NOT EXISTS _migration_0016_preflight (
    valid INTEGER NOT NULL CHECK (valid = 1)
);
INSERT INTO _migration_0016_preflight (valid)
SELECT CASE WHEN EXISTS (
    SELECT 1 FROM dispatch_artifact_manifests
    WHERE intent_event_id IS NOT NULL
      AND intent_event_id NOT IN (SELECT event_id FROM event_log)
) THEN 0 ELSE 1 END;
DROP TABLE _migration_0016_preflight;

CREATE TABLE dispatch_artifact_manifests_event_log_fk (
    attempt_id TEXT PRIMARY KEY
        REFERENCES dispatch_attempts(attempt_id),
    workspace_scope_id TEXT NOT NULL,
    staging_root_ref TEXT NOT NULL,
    manifest_digest TEXT NOT NULL,
    item_count INTEGER NOT NULL CHECK (item_count >= 0),
    intent_event_id TEXT
        REFERENCES event_log(event_id),
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    consumed_at INTEGER CHECK (
        consumed_at IS NULL OR consumed_at >= 0
    ),
    revision INTEGER NOT NULL CHECK (revision >= 1)
);

INSERT INTO dispatch_artifact_manifests_event_log_fk (
    attempt_id,
    workspace_scope_id,
    staging_root_ref,
    manifest_digest,
    item_count,
    intent_event_id,
    created_at,
    consumed_at,
    revision
)
SELECT
    attempt_id,
    workspace_scope_id,
    staging_root_ref,
    manifest_digest,
    item_count,
    intent_event_id,
    created_at,
    consumed_at,
    revision
FROM dispatch_artifact_manifests;

DROP TABLE dispatch_artifact_manifests;

ALTER TABLE dispatch_artifact_manifests_event_log_fk
RENAME TO dispatch_artifact_manifests;

INSERT INTO schema_migrations(version, name)
VALUES (16, '0016_dispatch_artifact_manifests_event_log_fk');
PRAGMA user_version = 16;

PRAGMA foreign_key_check;

COMMIT;
PRAGMA foreign_keys = ON;
