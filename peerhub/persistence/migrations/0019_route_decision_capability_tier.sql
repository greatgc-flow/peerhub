PRAGMA foreign_keys = OFF;
BEGIN IMMEDIATE;

-- Route decisions predate structured capability tiers. Keep legacy rows
-- nullable without inventing authority; all increment-3 writes are typed and
-- non-null, while attempting to load a legacy row fails closed.
ALTER TABLE route_decisions
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

INSERT INTO schema_migrations(version, name)
VALUES (19, '0019_route_decision_capability_tier');
PRAGMA user_version = 19;

PRAGMA foreign_key_check;

COMMIT;
PRAGMA foreign_keys = ON;
