BEGIN IMMEDIATE;

CREATE TABLE consensus_targets (
    target_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    state_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    target_kind TEXT NOT NULL,
    target_scope TEXT
);

CREATE INDEX idx_consensus_targets_kind_scope
    ON consensus_targets(target_kind, target_scope);

CREATE TABLE consensus_activation (
    singleton INTEGER PRIMARY KEY,
    activated INTEGER NOT NULL,
    activation_epoch INTEGER,
    activated_at INTEGER
);

INSERT INTO consensus_activation(singleton, activated) VALUES(1, 0);

INSERT INTO schema_migrations(version, name)
VALUES (36, '0036_consensus_v2_targets');

PRAGMA user_version = 36;
PRAGMA foreign_key_check;

COMMIT;
