PRAGMA foreign_keys = OFF;
BEGIN IMMEDIATE;

-- Step F: Drop legacy outbox tables now that event_log and effect_deliveries are fully active.
DROP TABLE IF EXISTS outbox_events;
DROP TABLE IF EXISTS outbox_checkpoints;

INSERT INTO schema_migrations(version, name)
VALUES (17, '0017_drop_legacy_outbox');
PRAGMA user_version = 17;

PRAGMA foreign_key_check;

COMMIT;
PRAGMA foreign_keys = ON;
