BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS duty_lease_recovery_receipts (
    receipt_id TEXT PRIMARY KEY,
    lease_id TEXT NOT NULL,
    recovered_at INTEGER NOT NULL,
    recovery_actor_principal_id TEXT NOT NULL,
    trigger TEXT NOT NULL,
    evidence_digest TEXT NOT NULL,
    policy_id TEXT NOT NULL,
    policy_revision TEXT NOT NULL,
    FOREIGN KEY (lease_id) REFERENCES duty_leases(lease_id)
);
INSERT INTO schema_migrations(version, name) VALUES (27, '0027_duty_lease_recovery');
PRAGMA user_version = 27;
PRAGMA foreign_key_check;
COMMIT;
