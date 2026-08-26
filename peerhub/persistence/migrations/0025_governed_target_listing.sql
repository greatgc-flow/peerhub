BEGIN IMMEDIATE;

-- Rebuildable query projection. The target JSON remains canonical; these
-- columns are transactionally maintained denormalizations for indexed reads.
ALTER TABLE governed_targets ADD COLUMN target_kind TEXT NOT NULL DEFAULT '';
ALTER TABLE governed_targets ADD COLUMN target_scope TEXT;

UPDATE governed_targets
SET
    target_kind = COALESCE(json_extract(state_json, '$.kind'), ''),
    target_scope = json_extract(state_json, '$.scope');

CREATE INDEX governed_targets_kind_scope_id
    ON governed_targets(target_kind, target_scope, target_id);

INSERT INTO schema_migrations(version, name)
VALUES (25, '0025_governed_target_listing');

PRAGMA user_version = 25;
PRAGMA foreign_key_check;

COMMIT;
