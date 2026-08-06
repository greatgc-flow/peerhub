PRAGMA foreign_keys = OFF;

BEGIN IMMEDIATE;

DROP TABLE IF EXISTS admission_snapshot_entries;
DROP TABLE IF EXISTS route_candidate_decisions;
DROP TABLE IF EXISTS route_decisions;
DROP TABLE IF EXISTS admission_snapshots;

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

INSERT INTO schema_migrations(version, name)
VALUES (11, '0011_admission_snapshot_configuration_digest');

PRAGMA user_version = 11;

COMMIT;

PRAGMA foreign_keys = ON;
