BEGIN IMMEDIATE;

-- D-CTX workspace trust anchor (Q2/D2, docs/design/peerhub-dctx-trust-anchor-
-- proposal-cc-2026-09-14.md, DIR-006-unanimous per
-- peerhub-dctx-trust-anchor-closure-2026-09-14.md). A row here can only ever
-- exist as a side effect of a real command admission: it is not a
-- cryptographic secret, it is a fact about this specific database
-- ("a credential for this command_id, under this workspace_home_id and
-- activation_epoch, was minted here"). Verification is table membership
-- scoped to all four of credential_id/command_id/workspace_home_id/
-- activation_epoch, never a standalone computation a caller could satisfy
-- without already having write access to this exact database -- this
-- detects ACCIDENTAL confusion (stale/expired/wrong-workspace/wrong-epoch/
-- wrong-command reuse), and explicitly does not resist a compromised
-- same-user process (D3's already-ratified scope boundary), which retains
-- unrestricted direct-SQL access to this table exactly as it does to every
-- other table in this database today.
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

CREATE INDEX dispatch_context_credentials_command_idx
    ON dispatch_context_credentials(command_id);

-- Fast path for restore-epoch invalidation (mirrors migration 0033's
-- capability_leases.revoked_at_epoch companion index pattern).
CREATE INDEX dispatch_context_credentials_epoch_idx
    ON dispatch_context_credentials(workspace_home_id, activation_epoch);

INSERT INTO schema_migrations(version, name)
VALUES (34, '0034_dispatch_context_credentials');

PRAGMA user_version = 34;
PRAGMA foreign_key_check;

COMMIT;
