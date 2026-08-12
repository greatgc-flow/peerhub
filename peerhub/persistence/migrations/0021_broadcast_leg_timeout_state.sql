PRAGMA foreign_keys = OFF;
BEGIN IMMEDIATE;

CREATE TABLE broadcast_legs_new (
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

INSERT INTO broadcast_legs_new (
    broadcast_round_id,
    leg_target,
    client_id,
    client_leg_request_id,
    command_id,
    leg_state,
    terminal_at
)
SELECT
    broadcast_round_id,
    leg_target,
    client_id,
    client_leg_request_id,
    command_id,
    leg_state,
    terminal_at
FROM broadcast_legs;

DROP TABLE broadcast_legs;
ALTER TABLE broadcast_legs_new RENAME TO broadcast_legs;

INSERT INTO schema_migrations(version, name)
VALUES (21, '0021_broadcast_leg_timeout_state');
PRAGMA user_version = 21;

PRAGMA foreign_key_check;

COMMIT;
PRAGMA foreign_keys = ON;
