PRAGMA foreign_keys = OFF;
BEGIN IMMEDIATE;

CREATE TABLE event_log (
    outbox_position INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    protocol_major INTEGER NOT NULL CHECK (protocol_major >= 0),
    protocol_minor INTEGER NOT NULL CHECK (protocol_minor >= 0),
    schema_version TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    occurred_at INTEGER NOT NULL CHECK (occurred_at >= 0),
    event_kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    request_id TEXT,
    round_id TEXT,
    evidence_refs_json TEXT NOT NULL,
    predecessor_digest TEXT,
    recovery_context_json TEXT,
    appended_at INTEGER NOT NULL CHECK (appended_at >= occurred_at),
    UNIQUE (outbox_position, event_id)
);

CREATE TABLE consumer_offsets (
    consumer_id TEXT PRIMARY KEY,
    outbox_position INTEGER NOT NULL CHECK (outbox_position >= 1),
    event_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    FOREIGN KEY (outbox_position, event_id) REFERENCES event_log(outbox_position, event_id)
);

CREATE TABLE effect_deliveries (
    event_id TEXT PRIMARY KEY REFERENCES event_log(event_id),
    outbox_position INTEGER NOT NULL UNIQUE,
    request_id TEXT NOT NULL REFERENCES mutation_requests(request_id),
    transition_receipt_id TEXT NOT NULL UNIQUE REFERENCES transition_receipts(receipt_id),
    topic TEXT NOT NULL,
    claimed_by TEXT,
    claim_attempt_id TEXT,
    claimed_at INTEGER CHECK (claimed_at IS NULL OR claimed_at >= 0),
    CHECK (
        (claimed_by IS NULL AND claim_attempt_id IS NULL AND claimed_at IS NULL)
        OR
        (claimed_by IS NOT NULL AND claim_attempt_id IS NOT NULL AND claimed_at IS NOT NULL)
    ),
    FOREIGN KEY (outbox_position, event_id) REFERENCES event_log(outbox_position, event_id)
);
CREATE INDEX effect_deliveries_pending_order ON effect_deliveries(claimed_at, outbox_position);

INSERT INTO schema_migrations(version, name)
VALUES (14, '0014_event_log_delivery_split');
PRAGMA user_version = 14;

COMMIT;
PRAGMA foreign_keys = ON;
