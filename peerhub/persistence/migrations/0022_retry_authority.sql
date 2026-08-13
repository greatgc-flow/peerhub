PRAGMA foreign_keys = OFF;
BEGIN IMMEDIATE;

CREATE TABLE retry_policies (
    command_id TEXT PRIMARY KEY REFERENCES dispatch_requests(command_id),
    max_attempts INTEGER NOT NULL CHECK (max_attempts >= 1)
);

CREATE TABLE capability_leases_new (
    capability_lease_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL REFERENCES dispatch_requests(command_id),
    admission_receipt_id TEXT NOT NULL REFERENCES admission_receipts(admission_receipt_id),
    session_lease_id TEXT NOT NULL UNIQUE REFERENCES leases(lease_id),
    subject_principal_id TEXT NOT NULL,
    selected_peer_kind TEXT NOT NULL,
    required_tier TEXT NOT NULL CHECK (
        required_tier IN (
            'READ_ONLY',
            'WORKTREE_WRITE',
            'GIT_MUTATE',
            'REMOTE_MUTATE'
        )
    ),
    authorized_tier TEXT NOT NULL CHECK (
        authorized_tier IN (
            'READ_ONLY',
            'WORKTREE_WRITE',
            'GIT_MUTATE',
            'REMOTE_MUTATE'
        )
        AND authorized_tier = required_tier
    ),
    minimum_enforcement TEXT NOT NULL CHECK (
        minimum_enforcement IN (
            'ADVISORY',
            'ENFORCED',
            'CONFINED'
        )
    ),
    selected_peer_instance_id TEXT NOT NULL,
    selected_profile_id TEXT NOT NULL,
    route_decision_digest TEXT NOT NULL,
    policy_revision_json TEXT NOT NULL,
    issuer_id TEXT NOT NULL,
    issued_at INTEGER NOT NULL CHECK (issued_at >= 0),
    expires_at INTEGER CHECK (
        expires_at IS NULL OR expires_at >= issued_at
    ),
    authorized_attempt_number INTEGER NOT NULL CHECK (authorized_attempt_number >= 1),
    previous_attempt_id TEXT REFERENCES dispatch_attempts(attempt_id),
    CHECK (
        (authorized_attempt_number = 1 AND previous_attempt_id IS NULL)
        OR (authorized_attempt_number > 1 AND previous_attempt_id IS NOT NULL)
    ),
    UNIQUE(command_id, authorized_attempt_number)
);

INSERT INTO capability_leases_new (
    capability_lease_id,
    command_id,
    admission_receipt_id,
    session_lease_id,
    subject_principal_id,
    selected_peer_kind,
    required_tier,
    authorized_tier,
    minimum_enforcement,
    selected_peer_instance_id,
    selected_profile_id,
    route_decision_digest,
    policy_revision_json,
    issuer_id,
    issued_at,
    expires_at,
    authorized_attempt_number,
    previous_attempt_id
)
SELECT
    capability_lease_id,
    command_id,
    admission_receipt_id,
    session_lease_id,
    subject_principal_id,
    selected_peer_kind,
    required_tier,
    authorized_tier,
    minimum_enforcement,
    selected_peer_instance_id,
    selected_profile_id,
    route_decision_digest,
    policy_revision_json,
    issuer_id,
    issued_at,
    expires_at,
    1,
    NULL
FROM capability_leases;

DROP TABLE capability_leases;
ALTER TABLE capability_leases_new RENAME TO capability_leases;

INSERT INTO schema_migrations(version, name)
VALUES (22, '0022_retry_authority');
PRAGMA user_version = 22;

PRAGMA foreign_key_check;

COMMIT;
PRAGMA foreign_keys = ON;
