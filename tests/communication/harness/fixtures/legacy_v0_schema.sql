-- Frozen v0 schema and initialization rows, captured before runtime retirement.
-- Source: main 1d330a1 v0 persistence; archive: legacy/v0-main-final 57a137c.
-- Test fixture only. Do not execute on a user database.
BEGIN TRANSACTION;
CREATE TABLE administrative_recovery_budgets (
    budget_id TEXT PRIMARY KEY,
    window_start INTEGER NOT NULL CHECK (window_start >= 0),
    count INTEGER NOT NULL CHECK (count >= 1),
    revision INTEGER NOT NULL CHECK (revision >= 1)
);
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
CREATE TABLE admission_snapshot_entries (
    snapshot_id TEXT NOT NULL REFERENCES admission_snapshots(snapshot_id),
    instance_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    health_projection_id TEXT NOT NULL REFERENCES health_projections(projection_id),
    health_projection_revision INTEGER NOT NULL CHECK (health_projection_revision >= 1),
    availability_state TEXT NOT NULL,
    admission_state TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    PRIMARY KEY (snapshot_id, instance_id, profile_id)
);
CREATE TABLE admission_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    digest TEXT NOT NULL,
    configuration_revision INTEGER NOT NULL CHECK (configuration_revision >= 0),
    configuration_digest TEXT NOT NULL,
    policy_id TEXT NOT NULL,
    policy_revision INTEGER NOT NULL CHECK (policy_revision >= 1),
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    FOREIGN KEY (policy_id, policy_revision)
        REFERENCES health_policy_revisions(policy_id, revision)
);
CREATE TABLE "broadcast_legs" (
    broadcast_round_id TEXT NOT NULL
        REFERENCES broadcast_rounds(broadcast_round_id),
    leg_target TEXT NOT NULL,
    client_id TEXT NOT NULL,
    client_leg_request_id TEXT NOT NULL,
    command_id TEXT UNIQUE REFERENCES dispatch_requests(command_id),
    leg_state TEXT NOT NULL CHECK (
        leg_state IN (
            'admitting',
            'pending',
            'completed',
            'failed',
            'timed_out'
        )
    ),
    terminal_at INTEGER CHECK (
        terminal_at IS NULL OR terminal_at >= 0
    ),
    PRIMARY KEY (broadcast_round_id, leg_target),
    UNIQUE (client_id, client_leg_request_id),
    CHECK (leg_state IN ('admitting', 'timed_out') OR command_id IS NOT NULL)
);
CREATE TABLE broadcast_rounds (
    broadcast_round_id TEXT PRIMARY KEY,
    wave_of TEXT REFERENCES broadcast_rounds(broadcast_round_id),
    prompt_digest TEXT NOT NULL,
    requested_targets INTEGER NOT NULL CHECK (requested_targets >= 1),
    deadline_at INTEGER CHECK (
        deadline_at IS NULL OR deadline_at >= 0
    ),
    status TEXT NOT NULL CHECK (status IN ('open', 'closed')),
    disposition TEXT CHECK (
        disposition IS NULL
        OR disposition IN (
            'all_completed',
            'partial',
            'none_completed'
        )
    ),
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    closed_at INTEGER CHECK (
        closed_at IS NULL OR closed_at >= created_at
    ),
    CHECK (wave_of IS NULL OR wave_of <> broadcast_round_id)
);
CREATE TABLE "capability_leases" (
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
    previous_attempt_id TEXT REFERENCES dispatch_attempts(attempt_id), revoked_at_epoch INTEGER
    CHECK (revoked_at_epoch IS NULL OR revoked_at_epoch >= 1),
    CHECK (
        (authorized_attempt_number = 1 AND previous_attempt_id IS NULL)
        OR (authorized_attempt_number > 1 AND previous_attempt_id IS NOT NULL)
    ),
    UNIQUE(command_id, authorized_attempt_number)
);
CREATE TABLE client_request_bindings (
    client_id TEXT NOT NULL,
    client_request_id TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    command_id TEXT NOT NULL
        REFERENCES dispatch_requests(command_id),
    admission_receipt_id TEXT NOT NULL
        REFERENCES admission_receipts(admission_receipt_id),
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    PRIMARY KEY (client_id, client_request_id)
);
CREATE TABLE command_idempotency_bindings (
    client_id TEXT NOT NULL,
    command_type TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    command_id TEXT NOT NULL
        REFERENCES dispatch_requests(command_id),
    admission_receipt_id TEXT NOT NULL
        REFERENCES admission_receipts(admission_receipt_id),
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    PRIMARY KEY (
        client_id,
        command_type,
        idempotency_key
    )
);
CREATE TABLE command_ledger (
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
CREATE TABLE consensus_activation (
    singleton INTEGER PRIMARY KEY,
    activated INTEGER NOT NULL,
    activation_epoch INTEGER,
    activated_at INTEGER
);
INSERT INTO "consensus_activation" VALUES(1,0,NULL,NULL);
CREATE TABLE consensus_targets (
    target_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    state_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL,
    target_kind TEXT NOT NULL,
    target_scope TEXT
);
CREATE TABLE consumer_offsets (
    consumer_id TEXT PRIMARY KEY,
    outbox_position INTEGER NOT NULL CHECK (outbox_position >= 1),
    event_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    FOREIGN KEY (outbox_position, event_id) REFERENCES event_log(outbox_position, event_id)
);
CREATE TABLE "dispatch_artifact_manifests" (
    attempt_id TEXT PRIMARY KEY
        REFERENCES dispatch_attempts(attempt_id),
    workspace_scope_id TEXT NOT NULL,
    staging_root_ref TEXT NOT NULL,
    manifest_digest TEXT NOT NULL,
    item_count INTEGER NOT NULL CHECK (item_count >= 0),
    intent_event_id TEXT
        REFERENCES event_log(event_id),
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    consumed_at INTEGER CHECK (
        consumed_at IS NULL OR consumed_at >= 0
    ),
    revision INTEGER NOT NULL CHECK (revision >= 1)
);
CREATE TABLE dispatch_artifacts (
    attempt_id TEXT NOT NULL
        REFERENCES dispatch_artifact_manifests(attempt_id),
    artifact_id TEXT NOT NULL,
    placeholder TEXT NOT NULL,
    workspace_scope_id TEXT NOT NULL,
    staging_ref TEXT NOT NULL,
    access_mode TEXT NOT NULL,
    declared_lifecycle TEXT NOT NULL,
    expected_sha256_hex TEXT,
    expected_length INTEGER CHECK (
        expected_length IS NULL OR expected_length >= 0
    ),
    verified_sha256_hex TEXT,
    verified_length INTEGER CHECK (
        verified_length IS NULL OR verified_length >= 0
    ),
    verified_object_identity_json TEXT,
    state TEXT NOT NULL CHECK (
        state IN (
            'DECLARED',
            'STAGED',
            'VERIFIED',
            'RESERVED',
            'CONSUMED',
            'ORPHANED',
            'CLEANED'
        )
    ),
    failure_code TEXT,
    declared_at INTEGER NOT NULL CHECK (declared_at >= 0),
    staged_at INTEGER CHECK (
        staged_at IS NULL OR staged_at >= 0
    ),
    verified_at INTEGER CHECK (
        verified_at IS NULL OR verified_at >= 0
    ),
    reserved_at INTEGER CHECK (
        reserved_at IS NULL OR reserved_at >= 0
    ),
    consumed_at INTEGER CHECK (
        consumed_at IS NULL OR consumed_at >= 0
    ),
    cleaned_at INTEGER CHECK (
        cleaned_at IS NULL OR cleaned_at >= 0
    ),
    orphaned_at INTEGER CHECK (
        orphaned_at IS NULL OR orphaned_at >= 0
    ),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    PRIMARY KEY (attempt_id, artifact_id)
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
CREATE TABLE dispatch_context_credentials (
    credential_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL
        REFERENCES dispatch_requests(command_id),
    workspace_home_id TEXT NOT NULL,
    activation_epoch INTEGER NOT NULL CHECK (activation_epoch >= 1),
    issued_at INTEGER NOT NULL CHECK (issued_at >= 0),
    expires_at INTEGER NOT NULL CHECK (expires_at > issued_at),
    revoked_at INTEGER CHECK (revoked_at IS NULL OR revoked_at >= 0)
);
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
, required_capability_tier TEXT
    CHECK (
        required_capability_tier IS NULL
        OR required_capability_tier IN (
            'READ_ONLY',
            'WORKTREE_WRITE',
            'GIT_MUTATE',
            'REMOTE_MUTATE'
        )
    ), restore_quarantined INTEGER NOT NULL
    DEFAULT 0 CHECK (restore_quarantined IN (0, 1)));
CREATE TABLE dispatch_transcripts (
    attempt_id TEXT PRIMARY KEY
        REFERENCES dispatch_attempts(attempt_id)
        ON DELETE CASCADE,
    peer_kind TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    transcript_text TEXT NOT NULL,
    created_at INTEGER NOT NULL CHECK (created_at >= 0)
);
CREATE TABLE duty_lease_recovery_receipts (
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
CREATE TABLE duty_leases (
    lease_id TEXT PRIMARY KEY,
    room_id TEXT NOT NULL,
    role TEXT NOT NULL,
    owner_instance_id TEXT NOT NULL,
    owner_profile_id TEXT NOT NULL,
    owner_principal_id TEXT NOT NULL,
    authority_epoch INTEGER NOT NULL,
    term INTEGER NOT NULL,
    challenge_until INTEGER,
    state TEXT NOT NULL,
    heartbeat_expires_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    consecutive_terms_held INTEGER NOT NULL DEFAULT 1,
    UNIQUE(room_id, role, authority_epoch)
);
CREATE TABLE effect_deliveries (
    event_id TEXT PRIMARY KEY REFERENCES event_log(event_id),
    outbox_position INTEGER NOT NULL UNIQUE,
    request_id TEXT NOT NULL REFERENCES mutation_requests(request_id),
    transition_receipt_id TEXT NOT NULL UNIQUE REFERENCES transition_receipts(receipt_id),
    topic TEXT NOT NULL,
    claimed_by TEXT,
    claim_attempt_id TEXT,
    claimed_at INTEGER CHECK (claimed_at IS NULL OR claimed_at >= 0), reconciliation_required INTEGER NOT NULL
    DEFAULT 0 CHECK (reconciliation_required IN (0, 1)),
    CHECK (
        (claimed_by IS NULL AND claim_attempt_id IS NULL AND claimed_at IS NULL)
        OR
        (claimed_by IS NOT NULL AND claim_attempt_id IS NOT NULL AND claimed_at IS NOT NULL)
    ),
    FOREIGN KEY (outbox_position, event_id) REFERENCES event_log(outbox_position, event_id)
);
CREATE TABLE "effect_receipts" (
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
CREATE TABLE evidence_artifacts (
    artifact_id TEXT PRIMARY KEY,
    source_tool_name TEXT NOT NULL,
    content_length INTEGER NOT NULL,
    sha256_hex TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE TABLE governed_targets (
    target_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    state_json TEXT NOT NULL,
    updated_at INTEGER NOT NULL CHECK (updated_at >= 0)
, target_kind TEXT NOT NULL DEFAULT '', target_scope TEXT);
CREATE TABLE health_circuits (
    circuit_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    subject TEXT NOT NULL,
    state TEXT NOT NULL,
    quarantine_authority_class TEXT NOT NULL,
    receipt_incident TEXT,
    receipt_gate_generation INTEGER CHECK (
        receipt_gate_generation IS NULL OR receipt_gate_generation >= 0
    ),
    receipt_timestamp INTEGER CHECK (
        receipt_timestamp IS NULL OR receipt_timestamp >= 0
    ),
    receipt_fingerprint TEXT,
    backoff_count INTEGER NOT NULL CHECK (backoff_count >= 0),
    cooldown_until INTEGER CHECK (
        cooldown_until IS NULL OR cooldown_until >= 0
    ),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    updated_at INTEGER NOT NULL CHECK (updated_at >= created_at),
    UNIQUE (scope, subject),
    CHECK (
        (
            receipt_incident IS NULL
            AND receipt_gate_generation IS NULL
            AND receipt_timestamp IS NULL
            AND receipt_fingerprint IS NULL
        )
        OR
        (
            receipt_incident IS NOT NULL
            AND receipt_gate_generation IS NOT NULL
            AND receipt_timestamp IS NOT NULL
            AND receipt_fingerprint IS NOT NULL
        )
    )
);
CREATE TABLE health_policy_revisions (
    policy_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    readiness_freshness_seconds INTEGER NOT NULL
        CHECK (readiness_freshness_seconds >= 1),
    recovery_backoff_seconds_json TEXT NOT NULL,
    recovery_jitter_fraction REAL NOT NULL
        CHECK (recovery_jitter_fraction >= 0.0 AND recovery_jitter_fraction <= 1.0),
    readiness_observation_threshold INTEGER NOT NULL
        CHECK (readiness_observation_threshold >= 1),
    administrative_recovery_probe_limit INTEGER NOT NULL
        CHECK (administrative_recovery_probe_limit >= 1),
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    PRIMARY KEY (policy_id, revision)
);
CREATE TABLE health_projections (
    projection_id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    availability_state TEXT NOT NULL,
    admission_state TEXT NOT NULL,
    readiness_observation_id TEXT REFERENCES readiness_observations(observation_id),
    operational_projection_id TEXT REFERENCES operational_projections(projection_id),
    operational_projection_revision INTEGER CHECK (
        operational_projection_revision IS NULL OR operational_projection_revision >= 1
    ),
    policy_id TEXT NOT NULL,
    policy_revision INTEGER NOT NULL CHECK (policy_revision >= 1),
    cooldown_until INTEGER CHECK (
        cooldown_until IS NULL OR cooldown_until >= 0
    ),
    evidence_refs_json TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    updated_at INTEGER NOT NULL CHECK (updated_at >= created_at), readiness_evaluation_json TEXT, sealed_runtime_revision TEXT, adapter_declares_probe_safe INTEGER
CHECK (
    adapter_declares_probe_safe IS NULL
    OR adapter_declares_probe_safe IN (0, 1)
),
    UNIQUE (instance_id, profile_id),
    FOREIGN KEY (policy_id, policy_revision)
        REFERENCES health_policy_revisions(policy_id, revision)
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
CREATE TABLE mutation_plans (
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
CREATE TABLE mutation_requests (
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
, principal_evidence TEXT NOT NULL
    DEFAULT 'LEGACY'
    CHECK (principal_evidence IN ('LOCAL_OS_ACCOUNT', 'DISPATCH_CAPABILITY', 'LEGACY', 'UNKNOWN')), actor_binding TEXT NOT NULL
    DEFAULT 'UNKNOWN'
    CHECK (actor_binding IN ('VERIFIED', 'ASSERTED', 'UNKNOWN')), resolved_principal TEXT);
CREATE TABLE operational_observations (
    observation_id TEXT PRIMARY KEY,
    source_event_id TEXT NOT NULL,
    outbox_position INTEGER NOT NULL CHECK (outbox_position >= 1),
    instance_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    transport TEXT NOT NULL,
    operational_failure_category TEXT,
    execution_certainty TEXT NOT NULL,
    process_integrity INTEGER NOT NULL CHECK (process_integrity IN (0, 1)),
    started_at INTEGER CHECK (started_at IS NULL OR started_at >= 0),
    terminal_at INTEGER NOT NULL CHECK (terminal_at >= 0),
    latency INTEGER CHECK (latency IS NULL OR latency >= 0),
    evidence_refs_json TEXT NOT NULL
);
CREATE TABLE operational_projections (
    projection_id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    -- Each of these stores one complete serialized EvidenceValue (state,
    -- source_tag, provider_id, provider_version, observed_at, captured_at,
    -- freshness_ttl, evidence_ref, value) -- NOT just state+value -- so a
    -- projection round-trips its real per-field evidence provenance
    -- instead of losing it.
    failure_category_json TEXT NOT NULL,
    process_integrity_json TEXT NOT NULL,
    latency_json TEXT NOT NULL,
    usage_json TEXT NOT NULL,
    failure_streak INTEGER NOT NULL CHECK (failure_streak >= 0),
    last_terminal_at INTEGER CHECK (
        last_terminal_at IS NULL OR last_terminal_at >= 0
    ),
    evidence_refs_json TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    updated_at INTEGER NOT NULL CHECK (updated_at >= 0),
    UNIQUE (instance_id, profile_id)
);
CREATE TABLE readiness_observations (
    observation_id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    evidence_state TEXT NOT NULL,
    source_tag TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    provider_version TEXT NOT NULL,
    observed_at INTEGER CHECK (observed_at IS NULL OR observed_at >= 0),
    captured_at INTEGER NOT NULL CHECK (captured_at >= 0),
    freshness_ttl INTEGER NOT NULL CHECK (freshness_ttl >= 0),
    evidence_ref TEXT NOT NULL,
    runtime_revision TEXT,
    issued_at INTEGER CHECK (issued_at IS NULL OR issued_at >= 0),
    valid_until INTEGER CHECK (valid_until IS NULL OR valid_until >= 0),
    integrity_verified INTEGER CHECK (integrity_verified IS NULL OR integrity_verified IN (0, 1))
);
CREATE TABLE readiness_projections (
    projection_id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    runtime_revision TEXT NOT NULL,
    issued_at INTEGER NOT NULL CHECK (issued_at >= 0),
    valid_until INTEGER NOT NULL CHECK (valid_until >= 0),
    integrity_verified INTEGER NOT NULL CHECK (integrity_verified IN (0, 1)),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    updated_at INTEGER NOT NULL CHECK (updated_at >= 0),
    UNIQUE (instance_id, profile_id)
);
CREATE TABLE "recovery_probe_grants" (
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
CREATE TABLE recovery_probe_receipts (
    probe_receipt_id TEXT PRIMARY KEY,
    grant_id TEXT NOT NULL REFERENCES recovery_probe_grants(grant_id),
    attempt_id TEXT NOT NULL,
    reported_revision INTEGER NOT NULL CHECK (reported_revision >= 1),
    reported_receipt_incident TEXT NOT NULL,
    reported_receipt_gate_generation INTEGER NOT NULL CHECK (reported_receipt_gate_generation >= 0),
    reported_receipt_timestamp INTEGER NOT NULL CHECK (reported_receipt_timestamp >= 0),
    reported_receipt_fingerprint TEXT NOT NULL,
    result TEXT NOT NULL,
    observed_at INTEGER NOT NULL CHECK (observed_at >= 0),
    evidence_refs_json TEXT NOT NULL
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
CREATE TABLE retry_policies (
    command_id TEXT PRIMARY KEY REFERENCES dispatch_requests(command_id),
    max_attempts INTEGER NOT NULL CHECK (max_attempts >= 1)
);
CREATE TABLE room_participation_sessions (
    session_id TEXT PRIMARY KEY,
    workspace_scope_id TEXT NOT NULL,
    room_id TEXT NOT NULL,
    actor_principal_id TEXT NOT NULL,
    owner_instance_id TEXT NOT NULL,
    owner_profile_id TEXT NOT NULL,
    session_fingerprint TEXT NOT NULL,
    session_generation INTEGER NOT NULL CHECK (session_generation >= 1),
    resume_parent_session_id TEXT,
    state TEXT NOT NULL CHECK (
        state IN ('ACTIVE', 'ENDED', 'EXPIRED', 'ABANDONED')
    ),
    heartbeat_expires_at INTEGER NOT NULL
        CHECK (heartbeat_expires_at >= 0),
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    updated_at INTEGER NOT NULL CHECK (updated_at >= created_at),
    UNIQUE (
        workspace_scope_id,
        room_id,
        actor_principal_id,
        owner_instance_id,
        owner_profile_id,
        session_generation
    ),
    FOREIGN KEY (resume_parent_session_id)
        REFERENCES room_participation_sessions(session_id)
);
CREATE TABLE room_session_events (
    event_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (
        event_type IN (
            'OPENED', 'RESUMED', 'EXPIRED', 'ABANDONED', 'ENDED'
        )
    ),
    at INTEGER NOT NULL CHECK (at >= 0),
    actor_principal_id TEXT NOT NULL,
    FOREIGN KEY (session_id)
        REFERENCES room_participation_sessions(session_id)
);
CREATE TABLE route_candidate_decisions (
    decision_id TEXT NOT NULL REFERENCES route_decisions(decision_id),
    candidate_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    representative_profile_id TEXT NOT NULL,
    eligibility TEXT NOT NULL,
    effective_weight INTEGER NOT NULL CHECK (effective_weight IN (0, 1)),
    exclusion_reason TEXT,
    evidence_refs_json TEXT NOT NULL,
    PRIMARY KEY (decision_id, candidate_id),
    CHECK (
        (
            eligibility = 'ELIGIBLE'
            AND effective_weight = 1
            AND exclusion_reason IS NULL
        )
        OR
        (
            eligibility = 'EXCLUDED'
            AND effective_weight = 0
            AND exclusion_reason IS NOT NULL
        )
    )
);
CREATE TABLE route_decisions (
    decision_id TEXT PRIMARY KEY,
    client_request_id TEXT NOT NULL,
    configuration_revision INTEGER NOT NULL CHECK (configuration_revision >= 0),
    configuration_digest TEXT NOT NULL,
    admission_snapshot_id TEXT NOT NULL REFERENCES admission_snapshots(snapshot_id),
    admission_snapshot_revision INTEGER NOT NULL CHECK (admission_snapshot_revision >= 1),
    admission_snapshot_digest TEXT NOT NULL,
    routing_policy_id TEXT NOT NULL,
    routing_policy_revision INTEGER NOT NULL CHECK (routing_policy_revision >= 1),
    audit_seed TEXT,
    selection_index INTEGER CHECK (selection_index IS NULL OR selection_index >= 0),
    selected_candidate_id TEXT,
    created_at INTEGER NOT NULL CHECK (created_at >= 0), required_capability_tier TEXT
    CHECK (
        required_capability_tier IS NULL
        OR required_capability_tier IN (
            'READ_ONLY',
            'WORKTREE_WRITE',
            'GIT_MUTATE',
            'REMOTE_MUTATE'
        )
    ),
    CHECK (
        (
            audit_seed IS NULL
            AND selection_index IS NULL
            AND selected_candidate_id IS NULL
        )
        OR
        (
            audit_seed IS NOT NULL
            AND selection_index IS NOT NULL
            AND selected_candidate_id IS NOT NULL
        )
    )
);
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
INSERT INTO "schema_migrations" VALUES(1,'0001_phase1_kernel');
INSERT INTO "schema_migrations" VALUES(2,'0002_dispatch_session_lease');
INSERT INTO "schema_migrations" VALUES(3,'0003_command_request_attempt');
INSERT INTO "schema_migrations" VALUES(4,'0004_idempotency_aliases');
INSERT INTO "schema_migrations" VALUES(5,'0005_health_routing');
INSERT INTO "schema_migrations" VALUES(6,'0006_recovery_probe_single_flight');
INSERT INTO "schema_migrations" VALUES(7,'0007_health_projection_readiness_context');
INSERT INTO "schema_migrations" VALUES(8,'0008_dispatch_artifact_metadata');
INSERT INTO "schema_migrations" VALUES(9,'0009_session_binding_generations');
INSERT INTO "schema_migrations" VALUES(10,'0010_session_context_telemetry');
INSERT INTO "schema_migrations" VALUES(11,'0011_admission_snapshot_configuration_digest');
INSERT INTO "schema_migrations" VALUES(12,'0012_session_rotation_conversation_scope');
INSERT INTO "schema_migrations" VALUES(13,'0013_session_context_conversation_scope');
INSERT INTO "schema_migrations" VALUES(14,'0014_event_log_delivery_split');
INSERT INTO "schema_migrations" VALUES(15,'0015_effect_receipts_delivery_fk');
INSERT INTO "schema_migrations" VALUES(16,'0016_dispatch_artifact_manifests_event_log_fk');
INSERT INTO "schema_migrations" VALUES(17,'0017_drop_legacy_outbox');
INSERT INTO "schema_migrations" VALUES(18,'0018_capability_leases');
INSERT INTO "schema_migrations" VALUES(19,'0019_route_decision_capability_tier');
INSERT INTO "schema_migrations" VALUES(20,'0020_broadcast_correlation');
INSERT INTO "schema_migrations" VALUES(21,'0021_broadcast_leg_timeout_state');
INSERT INTO "schema_migrations" VALUES(22,'0022_retry_authority');
INSERT INTO "schema_migrations" VALUES(23,'0023_evidence_artifacts');
INSERT INTO "schema_migrations" VALUES(24,'0024_telemetry_quota_tracking');
INSERT INTO "schema_migrations" VALUES(25,'0025_governed_target_listing');
INSERT INTO "schema_migrations" VALUES(26,'0026_duty_leases');
INSERT INTO "schema_migrations" VALUES(27,'0027_duty_lease_recovery');
INSERT INTO "schema_migrations" VALUES(28,'0028_room_participation_sessions');
INSERT INTO "schema_migrations" VALUES(29,'0029_recovery_probe_grant_lifecycle');
INSERT INTO "schema_migrations" VALUES(30,'0030_administrative_recovery_budget');
INSERT INTO "schema_migrations" VALUES(31,'0031_dispatch_transcripts');
INSERT INTO "schema_migrations" VALUES(32,'0032_workspace_activation_epoch');
INSERT INTO "schema_migrations" VALUES(33,'0033_restore_authority');
INSERT INTO "schema_migrations" VALUES(34,'0034_dispatch_context_credentials');
INSERT INTO "schema_migrations" VALUES(35,'0035_mutation_write_provenance');
INSERT INTO "schema_migrations" VALUES(36,'0036_consensus_v2_targets');
CREATE TABLE session_binding_generations (
    workspace_scope_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    conversation_scope TEXT NOT NULL,
    generation_id INTEGER NOT NULL CHECK (generation_id >= 1),
    conversation_id TEXT NOT NULL,
    state TEXT NOT NULL,
    claim_token TEXT,
    claim_expiry INTEGER,
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    updated_at INTEGER NOT NULL CHECK (updated_at >= 0),
    PRIMARY KEY (workspace_scope_id, instance_id, profile_id, conversation_scope, generation_id)
);
CREATE TABLE session_bindings (
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
CREATE TABLE session_context_observations (
    observation_id TEXT PRIMARY KEY,
    workspace_scope_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    conversation_scope TEXT NOT NULL DEFAULT 'global',
    generation_id INTEGER NOT NULL,
    observed_tokens INTEGER NOT NULL,
    window_tokens INTEGER NOT NULL,
    source TEXT NOT NULL,
    observed_at INTEGER NOT NULL
);
CREATE TABLE session_context_projections (
    projection_id TEXT PRIMARY KEY,
    workspace_scope_id TEXT NOT NULL,
    instance_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    conversation_scope TEXT NOT NULL DEFAULT 'global',
    generation_id INTEGER NOT NULL,
    observed_tokens INTEGER NOT NULL,
    window_tokens INTEGER NOT NULL,
    source TEXT NOT NULL,
    observed_at INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE (workspace_scope_id, instance_id, profile_id, conversation_scope, generation_id)
);
CREATE TABLE transition_receipts (
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
CREATE TABLE usage_observations (
    observation_id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    evidence_state TEXT NOT NULL,
    source_tag TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    provider_version TEXT NOT NULL,
    observed_at INTEGER CHECK (observed_at IS NULL OR observed_at >= 0),
    captured_at INTEGER NOT NULL CHECK (captured_at >= 0),
    freshness_ttl INTEGER NOT NULL CHECK (freshness_ttl >= 0),
    evidence_ref TEXT NOT NULL,
    quota_pool_scope TEXT,
    used_fraction REAL,
    remaining_fraction REAL,
    window_started_at INTEGER CHECK (window_started_at IS NULL OR window_started_at >= 0),
    resets_at INTEGER CHECK (resets_at IS NULL OR resets_at >= 0)
);
CREATE TABLE usage_projections (
    projection_id TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    quota_pool_scope TEXT NOT NULL,
    used_fraction REAL NOT NULL,
    remaining_fraction REAL NOT NULL,
    window_started_at INTEGER NOT NULL CHECK (window_started_at >= 0),
    resets_at INTEGER NOT NULL CHECK (resets_at >= 0),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    updated_at INTEGER NOT NULL CHECK (updated_at >= 0),
    UNIQUE (instance_id, profile_id, quota_pool_scope)
);
CREATE TABLE workspace_identity (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    workspace_home_id TEXT NOT NULL UNIQUE
, activation_epoch INTEGER NOT NULL DEFAULT 1);
INSERT INTO "workspace_identity" VALUES(1,'b7e95cd84ddf4f518862f37235263afc',1);
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
CREATE UNIQUE INDEX dispatch_artifacts_placeholder
ON dispatch_artifacts(attempt_id, placeholder);
CREATE UNIQUE INDEX dispatch_artifacts_staging_ref
ON dispatch_artifacts(workspace_scope_id, staging_ref);
CREATE INDEX dispatch_artifacts_attempt_state
ON dispatch_artifacts(attempt_id, state);
CREATE INDEX effect_deliveries_pending_order ON effect_deliveries(claimed_at, outbox_position);
CREATE TRIGGER broadcast_rounds_wave_parent_must_preexist
BEFORE INSERT ON broadcast_rounds
WHEN NEW.wave_of IS NOT NULL
 AND NOT EXISTS (
     SELECT 1
     FROM broadcast_rounds
     WHERE broadcast_round_id = NEW.wave_of
 )
BEGIN
    SELECT RAISE(ABORT, 'wave_of parent must already exist');
END;
CREATE TRIGGER broadcast_rounds_reject_existing_id
BEFORE INSERT ON broadcast_rounds
WHEN EXISTS (
    SELECT 1
    FROM broadcast_rounds
    WHERE broadcast_round_id = NEW.broadcast_round_id
)
BEGIN
    SELECT RAISE(ABORT, 'broadcast_round_id already exists');
END;
CREATE TRIGGER broadcast_rounds_wave_immutable
BEFORE UPDATE OF wave_of ON broadcast_rounds
WHEN NEW.wave_of IS NOT OLD.wave_of
BEGIN
    SELECT RAISE(ABORT, 'wave_of is immutable after insert');
END;
CREATE INDEX evidence_artifacts_expires_at_idx ON evidence_artifacts(expires_at);
CREATE INDEX governed_targets_kind_scope_id
    ON governed_targets(target_kind, target_scope, target_id);
CREATE UNIQUE INDEX idx_duty_leases_active
ON duty_leases(room_id, role) WHERE state = 'ACTIVE';
CREATE UNIQUE INDEX idx_room_participation_sessions_active
ON room_participation_sessions(
    workspace_scope_id,
    room_id,
    actor_principal_id,
    owner_instance_id,
    owner_profile_id
) WHERE state = 'ACTIVE';
CREATE INDEX idx_room_participation_sessions_room
ON room_participation_sessions(workspace_scope_id, room_id);
CREATE INDEX idx_room_session_events_session
ON room_session_events(session_id, at, event_id);
CREATE UNIQUE INDEX recovery_probe_grants_one_live_per_circuit
ON recovery_probe_grants(circuit_id)
WHERE state IN ('GRANTED', 'CLAIMED');
CREATE TRIGGER restore_no_attempt_insert BEFORE INSERT ON dispatch_attempts
WHEN EXISTS (SELECT 1 FROM dispatch_requests
             WHERE command_id = NEW.command_id AND restore_quarantined = 1)
BEGIN SELECT RAISE(ABORT, 'restore requires dispatch reconciliation'); END;
CREATE TRIGGER restore_no_attempt_update BEFORE UPDATE ON dispatch_attempts
WHEN EXISTS (SELECT 1 FROM dispatch_requests
             WHERE command_id = NEW.command_id AND restore_quarantined = 1)
BEGIN SELECT RAISE(ABORT, 'restore requires dispatch reconciliation'); END;
CREATE TRIGGER restore_no_capability_insert BEFORE INSERT ON capability_leases
WHEN EXISTS (SELECT 1 FROM dispatch_requests
             WHERE command_id = NEW.command_id AND restore_quarantined = 1)
BEGIN SELECT RAISE(ABORT, 'restore revoked command authority'); END;
CREATE TRIGGER restore_no_effect_claim BEFORE UPDATE OF claimed_by, claim_attempt_id, claimed_at
ON effect_deliveries WHEN OLD.reconciliation_required = 1
BEGIN SELECT RAISE(ABORT, 'restore requires effect reconciliation'); END;
CREATE TRIGGER restore_no_effect_receipt BEFORE INSERT ON effect_receipts
WHEN EXISTS (SELECT 1 FROM effect_deliveries
             WHERE event_id = NEW.outbox_event_id AND reconciliation_required = 1)
BEGIN SELECT RAISE(ABORT, 'restore requires effect reconciliation'); END;
CREATE INDEX dispatch_context_credentials_command_idx
    ON dispatch_context_credentials(command_id);
CREATE INDEX dispatch_context_credentials_epoch_idx
    ON dispatch_context_credentials(workspace_home_id, activation_epoch);
CREATE INDEX idx_consensus_targets_kind_scope
    ON consensus_targets(target_kind, target_scope);
DELETE FROM "sqlite_sequence";
COMMIT;
