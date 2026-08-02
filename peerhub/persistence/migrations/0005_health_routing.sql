-- Slice 4 migration: health evaluation, circuit management,
-- recovery probes, admission snapshots, and routing decision audit tables.

PRAGMA foreign_keys = OFF;

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS health_policy_revisions (
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

CREATE TABLE IF NOT EXISTS readiness_observations (
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

CREATE TABLE IF NOT EXISTS operational_observations (
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

CREATE TABLE IF NOT EXISTS operational_projections (
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

CREATE TABLE IF NOT EXISTS health_projections (
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
    updated_at INTEGER NOT NULL CHECK (updated_at >= created_at),
    UNIQUE (instance_id, profile_id),
    FOREIGN KEY (policy_id, policy_revision)
        REFERENCES health_policy_revisions(policy_id, revision)
);

CREATE TABLE IF NOT EXISTS health_circuits (
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

CREATE TABLE IF NOT EXISTS recovery_probe_grants (
    grant_id TEXT PRIMARY KEY,
    circuit_id TEXT NOT NULL REFERENCES health_circuits(circuit_id),
    receipt_incident TEXT NOT NULL,
    receipt_gate_generation INTEGER NOT NULL CHECK (receipt_gate_generation >= 0),
    receipt_timestamp INTEGER NOT NULL CHECK (receipt_timestamp >= 0),
    receipt_fingerprint TEXT NOT NULL,
    authorized_by TEXT NOT NULL,
    authorized_at INTEGER NOT NULL CHECK (authorized_at >= 0),
    remaining_probes INTEGER NOT NULL CHECK (remaining_probes IN (0, 1)),
    consumed_at INTEGER CHECK (consumed_at IS NULL OR consumed_at >= 0),
    consumed_by_attempt_id TEXT,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    CHECK (
        (
            remaining_probes = 1
            AND consumed_at IS NULL
            AND consumed_by_attempt_id IS NULL
        )
        OR
        (
            remaining_probes = 0
            AND consumed_at IS NOT NULL
            AND consumed_by_attempt_id IS NOT NULL
        )
    )
);

CREATE TABLE IF NOT EXISTS recovery_probe_receipts (
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

CREATE TABLE IF NOT EXISTS admission_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    digest TEXT NOT NULL,
    configuration_revision INTEGER NOT NULL CHECK (configuration_revision >= 0),
    policy_id TEXT NOT NULL,
    policy_revision INTEGER NOT NULL CHECK (policy_revision >= 1),
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
    FOREIGN KEY (policy_id, policy_revision)
        REFERENCES health_policy_revisions(policy_id, revision)
);

CREATE TABLE IF NOT EXISTS admission_snapshot_entries (
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

CREATE TABLE IF NOT EXISTS route_decisions (
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
    created_at INTEGER NOT NULL CHECK (created_at >= 0),
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

CREATE TABLE IF NOT EXISTS route_candidate_decisions (
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

INSERT INTO schema_migrations(version, name)
VALUES (5, '0005_health_routing');

PRAGMA user_version = 5;

COMMIT;

PRAGMA foreign_keys = ON;
