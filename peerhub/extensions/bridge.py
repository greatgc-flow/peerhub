"""Session Bridge (Wave 3): session mapping, execution certainty ledger, fenced delivery cycle, reconciliation.

Extension-owned tables (Core never imports this module). Builds on `bridge_claims.ClaimStore` (TD-04/19/25).
Certainty (TD-11/TD-26; D-OWN-A2/A4): NO downgrade ever. A separate invocation marker (bridge_invocations) is written before the invoke;
an unresolved marker blocks replay and recovery promotes it to MAY_HAVE_STARTED; a proven pre-spawn failure / control halt resolves it.
NOT_STARTED -> {NOT_STARTED, MAY_HAVE_STARTED, STARTED}; STARTED -> {STARTED, TERMINAL};
MAY_HAVE_STARTED and TERMINAL never change. A `control.reconcile` RETRY authorizes a NEW attempt row; the old row is untouched.
Wave 4 (control / context continuity): control Records (pause/resume/cancel/redirect, TD-05) are durable BEFORE any runtime effect;
the bridge accepts the intent in `bridge_controls` (committed) and only then calls the best-effort effect (interrupt/terminate/steer).
Intent gates delivery (pause) or cancels earlier unread Records (cancel) before any session/runtime side effect; effects are
idempotent per (control Record, peer) with a TD-19 lease; a bounded catch-up projection (TD-12) feeds fresh session generations.
Ledger evidence is append-only (DB triggers); a TERMINAL delivery's result is immutable.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Protocol

from peerhub.extensions.bridge_claims import ClaimScopeError, ClaimStore, ClaimToken, StaleClaimError
from peerhub.extensions.catchup import CatchUpBudget, ProjectedRecords, build_catch_up, project_catch_up
from peerhub.m1.store import CasMismatchError, CoreStore, IdempotencyConflictError

NOT_STARTED, MAY_HAVE_STARTED, STARTED, TERMINAL = "NOT_STARTED", "MAY_HAVE_STARTED", "STARTED", "TERMINAL"
_ALLOWED = {
    NOT_STARTED: {NOT_STARTED, MAY_HAVE_STARTED, STARTED},
    MAY_HAVE_STARTED: {MAY_HAVE_STARTED, STARTED, TERMINAL},  # forward edges on late evidence only (D-W3-3)
    STARTED: {STARTED, TERMINAL},
    TERMINAL: {TERMINAL},
}


class IllegalCertaintyTransition(ValueError):
    """Forbidden execution-certainty transition (TD-11); nothing was written."""


class TerminalConflictError(RuntimeError):
    """A different terminal result arrived for a delivery whose terminal truth is already committed (BRG-019)."""


class InvalidTerminalResultError(ValueError):
    """Terminal result is not a strict-JSON dict with a `response` key; rejected before anything is committed."""


class OffsetAckConflictError(RuntimeError):
    """Offset CAS conflicts exhausted the retry budget: the delivery is NOT acked (terminal truth stays, recovery re-acks)."""


class InvocationMarkerError(ValueError):
    """Invocation marker/resolution violates the marker protocol (no unresolved marker, or certainty is not NOT_STARTED); nothing was written."""


class NoDeliveryError(LookupError):
    """No delivery exists for the claim scope."""


class ReconcileRejectedError(ValueError):
    """The reconciliation Record cannot authorize a retry (wrong kind/decision/target/certainty)."""


class ControlRejectedError(ValueError):
    """A control request is not authentic/addressed (not durable, not a control Record, wrong peer/author); nothing was written."""


class BoundaryConflictError(RuntimeError):
    """The deterministic context.boundary key is occupied by a Record that is not this generation's boundary."""


class RuntimeTargetError(RuntimeError):
    """Ambiguous runtime failure (process/session may have started)."""


class PrespawnError(RuntimeTargetError):
    """Failure proven to be before any process/session start (resolves the invocation marker; certainty stays NOT_STARTED)."""


class ContextLostError(PrespawnError):
    """Adapter reports context/session loss (compaction, context limit) before the run began: safe NOT_STARTED retry on a fresh generation."""


class SessionError(RuntimeError):
    """Session create/resume failure."""


class RuntimeTarget(Protocol):
    runtime_kind: str
    resumable: bool

    def fingerprint(self) -> str: ...
    def binding(self) -> str: ...  # model/profile binding; a mapping is only resumable under the same binding
    def create_session(self) -> str: ...
    def resume_session(self, external_session_id: str) -> str: ...  # "ok" | "missing" | "unsupported" | "rejected"
    def deliver(self, external_session_id: str, record: Any, catch_up: list) -> Iterable[tuple]: ...


@dataclass
class CycleResult:
    status: str  # idle|delivered|recovered_terminal|failed_not_started|uncertain|blocked_uncertain|session_unavailable|fenced|paused|cancelled
    record_id: str | None = None
    delivery_id: str | None = None
    certainty: str | None = None
    response_record_id: str | None = None
    session_generation: int | None = None
    detail: dict = field(default_factory=dict)


CONTROL_EFFECT = {"control.pause": "interrupt", "control.cancel": "terminate", "control.redirect": "steer"}
_NO_EFFECT_OUTCOME = {"control.resume": "not_required", "context.boundary": "noted"}


def is_control_kind(kind: str) -> bool:
    """Records the bridge treats as control intent (never as prompts). control.reconcile is consumed via reconcile_uncertain."""
    return (kind.startswith("control.") and kind != "control.reconcile") or kind == "context.boundary"


@dataclass
class ControlResult:
    status: str  # applied | replayed | in_progress | superseded
    record_id: str
    kind: str
    peer_id: str
    stream_id: str
    effect: str
    runtime_outcome: str | None = None  # done | unsupported | failed | nothing_running | not_required | noted | unsupported_kind
    paused: bool = False
    detail: dict = field(default_factory=dict)


_DDL = [
    """CREATE TABLE IF NOT EXISTS bridge_sessions (
        stream_id TEXT NOT NULL, peer_id TEXT NOT NULL, runtime_kind TEXT NOT NULL, external_session_id TEXT NOT NULL,
        session_generation INTEGER NOT NULL, adapter_fingerprint TEXT NOT NULL, binding TEXT NOT NULL,
        resumable INTEGER NOT NULL, state TEXT NOT NULL, last_seen REAL NOT NULL,
        workspace_generation TEXT NOT NULL DEFAULT '', PRIMARY KEY (stream_id, peer_id))""",
    """CREATE TABLE IF NOT EXISTS bridge_session_events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, stream_id TEXT NOT NULL, peer_id TEXT NOT NULL, event TEXT NOT NULL,
        from_state TEXT NOT NULL, to_state TEXT NOT NULL, generation INTEGER NOT NULL, detail TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS bridge_deliveries (
        delivery_id TEXT PRIMARY KEY, seq INTEGER NOT NULL, stream_id TEXT NOT NULL, peer_id TEXT NOT NULL,
        record_id TEXT NOT NULL, record_position INTEGER NOT NULL, attempt INTEGER NOT NULL, certainty TEXT NOT NULL,
        claim_generation INTEGER NOT NULL, external_session_id TEXT, session_generation INTEGER, execution_id TEXT,
        result_json TEXT, result_digest TEXT, terminal_at TEXT, response_record_id TEXT, acked INTEGER NOT NULL DEFAULT 0,
        reconcile_record_id TEXT, UNIQUE (stream_id, peer_id, record_id, attempt))""",
    """CREATE TABLE IF NOT EXISTS bridge_evidence (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, delivery_id TEXT, stream_id TEXT NOT NULL, peer_id TEXT NOT NULL,
        kind TEXT NOT NULL, certainty TEXT, detail TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS bridge_reconciliations (
        delivery_id TEXT PRIMARY KEY, reconcile_record_id TEXT NOT NULL, decision TEXT NOT NULL, consumed_attempt INTEGER)""",
    """CREATE TABLE IF NOT EXISTS bridge_controls (
        record_id TEXT NOT NULL, stream_id TEXT NOT NULL, peer_id TEXT NOT NULL, kind TEXT NOT NULL, position INTEGER NOT NULL,
        effect TEXT NOT NULL, outcome TEXT, error TEXT, delivery_id TEXT, lease_id TEXT, lease_expires REAL,
        attempts INTEGER NOT NULL DEFAULT 0, accepted_seq INTEGER NOT NULL DEFAULT 0, target_delivery_id TEXT,
        target_session_id TEXT, target_claim_generation INTEGER, PRIMARY KEY (record_id, peer_id))""",
    """CREATE TRIGGER IF NOT EXISTS bridge_controls_target_write_once
        BEFORE UPDATE OF target_delivery_id, target_session_id, target_claim_generation ON bridge_controls
        WHEN OLD.target_delivery_id IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'control target is persisted once and never retargeted'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_controls_no_delete BEFORE DELETE ON bridge_controls
        BEGIN SELECT RAISE(ABORT, 'bridge_controls rows are never deleted'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_controls_identity_immutable
        BEFORE UPDATE OF record_id, stream_id, peer_id, kind, position, effect, accepted_seq ON bridge_controls
        BEGIN SELECT RAISE(ABORT, 'control intent identity is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_controls_outcome_write_once
        BEFORE UPDATE OF outcome, error, delivery_id ON bridge_controls WHEN OLD.outcome IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'control outcome is write-once'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_evidence_no_update BEFORE UPDATE ON bridge_evidence
        BEGIN SELECT RAISE(ABORT, 'bridge_evidence is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_evidence_no_delete BEFORE DELETE ON bridge_evidence
        BEGIN SELECT RAISE(ABORT, 'bridge_evidence is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_terminal_immutable
        BEFORE UPDATE OF certainty, result_json, result_digest, terminal_at ON bridge_deliveries WHEN OLD.certainty = 'TERMINAL'
        BEGIN SELECT RAISE(ABORT, 'terminal delivery is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_session_events_no_update BEFORE UPDATE ON bridge_session_events
        BEGIN SELECT RAISE(ABORT, 'bridge_session_events is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_session_events_no_delete BEFORE DELETE ON bridge_session_events
        BEGIN SELECT RAISE(ABORT, 'bridge_session_events is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_reconciliations_no_delete BEFORE DELETE ON bridge_reconciliations
        BEGIN SELECT RAISE(ABORT, 'bridge_reconciliations rows are never deleted'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_reconciliations_immutable
        BEFORE UPDATE OF delivery_id, decision ON bridge_reconciliations
        BEGIN SELECT RAISE(ABORT, 'authorization is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_reconciliations_valid_insert BEFORE INSERT ON bridge_reconciliations
        WHEN NOT EXISTS (SELECT 1 FROM records n JOIN bridge_deliveries d ON d.delivery_id = NEW.delivery_id
            WHERE n.record_id = NEW.reconcile_record_id AND n.kind = 'control.reconcile' AND n.stream_id = d.stream_id AND n.author_peer_id != d.peer_id AND json_valid(n.body_json)
            AND json_extract(n.body_json, '$.decision') = 'RETRY' AND json_extract(n.body_json, '$.delivery_id') = NEW.delivery_id)
        BEGIN SELECT RAISE(ABORT, 'authorization must be a durable control.reconcile RETRY for this delivery, not authored by the bridged peer'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_reconciliations_consumed_write_once
        BEFORE UPDATE OF consumed_attempt ON bridge_reconciliations WHEN OLD.consumed_attempt IS NOT NULL
        BEGIN SELECT RAISE(ABORT, 'consumed_attempt is write-once (NULL -> attempt only)'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_reconciliations_consumed_immutable
        BEFORE UPDATE OF reconcile_record_id ON bridge_reconciliations WHEN OLD.consumed_attempt IS NOT NULL OR NOT EXISTS (
            SELECT 1 FROM records n JOIN records o ON o.record_id = OLD.reconcile_record_id
            JOIN bridge_deliveries d ON d.delivery_id = NEW.delivery_id
            WHERE n.record_id = NEW.reconcile_record_id AND n.position > o.position AND n.stream_id = o.stream_id AND n.kind = 'control.reconcile' AND n.stream_id = d.stream_id AND n.author_peer_id != d.peer_id AND json_valid(n.body_json)
            AND json_extract(n.body_json, '$.decision') = 'RETRY' AND json_extract(n.body_json, '$.delivery_id') = NEW.delivery_id)
        BEGIN SELECT RAISE(ABORT, 'authorization may only move to a NEWER valid control.reconcile RETRY for this delivery, never once consumed'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_evidence_no_replace BEFORE INSERT ON bridge_evidence
        WHEN EXISTS (SELECT 1 FROM bridge_evidence WHERE seq = NEW.seq)
        BEGIN SELECT RAISE(ABORT, 'bridge_evidence is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_session_events_no_replace BEFORE INSERT ON bridge_session_events
        WHEN EXISTS (SELECT 1 FROM bridge_session_events WHERE seq = NEW.seq)
        BEGIN SELECT RAISE(ABORT, 'bridge_session_events is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_deliveries_no_replace BEFORE INSERT ON bridge_deliveries
        WHEN EXISTS (SELECT 1 FROM bridge_deliveries WHERE delivery_id = NEW.delivery_id OR (stream_id = NEW.stream_id AND peer_id = NEW.peer_id AND record_id = NEW.record_id AND attempt = NEW.attempt))
        BEGIN SELECT RAISE(ABORT, 'bridge_deliveries rows are never replaced'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_reconciliations_no_replace BEFORE INSERT ON bridge_reconciliations
        WHEN EXISTS (SELECT 1 FROM bridge_reconciliations WHERE delivery_id = NEW.delivery_id)
        BEGIN SELECT RAISE(ABORT, 'bridge_reconciliations rows are never replaced'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_controls_no_replace BEFORE INSERT ON bridge_controls
        WHEN EXISTS (SELECT 1 FROM bridge_controls WHERE record_id = NEW.record_id AND peer_id = NEW.peer_id)
        BEGIN SELECT RAISE(ABORT, 'bridge_controls rows are never replaced'); END""",
    """CREATE TABLE IF NOT EXISTS bridge_invocations (
        delivery_id TEXT NOT NULL, invocation_no INTEGER NOT NULL, stream_id TEXT NOT NULL, peer_id TEXT NOT NULL,
        claim_generation INTEGER NOT NULL, external_session_id TEXT, session_generation INTEGER,
        PRIMARY KEY (delivery_id, invocation_no))""",
    """CREATE TABLE IF NOT EXISTS bridge_invocation_resolutions (
        delivery_id TEXT NOT NULL, invocation_no INTEGER NOT NULL, reason TEXT NOT NULL, claim_generation INTEGER NOT NULL,
        PRIMARY KEY (delivery_id, invocation_no))""",
    """CREATE TRIGGER IF NOT EXISTS bridge_invocations_no_update BEFORE UPDATE ON bridge_invocations
        BEGIN SELECT RAISE(ABORT, 'bridge_invocations is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_invocations_no_delete BEFORE DELETE ON bridge_invocations
        BEGIN SELECT RAISE(ABORT, 'bridge_invocations is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_invocations_no_replace BEFORE INSERT ON bridge_invocations
        WHEN EXISTS (SELECT 1 FROM bridge_invocations WHERE delivery_id = NEW.delivery_id AND invocation_no = NEW.invocation_no)
        BEGIN SELECT RAISE(ABORT, 'bridge_invocations rows are never replaced'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_invocation_resolutions_no_update BEFORE UPDATE ON bridge_invocation_resolutions
        BEGIN SELECT RAISE(ABORT, 'bridge_invocation_resolutions is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_invocation_resolutions_no_delete BEFORE DELETE ON bridge_invocation_resolutions
        BEGIN SELECT RAISE(ABORT, 'bridge_invocation_resolutions is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_invocation_resolutions_no_replace BEFORE INSERT ON bridge_invocation_resolutions
        WHEN EXISTS (SELECT 1 FROM bridge_invocation_resolutions WHERE delivery_id = NEW.delivery_id AND invocation_no = NEW.invocation_no)
        BEGIN SELECT RAISE(ABORT, 'bridge_invocation_resolutions rows are never replaced'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_invocation_resolutions_needs_marker BEFORE INSERT ON bridge_invocation_resolutions
        WHEN NOT EXISTS (SELECT 1 FROM bridge_invocations WHERE delivery_id = NEW.delivery_id AND invocation_no = NEW.invocation_no)
        BEGIN SELECT RAISE(ABORT, 'a resolution needs its invocation marker'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_deliveries_no_rowid_replace BEFORE INSERT ON bridge_deliveries
        WHEN NEW.rowid IS NOT NULL AND EXISTS (SELECT 1 FROM bridge_deliveries WHERE rowid = NEW.rowid)
        BEGIN SELECT RAISE(ABORT, 'bridge_deliveries rows are never replaced'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_reconciliations_no_rowid_replace BEFORE INSERT ON bridge_reconciliations
        WHEN NEW.rowid IS NOT NULL AND EXISTS (SELECT 1 FROM bridge_reconciliations WHERE rowid = NEW.rowid)
        BEGIN SELECT RAISE(ABORT, 'bridge_reconciliations rows are never replaced'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_controls_no_rowid_replace BEFORE INSERT ON bridge_controls
        WHEN NEW.rowid IS NOT NULL AND EXISTS (SELECT 1 FROM bridge_controls WHERE rowid = NEW.rowid)
        BEGIN SELECT RAISE(ABORT, 'bridge_controls rows are never replaced'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_invocations_no_rowid_replace BEFORE INSERT ON bridge_invocations
        WHEN NEW.rowid IS NOT NULL AND EXISTS (SELECT 1 FROM bridge_invocations WHERE rowid = NEW.rowid)
        BEGIN SELECT RAISE(ABORT, 'bridge_invocations rows are never replaced'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_invocation_resolutions_no_rowid_replace BEFORE INSERT ON bridge_invocation_resolutions
        WHEN NEW.rowid IS NOT NULL AND EXISTS (SELECT 1 FROM bridge_invocation_resolutions WHERE rowid = NEW.rowid)
        BEGIN SELECT RAISE(ABORT, 'bridge_invocation_resolutions rows are never replaced'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_deliveries_no_delete BEFORE DELETE ON bridge_deliveries
        BEGIN SELECT RAISE(ABORT, 'bridge_deliveries rows are never deleted'); END""",
]


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Bridge:
    def __init__(self, store: CoreStore, claims: ClaimStore, *, owner_id: str = "bridge-1", lease_sec: float = 30.0,
                 max_session_attempts: int = 3, catch_up_limit: int = 100, ack_retries: int = 8,
                 catch_up_budget: CatchUpBudget | None = None, fault_hook: Callable[[str], None] | None = None) -> None:
        self.store, self.claims = store, claims
        self.owner_id, self.lease_sec = owner_id, lease_sec
        self.max_session_attempts, self.catch_up_limit, self.ack_retries = max_session_attempts, catch_up_limit, ack_retries
        self.catch_up_budget = catch_up_budget if catch_up_budget is not None else CatchUpBudget(max_records=catch_up_limit)
        self._hook = fault_hook
        with claims._tx() as conn:
            owned = [m.group(1) for stmt in _DDL if (m := re.match(r"\s*CREATE TABLE IF NOT EXISTS (\w+)", stmt))]
            marks = ",".join("?" * len(owned))
            # reopen installs EXACTLY the current trigger set: every trigger on a bridge-owned table is dropped first (including legacy
            # ones that no longer exist in the source), in the init transaction (no gap)
            for (name,) in conn.execute(f"SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name IN ({marks})", owned).fetchall():
                conn.execute(f'DROP TRIGGER IF EXISTS "{name}"')
            for stmt in _DDL:
                conn.execute(stmt)
            if "workspace_generation" not in {r[1] for r in conn.execute("PRAGMA table_info(bridge_sessions)")}:
                # stores created before FLT-008: unknown lineage ('' never equals a real generation) => never resumed, always fresh
                conn.execute("ALTER TABLE bridge_sessions ADD COLUMN workspace_generation TEXT NOT NULL DEFAULT ''")

    def _fire(self, point: str) -> None:
        if self._hook is not None:
            self._hook(point)

    # ------------------------------------------------------------------ ledger primitives
    @staticmethod
    def _evidence(conn: sqlite3.Connection, stream_id: str, peer_id: str, kind: str, delivery_id: str | None = None,
                  certainty: str | None = None, **detail: Any) -> None:
        conn.execute("INSERT INTO bridge_evidence (delivery_id, stream_id, peer_id, kind, certainty, detail) VALUES (?,?,?,?,?,?)",
                     (delivery_id, stream_id, peer_id, kind, certainty, json.dumps(detail, sort_keys=True, default=str)))

    @staticmethod
    def _check_scope(row, token: ClaimToken) -> None:
        if (row["stream_id"], row["peer_id"]) != (token.stream_id, token.peer_id):
            raise ClaimScopeError(f"claim for ({token.peer_id}, {token.stream_id}) cannot mutate delivery {row['delivery_id']}")

    def _transition_in(self, conn: sqlite3.Connection, delivery_id: str, to: str, kind: str, *,
                       token: ClaimToken | None = None, **detail: Any) -> None:
        row = conn.execute("SELECT * FROM bridge_deliveries WHERE delivery_id=?", (delivery_id,)).fetchone()
        if row is None:
            raise NoDeliveryError(delivery_id)
        if token is not None:
            self._check_scope(row, token)  # BEFORE any mutation
        if to not in _ALLOWED.get(row["certainty"], set()):
            raise IllegalCertaintyTransition(f"{row['certainty']} -> {to} is forbidden (TD-11/TD-26)")
        if to != row["certainty"]:
            conn.execute("UPDATE bridge_deliveries SET certainty=? WHERE delivery_id=?", (to, delivery_id))
        self._evidence(conn, row["stream_id"], row["peer_id"], kind, delivery_id, to, **detail)

    def transition(self, token: ClaimToken, delivery_id: str, to: str, kind: str = "transition", **detail: Any) -> None:
        """Fenced certainty transition; forbidden transitions raise IllegalCertaintyTransition with no write."""
        with self.claims.fenced(token) as conn:
            self._transition_in(conn, delivery_id, to, kind, token=token, **detail)

    def _mark_invocation(self, token: ClaimToken, delivery_id: str, **detail: Any) -> int:
        """D-OWN-A4: durable invocation marker BEFORE the runtime can act. Certainty stays NOT_STARTED while it is pending;
        an unresolved marker blocks replay and recovery promotes it to MAY_HAVE_STARTED."""
        with self.claims.fenced(token) as conn:
            row = conn.execute("SELECT * FROM bridge_deliveries WHERE delivery_id=?", (delivery_id,)).fetchone()
            if row is None:
                raise NoDeliveryError(delivery_id)
            self._check_scope(row, token)
            if row["certainty"] != NOT_STARTED or self._open_invocation(conn, delivery_id) is not None:
                raise InvocationMarkerError(f"cannot mark an invocation for a {row['certainty']} delivery or one with an unresolved marker")
            n = conn.execute("SELECT COALESCE(MAX(invocation_no),0)+1 FROM bridge_invocations WHERE delivery_id=?", (delivery_id,)).fetchone()[0]
            conn.execute("INSERT INTO bridge_invocations (delivery_id, invocation_no, stream_id, peer_id, claim_generation, "
                         "external_session_id, session_generation) VALUES (?,?,?,?,?,?,?)",
                         (delivery_id, n, token.stream_id, token.peer_id, token.generation, detail.get("external_session_id"),
                          detail.get("session_generation")))
            self._evidence(conn, token.stream_id, token.peer_id, "about_to_invoke", delivery_id, NOT_STARTED, invocation_no=n, **detail)
            return n

    @staticmethod
    def _open_invocation(conn: sqlite3.Connection, delivery_id: str) -> int | None:
        r = conn.execute("SELECT i.invocation_no FROM bridge_invocations i WHERE i.delivery_id=? AND NOT EXISTS ("
                         "SELECT 1 FROM bridge_invocation_resolutions r WHERE r.delivery_id=i.delivery_id AND r.invocation_no=i.invocation_no) "
                         "ORDER BY i.invocation_no DESC LIMIT 1", (delivery_id,)).fetchone()
        return None if r is None else r[0]

    def _resolve_invocation(self, token: ClaimToken, delivery_id: str, reason: str, error: str, kind: str) -> None:
        """A fenced, PROVEN pre-spawn failure or a control halt before the invoke resolves the marker. Certainty is never touched."""
        with self.claims.fenced(token) as conn:
            row = conn.execute("SELECT * FROM bridge_deliveries WHERE delivery_id=?", (delivery_id,)).fetchone()
            if row is None:
                raise NoDeliveryError(delivery_id)
            self._check_scope(row, token)
            n = self._open_invocation(conn, delivery_id)
            if row["certainty"] != NOT_STARTED or n is None or row["execution_id"] is not None:
                raise InvocationMarkerError(f"no unresolved invocation marker to resolve on a {row['certainty']} delivery")
            conn.execute("INSERT INTO bridge_invocation_resolutions (delivery_id, invocation_no, reason, claim_generation) VALUES (?,?,?,?)",
                         (delivery_id, n, reason, token.generation))
            self._evidence(conn, row["stream_id"], row["peer_id"], kind, delivery_id, NOT_STARTED, error=error)
        self._fire("bridge.after_invocation_resolved")

    def _promote_unresolved(self, token: ClaimToken) -> None:
        """Recovery: an unresolved marker means the runtime may have been invoked -> durable MAY_HAVE_STARTED with evidence."""
        q = ("SELECT d.delivery_id, i.invocation_no FROM bridge_deliveries d JOIN bridge_invocations i ON i.delivery_id=d.delivery_id "
             "WHERE d.stream_id=? AND d.peer_id=? AND d.certainty=? AND NOT EXISTS (SELECT 1 FROM bridge_invocation_resolutions r "
             "WHERE r.delivery_id=i.delivery_id AND r.invocation_no=i.invocation_no)")
        args = (token.stream_id, token.peer_id, NOT_STARTED)
        with self.claims._tx() as conn:
            if not conn.execute(q, args).fetchall():
                return  # nothing dangling: a read-only (possibly stale) cycle writes nothing
        with self.claims.fenced(token) as conn:
            for did, n in conn.execute(q, args).fetchall():
                self._transition_in(conn, did, MAY_HAVE_STARTED, "invocation_unresolved_recovered", token=token, invocation_no=n)
                conn.execute("INSERT INTO bridge_invocation_resolutions (delivery_id, invocation_no, reason, claim_generation) VALUES (?,?,?,?)",
                             (did, n, "promoted_may_have_started", token.generation))

    def begin_attempt(self, token: ClaimToken, record, *, reconcile_of: str | None = None) -> str:
        """Create (or, for NOT_STARTED retry, reuse) the attempt row for `record`; returns delivery_id."""
        with self.claims.fenced(token) as conn:
            s, p = token.stream_id, token.peer_id
            last = conn.execute("SELECT * FROM bridge_deliveries WHERE stream_id=? AND peer_id=? AND record_id=? "
                                "ORDER BY attempt DESC LIMIT 1", (s, p, record.record_id)).fetchone()
            if last is not None and last["certainty"] == NOT_STARTED and reconcile_of is None:
                return last["delivery_id"]
            if last is not None and reconcile_of is None:
                raise IllegalCertaintyTransition(f"record already has a {last['certainty']} attempt; replay needs reconcile.retry")
            attempt = 1 if last is None else last["attempt"] + 1
            did = f"{s}:{p}:{record.record_id}:{attempt}"
            seq = conn.execute("SELECT COALESCE(MAX(seq),0)+1 FROM bridge_deliveries").fetchone()[0]
            rec_rec = None
            if reconcile_of is not None:
                auth = conn.execute("SELECT * FROM bridge_reconciliations WHERE delivery_id=?", (reconcile_of,)).fetchone()
                if auth is None or auth["consumed_attempt"] is not None or last is None or last["delivery_id"] != reconcile_of:
                    raise ReconcileRejectedError("no unconsumed reconcile.retry authorization for this delivery")
                conn.execute("UPDATE bridge_reconciliations SET consumed_attempt=? WHERE delivery_id=?", (attempt, reconcile_of))
                rec_rec = auth["reconcile_record_id"]
            conn.execute(
                "INSERT INTO bridge_deliveries (delivery_id, seq, stream_id, peer_id, record_id, record_position, attempt, certainty, "
                "claim_generation, reconcile_record_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (did, seq, s, p, record.record_id, record.position, attempt, NOT_STARTED, token.generation, rec_rec))
            self._evidence(conn, s, p, "attempt_created", did, NOT_STARTED, attempt=attempt, reconcile_record_id=rec_rec,
                           claim_generation=token.generation)
            return did

    def _note(self, token: ClaimToken, delivery_id: str | None, kind: str, certainty: str | None = None, **detail: Any) -> None:
        with self.claims.fenced(token) as conn:
            self._evidence(conn, token.stream_id, token.peer_id, kind, delivery_id, certainty, **detail)

    # ------------------------------------------------------------------ harness-facing reads
    def current_mapping(self, peer_id: str, stream_id: str) -> dict | None:
        with self.claims._tx() as conn:
            r = conn.execute("SELECT * FROM bridge_sessions WHERE stream_id=? AND peer_id=?", (stream_id, peer_id)).fetchone()
        if r is None:
            return None
        d = dict(r)
        d["resumable"] = bool(d["resumable"])
        return d

    def session_events(self, peer_id: str, stream_id: str) -> list[dict]:
        with self.claims._tx() as conn:
            rows = conn.execute("SELECT * FROM bridge_session_events WHERE stream_id=? AND peer_id=? ORDER BY seq",
                                (stream_id, peer_id)).fetchall()
        return [dict(r) for r in rows]

    def execution_evidence(self, record_id: str, peer_id: str) -> dict:
        with self.claims._tx() as conn:
            dels = [dict(r) for r in conn.execute("SELECT * FROM bridge_deliveries WHERE record_id=? AND peer_id=? ORDER BY attempt",
                                                  (record_id, peer_id))]
            ids = [d["delivery_id"] for d in dels]
            ev = [dict(r) for r in conn.execute("SELECT * FROM bridge_evidence WHERE peer_id=? ORDER BY seq", (peer_id,))
                  if r["delivery_id"] in ids]
        for e in ev:
            e["detail"] = json.loads(e["detail"])
        return {"deliveries": dels, "events": ev}

    # ------------------------------------------------------------------ session resolution
    def _session_event(self, conn, token, event, frm, to, gen, **detail) -> None:
        conn.execute("INSERT INTO bridge_session_events (stream_id, peer_id, event, from_state, to_state, generation, detail) "
                     "VALUES (?,?,?,?,?,?,?)", (token.stream_id, token.peer_id, event, frm, to, gen, json.dumps(detail, sort_keys=True)))

    def _resolve_session(self, token: ClaimToken, runtime: RuntimeTarget, record, did: str):
        m = self.current_mapping(token.peer_id, token.stream_id)
        fp = runtime.fingerprint()
        bnd = runtime.binding()
        now = self.claims._clock()
        reason = None
        if (m is not None and m["adapter_fingerprint"] == fp and m["binding"] == bnd and m["resumable"] and m["state"] in ("ACTIVE", "FRESH")
                and m["workspace_generation"] == token.workspace_generation):  # FLT-008: a session of another lineage is never resumed
            try:
                outcome = runtime.resume_session(m["external_session_id"])
            except SessionError:
                outcome = "rejected"
            if outcome == "ok":
                with self.claims.fenced(token) as conn:
                    conn.execute("UPDATE bridge_sessions SET last_seen=?, state='ACTIVE' WHERE stream_id=? AND peer_id=?",
                                 (now, token.stream_id, token.peer_id))
                    self._session_event(conn, token, "resume_ok", m["state"], "ACTIVE", m["session_generation"])
                return m["external_session_id"], m["session_generation"]
            reason = outcome
            with self.claims.fenced(token) as conn:
                if outcome == "missing":
                    conn.execute("UPDATE bridge_sessions SET state='LOST' WHERE stream_id=? AND peer_id=?",
                                 (token.stream_id, token.peer_id))
                    self._session_event(conn, token, "session_lost", m["state"], "LOST", m["session_generation"])
                else:
                    self._session_event(conn, token, "resume_rejected", m["state"], "FRESH", m["session_generation"], outcome=outcome)
        elif m is not None:
            reason = ("workspace_generation_change" if m["workspace_generation"] != token.workspace_generation
                      else "fingerprint_change" if m["adapter_fingerprint"] != fp else "binding_change" if m["binding"] != bnd
                      else "not_resumable" if not m["resumable"] else self._lost_reason(token) if m["state"] == "LOST" else m["state"].lower())
            with self.claims.fenced(token) as conn:
                self._session_event(conn, token, reason, m["state"], "FRESH", m["session_generation"])
        if m is not None:
            self._fire("bridge.after_resume_failure")  # after a failed/unusable resume, before any creation attempt
        for i in range(self.max_session_attempts):
            gated = self._gate(token, runtime, record, did)  # D-W4-2b: a durable pause/cancel stops EVERY creation attempt
            if gated is not None:
                return gated
            try:
                ext = runtime.create_session()
            except SessionError as e:
                with self.claims.fenced(token) as conn:
                    self._session_event(conn, token, "create_failed", (m or {}).get("state", "NONE"), (m or {}).get("state", "NONE"),
                                        (m or {}).get("session_generation", 0), attempt=i + 1, error=str(e))
                self._fire("bridge.after_create_failure")
                continue
            gen = 1 if m is None else m["session_generation"] + 1
            with self.claims.fenced(token) as conn:
                conn.execute("INSERT OR REPLACE INTO bridge_sessions (stream_id, peer_id, runtime_kind, external_session_id, session_generation, "
                             "adapter_fingerprint, binding, resumable, state, last_seen, workspace_generation) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (token.stream_id, token.peer_id, runtime.runtime_kind, ext, gen, fp, bnd,
                              1 if runtime.resumable else 0, "ACTIVE" if m is None else "FRESH", now, token.workspace_generation))
                self._session_event(conn, token, "created" if m is None else "fresh_generation",
                                    "NONE" if m is None else ("LOST" if reason == "missing" or m["state"] == "LOST" else "ACTIVE"),
                                    "ACTIVE" if m is None else "FRESH", gen, reason=reason, at=_iso(now))
            return ext, gen
        return None

    def _lost_reason(self, token: ClaimToken) -> str:
        for e in reversed(self.session_events(token.peer_id, token.stream_id)):
            if e["event"] == "session_lost":
                return json.loads(e["detail"]).get("reason", "missing")
        return "missing"

    def _mark_lost(self, token: ClaimToken, reason: str) -> None:
        """Adapter reported context/session loss: the mapping is LOST; the next resolution creates a fresh generation."""
        m = self.current_mapping(token.peer_id, token.stream_id)
        if m is None or m["state"] == "LOST":
            return
        with self.claims.fenced(token) as conn:
            conn.execute("UPDATE bridge_sessions SET state='LOST' WHERE stream_id=? AND peer_id=?", (token.stream_id, token.peer_id))
            self._session_event(conn, token, "session_lost", m["state"], "LOST", m["session_generation"], reason=reason)

    def _needs_catch_up(self, peer: str, stream: str, gen: int) -> bool:
        """A session generation needs catch-up until some delivery actually reached it (NOT_STARTED rows never reached it)."""
        with self.claims._tx() as conn:
            seen = conn.execute("SELECT 1 FROM bridge_deliveries WHERE stream_id=? AND peer_id=? AND session_generation=? AND certainty != ? LIMIT 1",
                                (stream, peer, gen, NOT_STARTED)).fetchone()
        return seen is None

    def _unseen_redirects(self, peer: str, stream: str, rec) -> list:
        """control.redirect Records addressed to this peer that the resumed session has not seen and no steer delivered."""
        with self.claims._tx() as conn:
            last = conn.execute("SELECT COALESCE(MAX(record_position),0) FROM bridge_deliveries WHERE stream_id=? AND peer_id=? AND certainty != ?",
                                (stream, peer, NOT_STARTED)).fetchone()[0]
            done = {r[0] for r in conn.execute("SELECT record_id FROM bridge_controls WHERE stream_id=? AND peer_id=? AND outcome='done'",
                                               (stream, peer))}
        cands = self.store.read_records(stream, last, 2**31 - 1)
        return [r for r in cands if r.position < rec.position and r.kind == "control.redirect" and r.author_peer_id != peer
                and (not r.targets or peer in r.targets) and r.record_id not in done]

    @staticmethod
    def _redirect_pin(peer: str):
        return lambda r: r.kind == "control.redirect" and r.author_peer_id != peer and (not r.targets or peer in r.targets)

    def _fresh_info(self, peer: str, stream: str, gen: int) -> dict:
        for e in reversed(self.session_events(peer, stream)):
            if e["event"] == "fresh_generation" and e["generation"] == gen:
                return json.loads(e["detail"])
        return {}

    def _catch_up_for(self, token: ClaimToken, rec, gen: int) -> list:
        peer, stream = token.peer_id, token.stream_id
        if not self._needs_catch_up(peer, stream, gen):  # resumed session: only unseen redirects, under the same budget
            proj = project_catch_up(self._unseen_redirects(peer, stream, rec), self.catch_up_budget, before_position=rec.position)
            out = ProjectedRecords(proj.records)
            out.boundary = {**proj.boundary(), "session_generation": gen, "mode": "resumed_unseen_redirects"}
            return out
        after = 0  # D-W4-8b: the Offset governs DELIVERY only; a fresh generation is bootstrapped from the Stream history (TD-12 budget)
        proj = build_catch_up(self.store, stream, self.catch_up_budget, after_position=after, before_position=rec.position,
                              pin=self._redirect_pin(peer))  # a fresh session knows no redirect intent: pin ALL addressed ones
        out = ProjectedRecords(proj.records)
        out.boundary = {**proj.boundary(), "session_generation": gen, "mode": "fresh_generation"}
        if gen > 1:  # a fresh generation after loss/rejection/change: record the boundary durably (idempotent per generation)
            self._write_boundary(token, gen, proj)
        return out

    @staticmethod
    def _boundary_payload_ok(body: Any, peer: str, gen: int, reason: str | None, template: dict) -> bool:
        """D-W4-5b: the COMPLETE payload must be this generation's boundary: peer, generation, the recorded reason and a catch_up
        object with exactly the projection-metadata keys and sane types (window/counts may differ when a retry targets another Record)."""
        if not isinstance(body, dict) or set(body) != {"peer_id", "session_generation", "reason", "catch_up"}:
            return False
        if body["peer_id"] != peer or body["session_generation"] != gen or body["reason"] != reason:
            return False
        cu = body["catch_up"]
        if not isinstance(cu, dict) or set(cu) != set(template):
            return False
        ints = ("after_position", "candidates", "included_count", "omitted_count", "used_bytes", "used_tokens", "pinned_count")
        opt = ("before_position", "first_position", "last_position", "omitted_through_position")
        isint = lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0  # noqa: E731
        return (all(isint(cu[k]) for k in ints) and all(cu[k] is None or isint(cu[k]) for k in opt)
                and isinstance(cu["truncated"], bool) and cu["truncated"] == (cu["omitted_count"] > 0)
                and cu["candidates"] == cu["included_count"] + cu["omitted_count"]
                and isinstance(cu["budget"], dict) and cu["budget"] == template["budget"])

    def _boundary_matches_history(self, peer: str, stream: str, gen: int, cu: dict) -> bool:
        """The claimed metadata must equal the projection recomputed from the durable Stream for the claimed delivered position."""
        bp = cu["before_position"]
        with self.claims._tx() as conn:  # the Record the generation was FIRST used for (deterministic, survives retries for other Records)
            first = conn.execute("SELECT record_position FROM bridge_deliveries WHERE stream_id=? AND peer_id=? AND session_generation=? "
                                 "ORDER BY seq LIMIT 1", (stream, peer, gen)).fetchone()
        if bp is None or cu["after_position"] != 0 or first is None or first[0] != bp:
            return False
        at = self.store.read_records(stream, bp - 1, 1)
        if not at or at[0].position != bp:  # the delivered Record must really exist at that position
            return False
        return cu == build_catch_up(self.store, stream, self.catch_up_budget, after_position=0, before_position=bp,
                                    pin=self._redirect_pin(peer)).boundary()

    def _write_boundary(self, token: ClaimToken, gen: int, proj) -> None:
        peer, stream = token.peer_id, token.stream_id
        key = f"context-boundary:{peer}:{gen}"
        info = self._fresh_info(peer, stream, gen)
        try:
            self.store.append_record(
                guard=self.claims.guard(token), stream_id=stream, author_peer_id=peer, kind="context.boundary",
                body={"peer_id": peer, "session_generation": gen, "reason": info.get("reason"), "catch_up": proj.boundary()},
                idempotency_key=key, created_at=info.get("at") or _iso(self.claims._clock()))
        except IdempotencyConflictError:
            pass  # validated below: only THIS generation's boundary may occupy the key
        with self.claims._tx() as conn:
            row = conn.execute("SELECT kind, body_json FROM records WHERE stream_id=? AND author_peer_id=? AND idempotency_key=?",
                               (stream, peer, key)).fetchone()
        body = json.loads(row["body_json"]) if row is not None and row["kind"] == "context.boundary" else None
        if not (self._boundary_payload_ok(body, peer, gen, info.get("reason"), proj.boundary())
                and self._boundary_matches_history(peer, stream, gen, body["catch_up"])):
            raise BoundaryConflictError(f"idempotency key {key!r} is occupied by a Record that is not this generation's context.boundary")

    # ------------------------------------------------------------------ delivery cycle
    def delivery_cycle(self, peer_id: str, stream_id: str, runtime: RuntimeTarget) -> CycleResult:
        token = self.claims.acquire(peer_id, stream_id, self.owner_id, self.lease_sec)
        self._fire("claim.after_acquire")
        return self.run_cycle(token, runtime)

    def _inflight(self, token: ClaimToken) -> sqlite3.Row | None:
        with self.claims._tx() as conn:
            row = conn.execute("SELECT * FROM bridge_deliveries WHERE stream_id=? AND peer_id=? ORDER BY seq DESC LIMIT 1",
                               (token.stream_id, token.peer_id)).fetchone()
        return row if row is not None and row["acked"] == 0 else None  # sequential per scope: only the newest attempt can be in flight

    def _record_by_id(self, stream_id: str, position: int, record_id: str):
        for r in self.store.read_records(stream_id, position - 1, 1):
            if r.record_id == record_id:
                return r
        raise NoDeliveryError(f"record {record_id} not found")

    def run_cycle(self, token: ClaimToken, runtime: RuntimeTarget) -> CycleResult:
        try:
            return self._run_cycle(token, runtime)
        except OffsetAckConflictError as e:  # never report delivered/acked; terminal truth stays, next cycle re-acks
            infl = self._inflight(token)
            return CycleResult("ack_failed", infl["record_id"] if infl else None, infl["delivery_id"] if infl else None,
                               TERMINAL, detail={"error": str(e)})

    def _run_cycle(self, token: ClaimToken, runtime: RuntimeTarget) -> CycleResult:
        peer, stream = token.peer_id, token.stream_id
        self._promote_unresolved(token)  # D-OWN-A4: recovery never re-invokes; a dangling invocation marker is MAY_HAVE_STARTED
        self._handle_unread_controls(token, runtime)  # durable intent first: before any session/runtime side effect
        infl = self._inflight(token)
        reconcile_of = None
        if infl is not None:
            rec = self._record_by_id(stream, infl["record_position"], infl["record_id"])
            if infl["certainty"] == TERMINAL:  # BRG-015: recognize persisted terminal truth; never re-run the runtime
                rid = self._materialize(token, infl["delivery_id"])
                return CycleResult("recovered_terminal", rec.record_id, infl["delivery_id"], TERMINAL, rid)
            st = self.control_state(peer, stream)
            if st["paused"]:
                return CycleResult("paused", rec.record_id, infl["delivery_id"], infl["certainty"], detail={"pause_record_id": st["pause_record_id"]})
            if infl["certainty"] != NOT_STARTED:
                with self.claims._tx() as conn:
                    auth = conn.execute("SELECT * FROM bridge_reconciliations WHERE delivery_id=? AND consumed_attempt IS NULL",
                                        (infl["delivery_id"],)).fetchone()
                if auth is None or infl["certainty"] != MAY_HAVE_STARTED:
                    return CycleResult("blocked_uncertain", rec.record_id, infl["delivery_id"], infl["certainty"])
                blocker = self._retry_blocked_by_cancel(token, auth["reconcile_record_id"])
                if blocker is not None:  # D-W4-9: an older RETRY never overrides a newer cancel
                    return CycleResult("blocked_uncertain", rec.record_id, infl["delivery_id"], infl["certainty"],
                                       detail={"blocked_by_cancel": blocker})
                reconcile_of = infl["delivery_id"]
            else:
                blocker = self._retry_blocked_by_cancel(token, infl["reconcile_record_id"])
                if blocker is not None:
                    return CycleResult("blocked_uncertain", rec.record_id, infl["delivery_id"], NOT_STARTED, detail={"blocked_by_cancel": blocker})
        else:
            st = self.control_state(peer, stream)
            if st["paused"]:
                return CycleResult("paused", detail={"pause_record_id": st["pause_record_id"]})
            consumed: list[str] = []
            rec = self._next_record(token, runtime, consumed)
            if rec is None:
                return CycleResult("idle", detail={"consumed_controls": consumed} if consumed else {})
        did = self.begin_attempt(token, rec, reconcile_of=reconcile_of)
        self._fire("bridge.before_session_resolve")
        gated = self._gate(token, runtime, rec, did)
        if gated is not None:
            return gated
        sess = self._resolve_session(token, runtime, rec, did)
        if isinstance(sess, CycleResult):
            return sess
        if sess is None:
            self._note(token, did, "session_unavailable", NOT_STARTED, attempts=self.max_session_attempts)
            return CycleResult("session_unavailable", rec.record_id, did, NOT_STARTED,
                               detail={"attempts": self.max_session_attempts})
        ext, gen = sess
        self._fire("bridge.after_session_resolved")
        with self.claims.fenced(token) as conn:
            conn.execute("UPDATE bridge_deliveries SET external_session_id=?, session_generation=? WHERE delivery_id=?", (ext, gen, did))
        catch_up = self._catch_up_for(token, rec, gen)
        self._fire("bridge.before_marker")
        gated = self._gate(token, runtime, rec, did)
        if gated is not None:
            return gated
        return self._run_runtime(token, runtime, rec, did, ext, gen, catch_up)

    def _next_record(self, token: ClaimToken, runtime, consumed: list[str]):
        peer, stream = token.peer_id, token.stream_id
        while True:
            off = self.store.get_offset(peer, stream)
            recs = self.store.read_records(stream, off.read_through_position, 50)
            if not recs:
                return None
            for r in recs:
                if r.author_peer_id == peer or r.kind == "control.reconcile":  # own Records; reconcile via reconcile_uncertain
                    self._advance_to(token, r.position)
                elif is_control_kind(r.kind):  # control intent is never a prompt: apply (idempotent), then consume
                    if not r.targets or peer in r.targets:
                        self._apply_control({"record_id": r.record_id, "stream_id": stream, "position": r.position, "kind": r.kind},
                                            peer, runtime, token)
                        consumed.append(r.record_id)
                    self._advance_to(token, r.position)
                else:
                    return r

    def _run_runtime(self, token, runtime, rec, did, ext, gen, catch_up) -> CycleResult:
        started = False
        terminal: Any = None
        have_terminal = False
        # D-OWN-A4/TD-11: durable invocation marker BEFORE the runtime can possibly act; certainty stays NOT_STARTED while pending,
        # an unresolved marker after a crash is promoted to MAY_HAVE_STARTED by recovery (never re-invoked)
        self._mark_invocation(token, did, external_session_id=ext, session_generation=gen)
        self._fire("bridge.before_runtime_invoke")
        gated = self._gate(token, runtime, rec, did, after_marker=True)  # intent committed up to the very invoke still wins
        if gated is not None:
            return gated
        try:
            for ev in runtime.deliver(ext, rec, catch_up):
                try:
                    self.claims.heartbeat(token)
                except StaleClaimError as e:
                    self._late(token, did, "fenced_mid_delivery", error=str(e))
                    return CycleResult("fenced", rec.record_id, did, STARTED if started else NOT_STARTED, session_generation=gen)
                kind = ev[0]
                if kind == "started":
                    self.transition(token, did, STARTED, "started", execution_id=ev[1], external_session_id=ext, session_generation=gen)
                    with self.claims.fenced(token) as conn:
                        conn.execute("UPDATE bridge_deliveries SET execution_id=? WHERE delivery_id=?", (ev[1], did))
                    started = True
                    self._fire("bridge.after_runtime_start")
                elif kind == "output":
                    if not started:
                        break
                    self._note(token, did, "output", STARTED, chunk=ev[1])
                    self._fire("bridge.after_partial_output")
                elif kind == "terminal":
                    if started:
                        have_terminal, terminal = True, ev[1]
                    break
                else:  # timeout / disconnect / error: evidence only, certainty by durable start evidence
                    self._note(token, did, f"runtime_{kind}", STARTED if started else None, payload=list(ev[1:]))
                    break
        except PrespawnError as e:
            if started:
                self._note(token, did, "runtime_error", STARTED, error=str(e))
                if isinstance(e, ContextLostError):
                    self._mark_lost(token, "context_lost")
                return CycleResult("uncertain", rec.record_id, did, STARTED, session_generation=gen)
            self._resolve_invocation(token, did, "prespawn_failure", str(e), "prespawn_failure")
            if isinstance(e, ContextLostError):  # explicit pre-run loss: safe retry on a fresh generation (catch-up, not blind replay)
                self._mark_lost(token, "context_lost")
            return CycleResult("failed_not_started", rec.record_id, did, NOT_STARTED, session_generation=gen, detail={"error": str(e)})
        except RuntimeTargetError as e:
            if not started:
                self.transition(token, did, MAY_HAVE_STARTED, "start_uncertain", error=str(e))
                return CycleResult("uncertain", rec.record_id, did, MAY_HAVE_STARTED, session_generation=gen)
            self._note(token, did, "runtime_error", STARTED, error=str(e))
            return CycleResult("uncertain", rec.record_id, did, STARTED, session_generation=gen)
        if have_terminal:
            out = self._finalize(token, did, terminal)
            return CycleResult("delivered", rec.record_id, did, TERMINAL, out.response_record_id, gen)
        if not started:  # delivery was attempted but no start evidence either way
            self.transition(token, did, MAY_HAVE_STARTED, "start_uncertain")
            return CycleResult("uncertain", rec.record_id, did, MAY_HAVE_STARTED, session_generation=gen)
        self._note(token, did, "no_terminal", STARTED)
        return CycleResult("uncertain", rec.record_id, did, STARTED, session_generation=gen)

    def _late(self, token: ClaimToken, did: str | None, kind: str, **detail: Any) -> None:
        """Unfenced evidence-only write for stale/late callbacks (TD-25): never touches delivery state."""
        with self.claims._tx() as conn:
            self._evidence(conn, token.stream_id, token.peer_id, kind, did, None, claim_generation=token.generation, **detail)

    # ------------------------------------------------------------------ terminal finalization (BRG-006/014/019)
    @dataclass
    class FinalizeOutcome:
        outcome: str  # first | duplicate
        response_record_id: str | None

    @staticmethod
    def _validate_result(result: Any) -> str:
        if not isinstance(result, dict) or "response" not in result:
            raise InvalidTerminalResultError("terminal result must be a dict with a 'response' key")
        try:
            return _digest(result)
        except (TypeError, ValueError) as e:
            raise InvalidTerminalResultError(f"terminal result is not strict JSON: {e}") from e

    def latest_delivery_id(self, token: ClaimToken) -> str | None:
        with self.claims._tx() as conn:
            r = conn.execute("SELECT delivery_id FROM bridge_deliveries WHERE stream_id=? AND peer_id=? ORDER BY seq DESC LIMIT 1",
                             (token.stream_id, token.peer_id)).fetchone()
        return r[0] if r else None

    def finalize_terminal(self, token: ClaimToken, result: dict, delivery_id: str) -> "Bridge.FinalizeOutcome":
        """Callbacks are bound to the delivery_id they belong to (never inferred from 'newest')."""
        self._validate_result(result)
        return self._finalize(token, delivery_id, result)

    def _finalize(self, token: ClaimToken, did: str, result: dict) -> "Bridge.FinalizeOutcome":
        dig = self._validate_result(result)  # before anything is committed
        conflict = False
        try:
            with self.claims.fenced(token) as conn:
                row = conn.execute("SELECT * FROM bridge_deliveries WHERE delivery_id=?", (did,)).fetchone()
                if row is None:
                    raise NoDeliveryError(did)
                self._check_scope(row, token)
                if row["certainty"] == TERMINAL:
                    if row["result_digest"] == dig:
                        outcome = "duplicate"
                        self._evidence(conn, row["stream_id"], row["peer_id"], "duplicate_terminal", did, TERMINAL, digest=dig)
                    else:
                        conflict, outcome = True, "conflict"
                        self._evidence(conn, row["stream_id"], row["peer_id"], "conflicting_terminal", did, TERMINAL,
                                       digest=dig, committed_digest=row["result_digest"], result=result)
                else:
                    conn.execute("UPDATE bridge_deliveries SET result_json=?, result_digest=?, terminal_at=? WHERE delivery_id=?",
                                 (json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False), dig,
                                  _iso(self.claims._clock()), did))
                    self._transition_in(conn, did, TERMINAL, "terminal", token=token, digest=dig)  # illegal source state rolls the tx back
                    outcome = "first"
        except StaleClaimError as e:
            self._late(token, did, "stale_terminal_callback", digest=dig, error=str(e))
            raise
        if conflict:
            raise TerminalConflictError(f"delivery {did} already has committed terminal truth; differing result rejected")
        self._fire("bridge.after_terminal_evidence_before_response_append")
        rid = self._materialize(token, did)
        return Bridge.FinalizeOutcome(outcome, rid)

    def _materialize(self, token: ClaimToken, did: str) -> str:
        """Idempotent: append the response Record (fenced, idempotency key), then ack the Offset; each step safe to repeat."""
        with self.claims._tx() as conn:
            d = dict(conn.execute("SELECT * FROM bridge_deliveries WHERE delivery_id=?", (did,)).fetchone())
        self._check_scope(d, token)
        result = json.loads(d["result_json"])
        resp = self.store.append_record(
            guard=self.claims.guard(token), stream_id=d["stream_id"], author_peer_id=d["peer_id"], kind="response",
            body=result.get("response", result), reply_to=d["record_id"], created_at=d["terminal_at"],
            idempotency_key=f"bridge-response:{did}", metadata={"delivery_id": did, "execution_id": d["execution_id"]})
        with self.claims.fenced(token) as conn:
            if conn.execute("UPDATE bridge_deliveries SET response_record_id=? WHERE delivery_id=? AND response_record_id IS NULL",
                            (resp.record_id, did)).rowcount:
                self._evidence(conn, d["stream_id"], d["peer_id"], "response_appended", did, TERMINAL, record_id=resp.record_id)
        self._fire("bridge.after_terminal_evidence_before_offset_ack")
        for _ in range(self.ack_retries):
            off = self.store.get_offset(d["peer_id"], d["stream_id"])
            if off.read_through_position >= d["record_position"]:
                break
            try:
                self.store.advance_offset_cas(d["peer_id"], d["stream_id"], d["record_position"], off.revision,
                                              guard=self.claims.guard(token))
                break
            except CasMismatchError:
                continue
        else:
            if self.store.get_offset(d["peer_id"], d["stream_id"]).read_through_position < d["record_position"]:
                raise OffsetAckConflictError(f"offset CAS conflicted {self.ack_retries} times for {did}; delivery NOT acked")
        with self.claims.fenced(token) as conn:
            if conn.execute("UPDATE bridge_deliveries SET acked=1 WHERE delivery_id=? AND acked=0", (did,)).rowcount:
                self._evidence(conn, d["stream_id"], d["peer_id"], "offset_acked", did, TERMINAL, position=d["record_position"])
        return resp.record_id

    # ------------------------------------------------------------------ control (TD-05, CTL-001..005, BRG-008/009)
    def control_state(self, peer_id: str, stream_id: str) -> dict:
        """Gating state derived from ACCEPTED control intent, ordered by Record position (never by handling order)."""
        with self.claims._tx() as conn:
            last = conn.execute("SELECT kind, record_id FROM bridge_controls WHERE stream_id=? AND peer_id=? "
                                "AND kind IN ('control.pause','control.resume') ORDER BY position DESC LIMIT 1", (stream_id, peer_id)).fetchone()
            cancel = conn.execute("SELECT COALESCE(MAX(position),0) FROM bridge_controls WHERE stream_id=? AND peer_id=? "
                                  "AND kind='control.cancel'", (stream_id, peer_id)).fetchone()[0]
        paused = last is not None and last["kind"] == "control.pause"
        return {"paused": paused, "pause_record_id": last["record_id"] if paused else None, "last_cancel_position": cancel}

    def handle_control(self, record_id: str, runtime, peer_id: str | None = None) -> ControlResult:
        """Apply one durable control Record to `runtime` for the addressed peer. Authenticated ONLY against the persisted Record
        (kind, stream, author, targets); idempotent per (Record, peer): a replay or a concurrent duplicate never repeats the effect."""
        if not isinstance(record_id, str):
            raise TypeError("handle_control takes a persisted record_id (str), never a caller copy of the Record")
        with self.claims._tx() as conn:
            pr = conn.execute("SELECT * FROM records WHERE record_id=?", (record_id,)).fetchone()
            members = [r[0] for r in conn.execute("SELECT peer_id FROM stream_members WHERE stream_id=? ORDER BY peer_id",
                                                  (pr["stream_id"],))] if pr is not None else []
        if pr is None:
            raise ControlRejectedError("control Record is not durable")
        if not is_control_kind(pr["kind"]):
            raise ControlRejectedError(f"persisted kind {pr['kind']!r} is not a control Record")
        targets = json.loads(pr["targets_json"])
        if peer_id is None:
            cands = [m for m in (targets or members) if m != pr["author_peer_id"]]
            if len(cands) != 1:
                raise ControlRejectedError(f"cannot resolve the addressed peer from the Record ({len(cands)} candidates); pass peer_id")
            peer_id = cands[0]
        if peer_id not in members:
            raise ControlRejectedError(f"peer {peer_id!r} is not a member of stream {pr['stream_id']!r}")
        if peer_id == pr["author_peer_id"]:
            raise ControlRejectedError("the bridged peer cannot issue control Records for its own delivery")
        if targets and peer_id not in targets:
            raise ControlRejectedError(f"control Record is not addressed to peer {peer_id!r}")
        return self._apply_control(dict(pr), peer_id, runtime)

    def _control_result(self, status: str, row, outcome: str | None, **detail: Any) -> ControlResult:
        paused = self.control_state(row["peer_id"], row["stream_id"])["paused"]
        return ControlResult(status, row["record_id"], row["kind"], row["peer_id"], row["stream_id"], row["effect"], outcome, paused, detail)

    def _apply_control(self, pr: dict, peer: str, runtime, token: ClaimToken | None = None) -> ControlResult:
        kind, rid, stream = pr["kind"], pr["record_id"], pr["stream_id"]
        effect = CONTROL_EFFECT.get(kind, "none")
        lease_id = uuid.uuid4().hex
        now = self.claims._clock()
        sel = "SELECT * FROM bridge_controls WHERE record_id=? AND peer_id=?"
        # 1. durable intent acceptance + lease + persisted target (all commit BEFORE any runtime effect, TD-05, D-W4-1)
        early = None
        with self.claims._tx() as conn:
            row = conn.execute(sel, (rid, peer)).fetchone()
            if row is None:
                acc = conn.execute("SELECT COALESCE(MAX(seq),0) FROM bridge_evidence").fetchone()[0]
                conn.execute("INSERT INTO bridge_controls (record_id, stream_id, peer_id, kind, position, effect, accepted_seq) "
                             "VALUES (?,?,?,?,?,?,?)", (rid, stream, peer, kind, pr["position"], effect, acc))
                row = conn.execute(sel, (rid, peer)).fetchone()
            if row["outcome"] is not None:
                early = ("replayed", row["outcome"], {"error": row["error"]})
            elif effect == "none":
                outcome = _NO_EFFECT_OUTCOME.get(kind, "unsupported_kind")
                conn.execute("UPDATE bridge_controls SET outcome=? WHERE record_id=? AND peer_id=?", (outcome, rid, peer))
                self._evidence(conn, stream, peer, "control_applied", None, None, control_record_id=rid, control_kind=kind, effect=effect,
                               runtime_outcome=outcome)
                early = ("applied", outcome, {})
            elif row["lease_id"] is not None and now < row["lease_expires"]:  # TD-19: a live lease belongs to another handler
                early = ("in_progress", None, {})
            else:
                attempts = row["attempts"] + 1
                if row["target_delivery_id"] is None:  # decided ONCE; recovery never retargets
                    d = conn.execute("SELECT * FROM bridge_deliveries WHERE stream_id=? AND peer_id=? ORDER BY seq DESC LIMIT 1",
                                     (stream, peer)).fetchone()
                    if d is not None and not d["acked"] and d["certainty"] in (MAY_HAVE_STARTED, STARTED) and d["external_session_id"]:
                        tgt = (d["delivery_id"], d["external_session_id"], d["claim_generation"])
                    else:
                        tgt = ("-", None, None)
                    conn.execute("UPDATE bridge_controls SET target_delivery_id=?, target_session_id=?, target_claim_generation=? "
                                 "WHERE record_id=? AND peer_id=?", (*tgt, rid, peer))
                conn.execute("UPDATE bridge_controls SET lease_id=?, lease_expires=?, attempts=? WHERE record_id=? AND peer_id=?",
                             (lease_id, now + self.lease_sec, attempts, rid, peer))
        if early is not None:
            return self._control_result(early[0], row, early[1], **early[2])
        self._fire("control.after_intent_commit")
        # 2. fence EXECUTION: current lease AND (when called from a delivery cycle) the current claim token AND a still-current target
        status, outcome, error = None, None, None
        with self.claims._tx() as conn:
            row = conn.execute(sel, (rid, peer)).fetchone()
            if not (row["outcome"] is None and row["lease_id"] == lease_id and row["attempts"] == attempts
                    and self.claims._clock() < row["lease_expires"]):
                status = "superseded"
            elif token is not None and not self._token_current(conn, token):
                status = "fenced"
            elif row["target_delivery_id"] == "-":
                outcome = "nothing_running"
            else:
                d = conn.execute("SELECT * FROM bridge_deliveries WHERE delivery_id=?", (row["target_delivery_id"],)).fetchone()
                c = conn.execute("SELECT generation FROM bridge_claims WHERE stream_id=? AND peer_id=?", (stream, peer)).fetchone()
                if (d is None or d["acked"] or d["certainty"] not in (MAY_HAVE_STARTED, STARTED)
                        or c is None or c["generation"] != row["target_claim_generation"]):
                    outcome = "stale_target"
                elif not getattr(runtime, f"supports_{effect}", False):
                    outcome = "unsupported"
        if status is not None:
            return self._control_result(status, row, None)
        did = None if row["target_delivery_id"] == "-" else row["target_delivery_id"]
        if outcome is None:
            try:
                if effect == "steer":
                    self._steer(runtime, row["target_session_id"], pr)
                else:
                    getattr(runtime, effect)(row["target_session_id"])
                outcome = "done"
            except RuntimeTargetError as e:
                outcome, error = "failed", str(e)
        self._fire("control.after_effect_before_outcome")
        # 3. fence COMPLETION: outcome is write-once; only the CURRENT, UNEXPIRED lease holder (and current token) may write it
        with self.claims._tx() as conn:
            won = 0
            if token is None or self._token_current(conn, token):
                won = conn.execute("UPDATE bridge_controls SET outcome=?, error=?, delivery_id=? WHERE record_id=? AND peer_id=? "
                                   "AND outcome IS NULL AND lease_id=? AND attempts=? AND lease_expires > ?",
                                   (outcome, error, did, rid, peer, lease_id, attempts, self.claims._clock())).rowcount
            if won:
                self._evidence(conn, stream, peer, "control_applied", did, None, control_record_id=rid, control_kind=kind, effect=effect,
                               runtime_outcome=outcome, error=error)
            row = conn.execute(sel, (rid, peer)).fetchone()
        if not won:
            return self._control_result("superseded", row, None)
        return self._control_result("applied", row, outcome, error=error)

    def _token_current(self, conn, token: ClaimToken) -> bool:
        try:
            self.claims._check(conn, token)
            return True
        except StaleClaimError:
            return False

    def _steer(self, runtime, ext: str, pr: dict) -> None:
        rec = self._record_by_id(pr["stream_id"], pr["position"], pr["record_id"])
        runtime.steer(ext, rec)

    def _recover_stranded(self, token: ClaimToken, runtime) -> None:
        """A control whose lease expired without an outcome is revisited regardless of the Offset (D-W4-3)."""
        with self.claims._tx() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT record_id, stream_id, position, kind FROM bridge_controls WHERE stream_id=? AND peer_id=? AND outcome IS NULL "
                "AND (lease_id IS NULL OR lease_expires <= ?) ORDER BY position", (token.stream_id, token.peer_id, self.claims._clock()))]
        for r in rows:
            self._apply_control(r, token.peer_id, runtime, token)

    def _handle_unread_controls(self, token: ClaimToken, runtime) -> None:
        """Honour durable intent BEFORE any side effect: recover stranded controls, then apply every unprocessed control in the unread window."""
        self._recover_stranded(token, runtime)
        peer, stream = token.peer_id, token.stream_id
        cursor = self.store.get_offset(peer, stream).read_through_position
        while True:
            recs = self.store.read_records(stream, cursor, 100)
            if not recs:
                return
            for r in recs:
                cursor = r.position
                if r.author_peer_id != peer and is_control_kind(r.kind) and (not r.targets or peer in r.targets):
                    self._apply_control({"record_id": r.record_id, "stream_id": stream, "position": r.position, "kind": r.kind},
                                        peer, runtime, token)

    def _note_once(self, token: ClaimToken, did: str | None, kind: str, record_id: str, **detail: Any) -> None:
        with self.claims.fenced(token) as conn:
            for r in conn.execute("SELECT detail FROM bridge_evidence WHERE stream_id=? AND peer_id=? AND kind=?",
                                  (token.stream_id, token.peer_id, kind)):
                if json.loads(r[0]).get("record_id") == record_id:
                    return
            self._evidence(conn, token.stream_id, token.peer_id, kind, did, None, record_id=record_id, **detail)

    def _advance_to(self, token: ClaimToken, position: int) -> None:
        peer, stream = token.peer_id, token.stream_id
        for _ in range(self.ack_retries):
            off = self.store.get_offset(peer, stream)
            if off.read_through_position >= position:
                return
            try:
                self.store.advance_offset_cas(peer, stream, position, off.revision, guard=self.claims.guard(token))
                return
            except CasMismatchError:
                continue
        raise OffsetAckConflictError(f"offset CAS conflicted {self.ack_retries} times advancing to {position}")

    def _cancel_applies(self, token: ClaimToken, rec, did: str) -> str | None:
        """A cancel accepted AFTER this attempt was created (and, for a reconcile retry, positioned after the RETRY Record) cancels
        this attempt before it runs. Earlier unread Records are NOT affected (D-W4-7). Deterministic from persisted rows."""
        with self.claims._tx() as conn:
            d = conn.execute("SELECT reconcile_record_id FROM bridge_deliveries WHERE delivery_id=?", (did,)).fetchone()
            thr = rec.position
            if d is not None and d["reconcile_record_id"]:
                pr = conn.execute("SELECT position FROM records WHERE record_id=?", (d["reconcile_record_id"],)).fetchone()
                thr = max(thr, pr[0] if pr else 0)
            ev = conn.execute("SELECT seq FROM bridge_evidence WHERE delivery_id=? AND kind='attempt_created'", (did,)).fetchone()
            hit = conn.execute("SELECT record_id FROM bridge_controls WHERE stream_id=? AND peer_id=? AND kind='control.cancel' "
                               "AND position>? AND accepted_seq>=?", (token.stream_id, token.peer_id, thr, ev[0] if ev else 0)).fetchone()
        return hit[0] if hit else None

    def _retry_blocked_by_cancel(self, token: ClaimToken, reconcile_record_id: str | None) -> str | None:
        """D-W4-9: an authorization (RETRY Record) never overrides a cancel positioned AFTER it; a newer RETRY wins over an older cancel."""
        if not reconcile_record_id:
            return None
        with self.claims._tx() as conn:
            pr = conn.execute("SELECT position FROM records WHERE record_id=?", (reconcile_record_id,)).fetchone()
            hit = conn.execute("SELECT record_id FROM bridge_controls WHERE stream_id=? AND peer_id=? AND kind='control.cancel' AND position>?",
                               (token.stream_id, token.peer_id, pr[0] if pr else 0)).fetchone()
        return hit[0] if hit else None

    def _cancel_attempt(self, token: ClaimToken, rec, did: str, cancel_id: str) -> None:
        """Consume ONE cancelled in-flight attempt: evidence once, mark the attempt done, ack only this Record."""
        self._note_once(token, did, "cancelled_before_invoke", rec.record_id, cancel_record_id=cancel_id)
        self._fire("bridge.cancel_skip_before_ack")
        with self.claims.fenced(token) as conn:
            conn.execute("UPDATE bridge_deliveries SET acked=1 WHERE delivery_id=? AND certainty=?", (did, NOT_STARTED))
        self._advance_to(token, rec.position)

    def _gate(self, token: ClaimToken, runtime, rec, did: str, *, after_marker: bool = False) -> CycleResult | None:
        """Re-check durable intent right before a side effect (session create/resume, runtime invocation)."""
        self._handle_unread_controls(token, runtime)
        st = self.control_state(token.peer_id, token.stream_id)
        cancel = None if st["paused"] else self._cancel_applies(token, rec, did)
        if not st["paused"] and cancel is None:
            return None
        if after_marker:  # the invoke marker is already durable: RESOLVE it (nothing was invoked); certainty is untouched
            self._resolve_invocation(token, did, "control_halt", "halted by control intent before invoke", "control_halt")
        if st["paused"]:
            return CycleResult("paused", rec.record_id, did, NOT_STARTED, detail={"pause_record_id": st["pause_record_id"]})
        self._cancel_attempt(token, rec, did, cancel)
        return CycleResult("cancelled", rec.record_id, did, NOT_STARTED, detail={"cancel_record_id": cancel})

    # ------------------------------------------------------------------ reconciliation (TD-26, CERT-002/003)
    def reconcile_uncertain(self, record_id: str, reconciliation_record) -> dict:
        """Register a durable control.reconcile RETRY for the uncertain delivery of `record_id`; idempotent, one authorization per delivery."""
        # authenticate against the PERSISTED Record only; the caller's object contributes nothing but its id
        with self.claims._tx() as conn:
            pr = conn.execute("SELECT * FROM records WHERE record_id=?", (getattr(reconciliation_record, "record_id", None),)).fetchone()
        if pr is None:
            raise ReconcileRejectedError("reconciliation Record is not durable")
        if pr["kind"] != "control.reconcile":
            raise ReconcileRejectedError(f"persisted kind {pr['kind']!r} is not control.reconcile")
        body = json.loads(pr["body_json"])
        body = body if isinstance(body, dict) else {}
        if body.get("decision") != "RETRY":
            raise ReconcileRejectedError(f"decision {body.get('decision')!r} is not authorized in M1 (only RETRY, TD-26)")
        rr = type("Persisted", (), {"record_id": pr["record_id"], "stream_id": pr["stream_id"], "author": pr["author_peer_id"]})
        did = body.get("delivery_id")
        with self.claims._tx() as conn:
            d = conn.execute("SELECT * FROM bridge_deliveries WHERE delivery_id=?", (did,)).fetchone()
            if d is None or d["record_id"] != record_id or d["stream_id"] != rr.stream_id:
                raise ReconcileRejectedError("reconcile does not reference a delivery of this Record in this Stream")
            if rr.author == d["peer_id"]:
                raise ReconcileRejectedError("the bridged peer cannot authorize its own retry")
            if d["certainty"] != MAY_HAVE_STARTED:
                raise ReconcileRejectedError(f"delivery is {d['certainty']}; only MAY_HAVE_STARTED can be reconciled")
            cur = conn.execute("SELECT r.reconcile_record_id, r.consumed_attempt, p.position FROM bridge_reconciliations r "
                               "LEFT JOIN records p ON p.record_id = r.reconcile_record_id WHERE r.delivery_id=?", (did,)).fetchone()
            if cur is None:
                conn.execute("INSERT INTO bridge_reconciliations (delivery_id, reconcile_record_id, decision) VALUES (?,?,?)",
                             (did, rr.record_id, "RETRY"))
            elif cur["consumed_attempt"] is None and pr["position"] > (cur["position"] or 0):  # D-W4-9b: latest decision by position wins
                conn.execute("UPDATE bridge_reconciliations SET reconcile_record_id=? WHERE delivery_id=?", (rr.record_id, did))
            auth = dict(conn.execute("SELECT * FROM bridge_reconciliations WHERE delivery_id=?", (did,)).fetchone())
            self._evidence(conn, d["stream_id"], d["peer_id"], "reconcile_registered", did, d["certainty"],
                           reconcile_record_id=rr.record_id, authorized_by=auth["reconcile_record_id"])
        self._fire("bridge.after_reconcile_commit")
        return auth
