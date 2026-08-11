PRAGMA foreign_keys = OFF;
BEGIN IMMEDIATE;

-- Primitive A stores correlation only. Peer response content remains
-- in-memory, matching the existing single-peer dispatch path.
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

CREATE TABLE broadcast_legs (
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
    CHECK (leg_state = 'admitting' OR command_id IS NOT NULL)
);

-- A foreign key alone is insufficient here: SQLite validates a multi-row
-- statement after all of its rows are visible. Requiring the parent from a
-- BEFORE INSERT trigger makes every wave edge point to an earlier row.
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

-- INSERT OR REPLACE deletes and reinserts instead of running an UPDATE.
-- Rejecting an existing identity before conflict handling prevents that
-- path (and UPSERT) from bypassing wave immutability.
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

INSERT INTO schema_migrations(version, name)
VALUES (20, '0020_broadcast_correlation');
PRAGMA user_version = 20;

PRAGMA foreign_key_check;

COMMIT;
PRAGMA foreign_keys = ON;
