PRAGMA foreign_keys = OFF;

BEGIN IMMEDIATE;

DROP INDEX recovery_probe_grants_one_live_per_circuit;

CREATE TABLE recovery_probe_grants_new (
    grant_id TEXT PRIMARY KEY,
    circuit_id TEXT NOT NULL REFERENCES health_circuits(circuit_id),
    receipt_incident TEXT NOT NULL,
    receipt_gate_generation INTEGER NOT NULL
        CHECK (receipt_gate_generation >= 0),
    receipt_timestamp INTEGER NOT NULL CHECK (receipt_timestamp >= 0),
    receipt_fingerprint TEXT NOT NULL,
    authorized_by TEXT NOT NULL,
    authorized_at INTEGER NOT NULL CHECK (authorized_at >= 0),
    authorization_mode TEXT NOT NULL CHECK (
        authorization_mode IN ('AUTOMATIC', 'ADMINISTRATIVE')
    ),
    authorized_circuit_revision INTEGER NOT NULL
        CHECK (authorized_circuit_revision >= 1),
    state TEXT NOT NULL CHECK (
        state IN ('GRANTED', 'CLAIMED', 'SUCCEEDED', 'FAILED', 'EXPIRED')
    ),
    expires_at INTEGER NOT NULL CHECK (expires_at > authorized_at),
    consumed_at INTEGER CHECK (consumed_at IS NULL OR consumed_at >= 0),
    consumed_by_attempt_id TEXT,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    CHECK (
        (consumed_at IS NULL AND consumed_by_attempt_id IS NULL)
        OR
        (consumed_at IS NOT NULL AND consumed_by_attempt_id IS NOT NULL)
    ),
    CHECK (
        (state = 'GRANTED' AND consumed_at IS NULL)
        OR
        (state IN ('CLAIMED', 'SUCCEEDED', 'FAILED') AND consumed_at IS NOT NULL)
        OR
        state = 'EXPIRED'
    )
);

INSERT INTO recovery_probe_grants_new (
    grant_id,
    circuit_id,
    receipt_incident,
    receipt_gate_generation,
    receipt_timestamp,
    receipt_fingerprint,
    authorized_by,
    authorized_at,
    authorization_mode,
    authorized_circuit_revision,
    state,
    expires_at,
    consumed_at,
    consumed_by_attempt_id,
    revision
)
SELECT
    grants.grant_id,
    grants.circuit_id,
    grants.receipt_incident,
    grants.receipt_gate_generation,
    grants.receipt_timestamp,
    grants.receipt_fingerprint,
    grants.authorized_by,
    grants.authorized_at,
    'AUTOMATIC',
    circuits.revision,
    CASE
        WHEN grants.remaining_probes = 1 THEN 'GRANTED'
        ELSE 'CLAIMED'
    END,
    grants.authorized_at + 300,
    grants.consumed_at,
    grants.consumed_by_attempt_id,
    grants.revision
FROM recovery_probe_grants AS grants
JOIN health_circuits AS circuits
    ON circuits.circuit_id = grants.circuit_id;

DROP TABLE recovery_probe_grants;
ALTER TABLE recovery_probe_grants_new RENAME TO recovery_probe_grants;

CREATE UNIQUE INDEX recovery_probe_grants_one_live_per_circuit
ON recovery_probe_grants(circuit_id)
WHERE state IN ('GRANTED', 'CLAIMED');

INSERT INTO schema_migrations(version, name)
VALUES (29, '0029_recovery_probe_grant_lifecycle');

PRAGMA user_version = 29;
PRAGMA foreign_key_check;

COMMIT;

PRAGMA foreign_keys = ON;
