BEGIN IMMEDIATE;

ALTER TABLE workspace_identity ADD COLUMN activation_epoch INTEGER NOT NULL DEFAULT 1;

INSERT OR IGNORE INTO schema_migrations(version, name)
VALUES (32, '0032_workspace_activation_epoch');

PRAGMA user_version = 32;

COMMIT;
