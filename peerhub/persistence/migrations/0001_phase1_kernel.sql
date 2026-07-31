BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS workspace_identity (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    workspace_home_id TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS governed_targets (
    target_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    state_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL CHECK (updated_at >= 0)
);

CREATE TABLE IF NOT EXISTS mutation_requests (
    request_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL UNIQUE,
    correlation_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    command_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    policy_revision TEXT NOT NULL,
    target_id TEXT NOT NULL,
    expected_revision INTEGER NOT NULL
        CHECK (expected_revision >= 0),
    operation TEXT NOT NULL,
    desired_state_json TEXT NOT NULL,
    effect_kind TEXT NOT NULL,
    effect_payload_json TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    created_at INTEGER NOT NULL CHECK (created_at >= 0)
);

CREATE TABLE IF NOT EXISTS mutation_plans (
    plan_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE
        REFERENCES mutation_requests(request_id),
    request_digest TEXT NOT NULL,
    target_id TEXT NOT NULL,
    previous_revision INTEGER NOT NULL
        CHECK (previous_revision >= 0),
    next_revision INTEGER NOT NULL,
    next_state_json TEXT NOT NULL,
    effect_kind TEXT NOT NULL,
    effect_payload_json TEXT NOT NULL,
    planned_at INTEGER NOT NULL CHECK (planned_at >= 0),
    CHECK (next_revision = previous_revision + 1)
);

CREATE TABLE IF NOT EXISTS transition_receipts (
    receipt_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE
        REFERENCES mutation_requests(request_id),
    plan_id TEXT NOT NULL UNIQUE
        REFERENCES mutation_plans(plan_id),
    target_id TEXT NOT NULL,
    previous_revision INTEGER NOT NULL
        CHECK (previous_revision >= 0),
    next_revision INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (
        status = 'COMMITTED_ENFORCEMENT_PENDING'
    ),
    committed_at INTEGER NOT NULL CHECK (committed_at >= 0),
    outbox_event_id TEXT NOT NULL UNIQUE,
    evidence_refs_json TEXT NOT NULL,
    CHECK (next_revision = previous_revision + 1)
);

CREATE TABLE IF NOT EXISTS command_ledger (
    client_id TEXT NOT NULL,
    command_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    request_id TEXT NOT NULL UNIQUE
        REFERENCES mutation_requests(request_id),
    receipt_id TEXT NOT NULL UNIQUE
        REFERENCES transition_receipts(receipt_id),
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    PRIMARY KEY (
        client_id,
        command_type,
        idempotency_key
    )
);

CREATE TABLE IF NOT EXISTS outbox_events (
    event_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL
        REFERENCES mutation_requests(request_id),
    transition_receipt_id TEXT NOT NULL UNIQUE
        REFERENCES transition_receipts(receipt_id),
    topic TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('PENDING', 'CLAIMED', 'CONSUMED')
    ),
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    claimed_by TEXT,
    claim_attempt_id TEXT,
    claimed_at INTEGER CHECK (
        claimed_at IS NULL OR claimed_at >= 0
    ),
    consumed_at INTEGER CHECK (
        consumed_at IS NULL OR consumed_at >= 0
    ),
    CHECK (
        (
            state = 'PENDING'
            AND claimed_by IS NULL
            AND claim_attempt_id IS NULL
            AND claimed_at IS NULL
            AND consumed_at IS NULL
        )
        OR
        (
            state = 'CLAIMED'
            AND claimed_by IS NOT NULL
            AND claim_attempt_id IS NOT NULL
            AND claimed_at IS NOT NULL
            AND consumed_at IS NULL
        )
        OR
        (
            state = 'CONSUMED'
            AND claimed_by IS NOT NULL
            AND claim_attempt_id IS NOT NULL
            AND claimed_at IS NOT NULL
            AND consumed_at IS NOT NULL
        )
    )
);

CREATE INDEX IF NOT EXISTS outbox_events_state_order
ON outbox_events(state, created_at, event_id);

CREATE TABLE IF NOT EXISTS effect_receipts (
    effect_receipt_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL
        REFERENCES mutation_requests(request_id),
    outbox_event_id TEXT NOT NULL UNIQUE
        REFERENCES outbox_events(event_id),
    attempt_id TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (
        outcome IN ('EFFECT_SUCCEEDED', 'EFFECT_FAILED')
    ),
    completed_at INTEGER NOT NULL CHECK (completed_at >= 0),
    evidence_refs_json TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_migrations(version, name)
VALUES (1, '0001_phase1_kernel');

PRAGMA user_version = 1;

COMMIT;
