PRAGMA foreign_keys = OFF;
BEGIN IMMEDIATE;

CREATE TABLE effect_receipts_delivery_fk (
    effect_receipt_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL
        REFERENCES mutation_requests(request_id),
    outbox_event_id TEXT NOT NULL UNIQUE
        REFERENCES effect_deliveries(event_id),
    attempt_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (
        outcome IN ('EFFECT_SUCCEEDED', 'EFFECT_FAILED')
    ),
    completed_at INTEGER NOT NULL CHECK (completed_at >= 0),
    evidence_refs_json TEXT NOT NULL
);

INSERT INTO effect_receipts_delivery_fk (
    effect_receipt_id,
    request_id,
    outbox_event_id,
    attempt_id,
    owner_id,
    outcome,
    completed_at,
    evidence_refs_json
)
SELECT
    effect_receipt_id,
    request_id,
    outbox_event_id,
    attempt_id,
    owner_id,
    outcome,
    completed_at,
    evidence_refs_json
FROM effect_receipts;

DROP TABLE effect_receipts;
ALTER TABLE effect_receipts_delivery_fk
RENAME TO effect_receipts;

INSERT INTO schema_migrations(version, name)
VALUES (15, '0015_effect_receipts_delivery_fk');
PRAGMA user_version = 15;

COMMIT;
PRAGMA foreign_keys = ON;
