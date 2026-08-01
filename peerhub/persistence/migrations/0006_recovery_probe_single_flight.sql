-- Slice 4 correction: structurally enforce HR-05's
-- one-live-recovery-grant-per-circuit invariant.

PRAGMA foreign_keys = OFF;

BEGIN IMMEDIATE;

CREATE UNIQUE INDEX recovery_probe_grants_one_live_per_circuit
ON recovery_probe_grants(circuit_id)
WHERE consumed_at IS NULL;

INSERT INTO schema_migrations(version, name)
VALUES (6, '0006_recovery_probe_single_flight');

PRAGMA user_version = 6;

COMMIT;

PRAGMA foreign_keys = ON;
