"""D-CTX workspace trust anchor: credential issuance and verification.

Answers Q2/D2 (docs/design/peerhub-dctx-trust-anchor-proposal-cc-2026-09-14.md,
DIR-006-unanimous per peerhub-dctx-trust-anchor-closure-2026-09-14.md). A
credential is not a secret an algorithm verifies out of context -- it is a
row that can only exist as a side effect of a real command admission
(``issue_credential`` is meant to be called inside the same transaction
that writes the corresponding ``dispatch_requests`` row, though this module
takes a plain ``sqlite3.Connection`` and does not itself manage that
transaction, mirroring ``restore_authority.py``'s pattern). Verification is
table membership scoped to ``workspace_home_id``/``activation_epoch``
(opaque, monotonic, not caller-suppliable -- see
``SqliteStateStore.mint_new_epoch``/``mint_new_identity_and_epoch``) and
``command_id``, never a standalone computation a caller could satisfy
without already having write access to this exact database.

This detects ACCIDENTAL confusion (a stale credential from a completed
attempt, a credential presented against the wrong workspace or the wrong
command, a credential replayed across a restore-bumped epoch). It does not
resist a compromised same-user process, which retains unrestricted direct-
SQL access to this table exactly as it does to every other table in this
database today (D3's already-ratified scope boundary) -- this module makes
no attempt to change that, and callers must not represent it as doing so.
"""

from __future__ import annotations

import sqlite3


def issue_credential(
    connection: sqlite3.Connection,
    *,
    credential_id: str,
    command_id: str,
    workspace_home_id: str,
    activation_epoch: int,
    issued_at: int,
    expires_at: int,
) -> None:
    """Record one credential. Caller generates ``credential_id`` (an
    unpredictable token, e.g. ``secrets.token_urlsafe(32)``) and supplies
    the current ``workspace_home_id``/``activation_epoch`` read from this
    same connection's ``workspace_identity`` row -- this function does not
    read them itself, so a caller cannot accidentally bind a credential to
    a stale epoch read earlier in a long-running process."""

    connection.execute(
        """
        INSERT INTO dispatch_context_credentials (
            credential_id, command_id, workspace_home_id,
            activation_epoch, issued_at, expires_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (credential_id, command_id, workspace_home_id, activation_epoch, issued_at, expires_at),
    )


def verify_credential(
    connection: sqlite3.Connection,
    *,
    credential_id: str,
    command_id: str,
    workspace_home_id: str,
    activation_epoch: int,
    now: int,
) -> bool:
    """Return whether this credential authorizes this command in this
    workspace right now. Every field must match exactly -- a mismatch on
    any one (wrong command, wrong workspace, superseded epoch, expired,
    revoked) fails closed rather than degrading to a partial match."""

    row = connection.execute(
        """
        SELECT 1 FROM dispatch_context_credentials
        WHERE credential_id = ?
          AND command_id = ?
          AND workspace_home_id = ?
          AND activation_epoch = ?
          AND revoked_at IS NULL
          AND expires_at > ?
        """,
        (credential_id, command_id, workspace_home_id, activation_epoch, now),
    ).fetchone()
    return row is not None


def revoke_credentials_below_epoch(
    connection: sqlite3.Connection,
    *,
    workspace_home_id: str,
    minimum_epoch: int,
    revoked_at: int,
) -> None:
    """Revoke every non-revoked credential minted under an earlier epoch.

    Meant to be called alongside ``restore_authority.invalidate_restored_
    authority`` (same transaction, same restore event) so a credential
    minted before a restore cannot be replayed against the post-restore
    epoch -- D12's "restoring a credential ledger cannot revive a saved
    bearer," extended from dispatch/session authority to D-CTX credentials.
    """

    connection.execute(
        """
        UPDATE dispatch_context_credentials
        SET revoked_at = ?
        WHERE workspace_home_id = ?
          AND activation_epoch < ?
          AND revoked_at IS NULL
        """,
        (revoked_at, workspace_home_id, minimum_epoch),
    )
