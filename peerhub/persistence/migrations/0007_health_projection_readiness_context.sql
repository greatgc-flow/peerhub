-- Slice 4 correction: preserve the readiness evaluation and its
-- comparison inputs on the policy-owned live health projection.
--
-- Columns are nullable only because pre-0007 rows cannot be truthfully
-- backfilled: their sealed runtime revision and adapter probe-safety
-- input were never persisted. New health-service writes always provide
-- all three values and fail closed on a legacy incomplete projection.

PRAGMA foreign_keys = OFF;

BEGIN IMMEDIATE;

ALTER TABLE health_projections
ADD COLUMN readiness_evaluation_json TEXT;

ALTER TABLE health_projections
ADD COLUMN sealed_runtime_revision TEXT;

ALTER TABLE health_projections
ADD COLUMN adapter_declares_probe_safe INTEGER
CHECK (
    adapter_declares_probe_safe IS NULL
    OR adapter_declares_probe_safe IN (0, 1)
);

INSERT INTO schema_migrations(version, name)
VALUES (7, '0007_health_projection_readiness_context');

PRAGMA user_version = 7;

COMMIT;

PRAGMA foreign_keys = ON;
