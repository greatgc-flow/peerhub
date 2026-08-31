BEGIN IMMEDIATE;

CREATE TABLE administrative_recovery_budgets (
    budget_id TEXT PRIMARY KEY,
    window_start INTEGER NOT NULL CHECK (window_start >= 0),
    count INTEGER NOT NULL CHECK (count >= 1),
    revision INTEGER NOT NULL CHECK (revision >= 1)
);

INSERT INTO schema_migrations(version, name)
VALUES (30, '0030_administrative_recovery_budget');

PRAGMA user_version = 30;
PRAGMA foreign_key_check;

COMMIT;
