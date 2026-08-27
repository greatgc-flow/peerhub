BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS room_participation_sessions (
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

CREATE UNIQUE INDEX IF NOT EXISTS idx_room_participation_sessions_active
ON room_participation_sessions(
    workspace_scope_id,
    room_id,
    actor_principal_id,
    owner_instance_id,
    owner_profile_id
) WHERE state = 'ACTIVE';

CREATE INDEX IF NOT EXISTS idx_room_participation_sessions_room
ON room_participation_sessions(workspace_scope_id, room_id);

CREATE TABLE IF NOT EXISTS room_session_events (
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

CREATE INDEX IF NOT EXISTS idx_room_session_events_session
ON room_session_events(session_id, at, event_id);

INSERT INTO schema_migrations(version, name)
VALUES (28, '0028_room_participation_sessions');

PRAGMA user_version = 28;
PRAGMA foreign_key_check;

COMMIT;
