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

from .tables import TABLE_DISPATCH_REQUESTS


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


def verify_credential_for_actor(
    connection: sqlite3.Connection,
    *,
    credential_id: str,
    claimed_actor_id: str,
    workspace_home_id: str,
    activation_epoch: int,
    now: int,
) -> bool:
    """Return whether this credential authorizes ``claimed_actor_id`` to
    act as a governance participant, right now, in this workspace.

    A governance mutation (a consensus vote, a Final Call ACK) is a
    *different* command than the ``peer.ask`` dispatch the credential was
    minted for -- ``verify_credential``'s exact ``command_id`` match does
    not apply here. Instead this joins back to the ``dispatch_requests``
    row the credential's own ``command_id`` names, and checks that ITS
    ``selected_peer_instance_id`` (the peer PeerHub's own routing actually
    dispatched -- never a value the caller supplies) equals
    ``claimed_actor_id``. This is what actually stops one dispatched
    process from casting a vote as a *different* peer than the one it was
    admitted and routed as: the credential can only ever assert the
    identity PeerHub itself already recorded at admission time.

    Same fail-closed semantics as ``verify_credential``: wrong workspace,
    superseded epoch, expired, revoked, or an actor mismatch all fail
    identically -- no partial credit, no distinguishing error detail that
    would help a caller iterate toward a working forgery.
    """

    row = connection.execute(
        f"""
        SELECT 1 FROM dispatch_context_credentials AS credential
        JOIN {TABLE_DISPATCH_REQUESTS} AS request
          ON request.command_id = credential.command_id
        WHERE credential.credential_id = ?
          AND credential.workspace_home_id = ?
          AND credential.activation_epoch = ?
          AND credential.revoked_at IS NULL
          AND credential.expires_at > ?
          AND request.selected_peer_instance_id = ?
        """,
        (credential_id, workspace_home_id, activation_epoch, now, claimed_actor_id),
    ).fetchone()
    return row is not None


def resolve_actor_for_credential(
    connection: sqlite3.Connection,
    *,
    credential_id: str,
    workspace_home_id: str,
    activation_epoch: int,
    now: int,
) -> str | None:
    """Return the peer identity a valid credential is bound to, or ``None``
    if it does not currently authorize anyone (invalid, wrong workspace/
    epoch, expired, or revoked).

    This is the read-side counterpart to ``verify_credential_for_actor``:
    where that function checks a *caller-claimed* actor against the
    credential's bound ``dispatch_requests.selected_peer_instance_id``,
    this one has no claim to check yet and simply returns that bound
    identity -- used to let a caller omit an explicit actor/voter flag when
    it already holds a valid credential, instead of retyping an identity
    PeerHub already recorded at admission time. A caller that also supplies
    an explicit actor must still route through ``verify_credential_for_actor``
    (or ``ConsensusService.cast_vote``'s existing check) so an explicit
    contradiction is rejected, not silently overridden by this function."""

    row = connection.execute(
        f"""
        SELECT request.selected_peer_instance_id
        FROM dispatch_context_credentials AS credential
        JOIN {TABLE_DISPATCH_REQUESTS} AS request
          ON request.command_id = credential.command_id
        WHERE credential.credential_id = ?
          AND credential.workspace_home_id = ?
          AND credential.activation_epoch = ?
          AND credential.revoked_at IS NULL
          AND credential.expires_at > ?
        """,
        (credential_id, workspace_home_id, activation_epoch, now),
    ).fetchone()
    return row[0] if row is not None else None


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
