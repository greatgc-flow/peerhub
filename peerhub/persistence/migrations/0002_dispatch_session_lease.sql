-- Slice 2 migration: session bindings, leases, and recovery receipts.

CREATE TABLE IF NOT EXISTS session_bindings (
    workspace_scope_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    conversation_scope TEXT NOT NULL,
    session_id TEXT NOT NULL,
    current_lease_id TEXT,
    adapter_fingerprint TEXT NOT NULL,
    readiness_binding TEXT NOT NULL,
    session_generation INTEGER NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    state TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (workspace_scope_id, instance_id, profile_id, conversation_scope)
);

CREATE TABLE IF NOT EXISTS leases (
    lease_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    owner_principal_id TEXT NOT NULL,
    owner_instance_id TEXT NOT NULL,
    owner_process_pid INTEGER NOT NULL,
    owner_process_creation_time INTEGER NOT NULL,
    owner_peer_id TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL,
    heartbeat_expires_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS recovery_receipts (
    recovery_receipt_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    lease_id TEXT NOT NULL,
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
    post_fencing_token INTEGER NOT NULL,
    FOREIGN KEY (lease_id) REFERENCES leases(lease_id)
);

CREATE INDEX IF NOT EXISTS idx_leases_session_id ON leases(session_id);
