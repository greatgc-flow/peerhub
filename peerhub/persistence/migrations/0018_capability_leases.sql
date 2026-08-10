PRAGMA foreign_keys = OFF;
BEGIN IMMEDIATE;

-- Increment 2 introduces durable storage before increment 3 threads the
-- required tier through admission. Keep this constrained but nullable and
-- deliberately omit a default so legacy/current callers cannot receive an
-- implicit authority grant.
ALTER TABLE dispatch_requests
ADD COLUMN required_capability_tier TEXT
    CHECK (
        required_capability_tier IS NULL
        OR required_capability_tier IN (
            'READ_ONLY',
            'WORKTREE_WRITE',
            'GIT_MUTATE',
            'REMOTE_MUTATE'
        )
    );

CREATE TABLE capability_leases (
    capability_lease_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL UNIQUE
        REFERENCES dispatch_requests(command_id),
    admission_receipt_id TEXT NOT NULL UNIQUE
        REFERENCES admission_receipts(admission_receipt_id),
    session_lease_id TEXT NOT NULL UNIQUE
        REFERENCES leases(lease_id),
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
    )
);

INSERT INTO schema_migrations(version, name)
VALUES (18, '0018_capability_leases');
PRAGMA user_version = 18;

PRAGMA foreign_key_check;

COMMIT;
PRAGMA foreign_keys = ON;
