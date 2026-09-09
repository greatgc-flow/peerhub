BEGIN IMMEDIATE;

CREATE TABLE dispatch_transcripts (
    attempt_id TEXT PRIMARY KEY
        REFERENCES dispatch_attempts(attempt_id)
        ON DELETE CASCADE,
    peer_kind TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    transcript_text TEXT NOT NULL,
    created_at INTEGER NOT NULL CHECK (created_at >= 0)
);

INSERT INTO schema_migrations(version, name)
VALUES (31, '0031_dispatch_transcripts');

PRAGMA user_version = 31;
PRAGMA foreign_key_check;

COMMIT;
