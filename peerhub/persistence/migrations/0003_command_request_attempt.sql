-- Slice 3: request/attempt state, server command idempotency,
-- full lease fences, and the canonical protocol-wide outbox.
--
-- There is no ratified way to invent command_id/attempt_id/authority_epoch
-- for an already-persisted Slice 2 lease. Migration therefore fails closed
-- if such rows exist instead of manufacturing authority identities.
--
-- leases.command_id is intentionally not an SQL foreign key: the retained
-- Slice 2 active-lease API creates a fully fenced lease without creating a
-- Slice 3 admission row. Slice 3 admission still binds request, lease,
-- receipt, and both idempotency identities transactionally.

PRAGMA foreign_keys = OFF;

BEGIN IMMEDIATE;

CREATE TEMP TABLE slice3_lease_migration_guard (
    existing_lease_count INTEGER NOT NULL
        CHECK (existing_lease_count = 0)
);

INSERT INTO slice3_lease_migration_guard(existing_lease_count)
SELECT COUNT(*) FROM leases;

DROP TABLE slice3_lease_migration_guard;

DROP INDEX IF EXISTS outbox_events_state_order;
DROP INDEX IF EXISTS idx_leases_session_id;

ALTER TABLE effect_receipts
RENAME TO effect_receipts_slice2;

ALTER TABLE outbox_events
RENAME TO outbox_events_slice2;

ALTER TABLE recovery_receipts
RENAME TO recovery_receipts_slice2;

ALTER TABLE leases
RENAME TO leases_slice2;

CREATE TABLE dispatch_requests (
    command_id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    client_request_id TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    authenticated_principal TEXT NOT NULL,
    command_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    params_json TEXT NOT NULL,
    expected_policy_revision_json TEXT NOT NULL,
    expected_configuration_revision_json TEXT NOT NULL,
    policy_revision_json TEXT NOT NULL,
    configuration_revision_json TEXT NOT NULL,
    completion_contract_json TEXT NOT NULL,
    selected_peer_instance_id TEXT NOT NULL,
    selected_profile_id TEXT NOT NULL,
    route_decision_digest TEXT NOT NULL,
    lease_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL CHECK (
        state IN (
            'ADMITTED',
            'REJECTED_POLICY',
            'PREPARED',
            'FAILED_PRE_DISPATCH',
            'DISPATCH_INTENT',
            'START_UNCERTAIN',
            'RUNNING',
            'CANCELLING',
            'ASSESSING',
            'SUCCEEDED_VERIFIED',
            'DELIVERED_UNVERIFIED',
            'INCOMPLETE',
            'FAILED',
            'INTERRUPTED',
            'CANCELLED'
        )
    ),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    updated_at INTEGER NOT NULL CHECK (updated_at >= created_at),
    terminal_error_code TEXT
);

CREATE TABLE lease_fencing_sequence (
    fencing_token INTEGER PRIMARY KEY AUTOINCREMENT
);

CREATE TABLE leases (
    lease_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    command_id TEXT NOT NULL,
    attempt_id TEXT,
    fencing_token INTEGER NOT NULL CHECK (fencing_token >= 1),
    authority_epoch INTEGER NOT NULL CHECK (authority_epoch >= 0),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    owner_principal_id TEXT NOT NULL,
    owner_instance_id TEXT NOT NULL,
    owner_process_pid INTEGER,
    owner_process_creation_time INTEGER,
    owner_peer_id TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL CHECK (
        state IN (
            'RESERVED',
            'ACTIVE',
            'RENEWED',
            'RELEASED',
            'EXPIRED',
            'FENCING',
            'FENCED',
            'IDENTITY_MISMATCH',
            'OWNERSHIP_LOST',
            'ABANDONED_PRE_SPAWN'
        )
    ),
    heartbeat_expires_at INTEGER NOT NULL
        CHECK (heartbeat_expires_at >= 0),
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    updated_at INTEGER NOT NULL CHECK (updated_at >= created_at),
    CHECK (
        (
            owner_process_pid IS NULL
            AND owner_process_creation_time IS NULL
        )
        OR
        (
            owner_process_pid IS NOT NULL
            AND owner_process_creation_time IS NOT NULL
        )
    ),
    CHECK (
        state IN ('RESERVED', 'ABANDONED_PRE_SPAWN')
        OR (
            attempt_id IS NOT NULL
            AND owner_process_pid IS NOT NULL
            AND owner_process_creation_time IS NOT NULL
        )
    )
);

CREATE TABLE dispatch_attempts (
    attempt_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL
        REFERENCES dispatch_requests(command_id),
    attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1),
    lease_id TEXT NOT NULL REFERENCES leases(lease_id),
    state TEXT NOT NULL CHECK (
        state IN (
            'PREPARED',
            'FAILED_PRE_DISPATCH',
            'DISPATCH_INTENT',
            'START_UNCERTAIN',
            'RUNNING',
            'CANCELLING',
            'ASSESSING',
            'SUCCEEDED_VERIFIED',
            'DELIVERED_UNVERIFIED',
            'INCOMPLETE',
            'FAILED',
            'INTERRUPTED',
            'CANCELLED'
        )
    ),
    execution_certainty TEXT NOT NULL CHECK (
        execution_certainty IN (
            'NOT_STARTED',
            'MAY_HAVE_STARTED',
            'STARTED',
            'TERMINAL'
        )
    ),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    reconciliation_complete INTEGER NOT NULL DEFAULT 0
        CHECK (reconciliation_complete IN (0, 1)),
    result_json TEXT,
    terminal_error_code TEXT,
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    updated_at INTEGER NOT NULL CHECK (updated_at >= created_at),
    UNIQUE (command_id, attempt_number),
    UNIQUE (attempt_id, command_id),
    UNIQUE (attempt_id, lease_id)
);

CREATE UNIQUE INDEX dispatch_attempts_one_active_per_command
ON dispatch_attempts(command_id)
WHERE state NOT IN (
    'FAILED_PRE_DISPATCH',
    'SUCCEEDED_VERIFIED',
    'DELIVERED_UNVERIFIED',
    'INCOMPLETE',
    'FAILED',
    'INTERRUPTED',
    'CANCELLED'
);

CREATE INDEX dispatch_attempts_command_order
ON dispatch_attempts(command_id, attempt_number);

CREATE INDEX idx_leases_session_id
ON leases(session_id);

CREATE INDEX leases_command_attempt
ON leases(command_id, attempt_id);

CREATE TABLE admission_receipts (
    admission_receipt_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL UNIQUE
        REFERENCES dispatch_requests(command_id),
    client_id TEXT NOT NULL,
    client_request_id TEXT NOT NULL,
    command_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    completion_contract_id TEXT NOT NULL,
    lease_id TEXT NOT NULL UNIQUE REFERENCES leases(lease_id),
    policy_revision_json TEXT NOT NULL,
    configuration_revision_json TEXT NOT NULL,
    admitted_at INTEGER NOT NULL CHECK (admitted_at >= 0)
);

CREATE TABLE client_request_bindings (
    client_id TEXT NOT NULL,
    client_request_id TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    command_id TEXT NOT NULL UNIQUE
        REFERENCES dispatch_requests(command_id),
    admission_receipt_id TEXT NOT NULL UNIQUE
        REFERENCES admission_receipts(admission_receipt_id),
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    PRIMARY KEY (client_id, client_request_id)
);

CREATE TABLE command_idempotency_bindings (
    client_id TEXT NOT NULL,
    command_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    command_id TEXT NOT NULL UNIQUE
        REFERENCES dispatch_requests(command_id),
    admission_receipt_id TEXT NOT NULL UNIQUE
        REFERENCES admission_receipts(admission_receipt_id),
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    PRIMARY KEY (
        client_id,
        command_type,
        idempotency_key
    )
);

CREATE TABLE recovery_receipts (
    recovery_receipt_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    lease_id TEXT NOT NULL REFERENCES leases(lease_id),
    detected_at INTEGER NOT NULL,
    recovery_actor_principal_id TEXT NOT NULL,
    trigger TEXT NOT NULL,
    mismatch_dimensions_json TEXT NOT NULL,
    evidence_digest TEXT NOT NULL,
    policy_id TEXT NOT NULL,
    policy_revision INTEGER NOT NULL,
    decision TEXT NOT NULL,
    certainty_before_policy TEXT NOT NULL,
    certainty_after_policy TEXT NOT NULL,
    external_effect_certainty TEXT,
    pre_lifecycle_state TEXT NOT NULL,
    pre_revision INTEGER NOT NULL,
    pre_fencing_token INTEGER NOT NULL,
    post_lifecycle_state TEXT NOT NULL,
    post_revision INTEGER NOT NULL,
    post_fencing_token INTEGER NOT NULL
);

CREATE TABLE outbox_events (
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
    transition_receipt_id TEXT UNIQUE
        REFERENCES transition_receipts(receipt_id),
    topic TEXT,
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

INSERT INTO outbox_events (
    event_id,
    protocol_major,
    protocol_minor,
    schema_version,
    correlation_id,
    occurred_at,
    event_kind,
    payload_json,
    request_id,
    round_id,
    evidence_refs_json,
    predecessor_digest,
    recovery_context_json,
    transition_receipt_id,
    topic,
    state,
    created_at,
    claimed_by,
    claim_attempt_id,
    claimed_at,
    consumed_at
)
SELECT
    old.event_id,
    1,
    0,
    '1.0.0',
    requests.correlation_id,
    old.created_at,
    old.topic,
    old.payload_json,
    old.request_id,
    NULL,
    '[]',
    NULL,
    NULL,
    old.transition_receipt_id,
    old.topic,
    old.state,
    old.created_at,
    old.claimed_by,
    old.claim_attempt_id,
    old.claimed_at,
    old.consumed_at
FROM outbox_events_slice2 AS old
JOIN mutation_requests AS requests
    ON requests.request_id = old.request_id
ORDER BY old.created_at, old.event_id;

CREATE INDEX outbox_events_state_order
ON outbox_events(state, outbox_position);

CREATE INDEX outbox_events_governance_recovery
ON outbox_events(
    state,
    transition_receipt_id,
    outbox_position
);

CREATE TABLE effect_receipts (
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

INSERT INTO effect_receipts (
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
FROM effect_receipts_slice2;

CREATE TABLE outbox_checkpoints (
    consumer_id TEXT PRIMARY KEY,
    outbox_position INTEGER NOT NULL
        CHECK (outbox_position >= 0),
    event_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1)
);

DROP TABLE effect_receipts_slice2;
DROP TABLE outbox_events_slice2;
DROP TABLE recovery_receipts_slice2;
DROP TABLE leases_slice2;

INSERT INTO schema_migrations(version, name)
VALUES (3, '0003_command_request_attempt');

PRAGMA user_version = 3;

COMMIT;

PRAGMA foreign_keys = ON;
