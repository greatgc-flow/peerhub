BEGIN IMMEDIATE;

-- D-CTX Increment 0 (docs/design/peerhub-dctx-proposal-1-2026-09-13.md
-- section 5, D0/Q2/D2 closed 2026-09-14): a forensic, additive-only
-- record of who/how each governance mutation was actually made.
-- Historical rows default to LEGACY/UNKNOWN per the proposal's own
-- spec -- they must never be retroactively described as asserted or
-- verified. New rows always supply LOCAL_OS_ACCOUNT/ASSERTED (or
-- UNKNOWN/UNKNOWN if OS identity resolution itself fails) going
-- forward; DISPATCH_CAPABILITY/VERIFIED are not assigned by any code
-- yet -- that is D-CTX Increment 1, which does not exist. These
-- columns do not participate in payload_digest (D9: hashing
-- credential-instance evidence would reject a legitimate retry) and do
-- not change quorum/authorization, which remains actor_id alone.
ALTER TABLE mutation_requests ADD COLUMN principal_evidence TEXT NOT NULL
    DEFAULT 'LEGACY'
    CHECK (principal_evidence IN ('LOCAL_OS_ACCOUNT', 'DISPATCH_CAPABILITY', 'LEGACY', 'UNKNOWN'));
ALTER TABLE mutation_requests ADD COLUMN actor_binding TEXT NOT NULL
    DEFAULT 'UNKNOWN'
    CHECK (actor_binding IN ('VERIFIED', 'ASSERTED', 'UNKNOWN'));
ALTER TABLE mutation_requests ADD COLUMN resolved_principal TEXT;

INSERT INTO schema_migrations(version, name)
VALUES (35, '0035_mutation_write_provenance');

PRAGMA user_version = 35;
PRAGMA foreign_key_check;

COMMIT;
