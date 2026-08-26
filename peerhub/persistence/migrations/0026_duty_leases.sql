BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS duty_leases (
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
CREATE UNIQUE INDEX IF NOT EXISTS idx_duty_leases_active
ON duty_leases(room_id, role) WHERE state = 'ACTIVE';

INSERT INTO schema_migrations(version, name) VALUES (26, '0026_duty_leases');

PRAGMA user_version = 26;
PRAGMA foreign_key_check;

COMMIT;
