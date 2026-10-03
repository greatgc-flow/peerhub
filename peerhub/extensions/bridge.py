"""Session Bridge (Wave 3): session mapping, execution certainty ledger, fenced delivery cycle, reconciliation.

Extension-owned tables (Core never imports this module). Builds on `bridge_claims.ClaimStore` (TD-04/19/25).
Certainty (TD-11/TD-26): NOT_STARTED -> {NOT_STARTED, MAY_HAVE_STARTED, STARTED}; STARTED -> {STARTED, TERMINAL};
MAY_HAVE_STARTED and TERMINAL never change. A `control.reconcile` RETRY authorizes a NEW attempt row; the old row is untouched.
Ledger evidence is append-only (DB triggers); a TERMINAL delivery's result is immutable.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Protocol

from peerhub.extensions.bridge_claims import ClaimStore, ClaimToken, StaleClaimError
from peerhub.m1.store import CasMismatchError, CoreStore

NOT_STARTED, MAY_HAVE_STARTED, STARTED, TERMINAL = "NOT_STARTED", "MAY_HAVE_STARTED", "STARTED", "TERMINAL"
_ALLOWED = {
    NOT_STARTED: {NOT_STARTED, MAY_HAVE_STARTED, STARTED},
    MAY_HAVE_STARTED: {MAY_HAVE_STARTED},
    STARTED: {STARTED, TERMINAL},
    TERMINAL: {TERMINAL},
}


class IllegalCertaintyTransition(ValueError):
    """Forbidden execution-certainty transition (TD-11); nothing was written."""


class TerminalConflictError(RuntimeError):
    """A different terminal result arrived for a delivery whose terminal truth is already committed (BRG-019)."""


class NoDeliveryError(LookupError):
    """No delivery exists for the claim scope."""


class ReconcileRejectedError(ValueError):
    """The reconciliation Record cannot authorize a retry (wrong kind/decision/target/certainty)."""


class RuntimeTargetError(RuntimeError):
    """Ambiguous runtime failure (process/session may have started)."""


class PrespawnError(RuntimeTargetError):
    """Failure before any process/session start (certainty stays NOT_STARTED)."""


class SessionError(RuntimeError):
    """Session create/resume failure."""


class RuntimeTarget(Protocol):
    runtime_kind: str
    resumable: bool

    def fingerprint(self) -> str: ...
    def create_session(self) -> str: ...
    def resume_session(self, external_session_id: str) -> str: ...  # "ok" | "missing" | "unsupported" | "rejected"
    def deliver(self, external_session_id: str, record: Any, catch_up: list) -> Iterable[tuple]: ...


@dataclass
class CycleResult:
    status: str  # idle|delivered|recovered_terminal|failed_not_started|uncertain|blocked_uncertain|session_unavailable|fenced
    record_id: str | None = None
    delivery_id: str | None = None
    certainty: str | None = None
    response_record_id: str | None = None
    session_generation: int | None = None
    detail: dict = field(default_factory=dict)


_DDL = [
    """CREATE TABLE IF NOT EXISTS bridge_sessions (
        stream_id TEXT NOT NULL, peer_id TEXT NOT NULL, runtime_kind TEXT NOT NULL, external_session_id TEXT NOT NULL,
        session_generation INTEGER NOT NULL, adapter_fingerprint TEXT NOT NULL, binding TEXT NOT NULL,
        resumable INTEGER NOT NULL, state TEXT NOT NULL, last_seen REAL NOT NULL, PRIMARY KEY (stream_id, peer_id))""",
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
    """CREATE TRIGGER IF NOT EXISTS bridge_evidence_no_update BEFORE UPDATE ON bridge_evidence
        BEGIN SELECT RAISE(ABORT, 'bridge_evidence is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_evidence_no_delete BEFORE DELETE ON bridge_evidence
        BEGIN SELECT RAISE(ABORT, 'bridge_evidence is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_terminal_immutable
        BEFORE UPDATE OF certainty, result_json, result_digest ON bridge_deliveries WHEN OLD.certainty = 'TERMINAL'
        BEGIN SELECT RAISE(ABORT, 'terminal delivery is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS bridge_deliveries_no_delete BEFORE DELETE ON bridge_deliveries
        BEGIN SELECT RAISE(ABORT, 'bridge_deliveries rows are never deleted'); END""",
]


def _digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Bridge:
    def __init__(self, store: CoreStore, claims: ClaimStore, *, owner_id: str = "bridge-1", lease_sec: float = 30.0,
                 max_session_attempts: int = 3, catch_up_limit: int = 100,
                 fault_hook: Callable[[str], None] | None = None) -> None:
        self.store, self.claims = store, claims
        self.owner_id, self.lease_sec = owner_id, lease_sec
        self.max_session_attempts, self.catch_up_limit = max_session_attempts, catch_up_limit
        self._hook = fault_hook
        with claims._tx() as conn:
            for stmt in _DDL:
                conn.execute(stmt)

    def _fire(self, point: str) -> None:
        if self._hook is not None:
            self._hook(point)

    # ------------------------------------------------------------------ ledger primitives
    @staticmethod
    def _evidence(conn: sqlite3.Connection, stream_id: str, peer_id: str, kind: str, delivery_id: str | None = None,
                  certainty: str | None = None, **detail: Any) -> None:
        conn.execute("INSERT INTO bridge_evidence (delivery_id, stream_id, peer_id, kind, certainty, detail) VALUES (?,?,?,?,?,?)",
                     (delivery_id, stream_id, peer_id, kind, certainty, json.dumps(detail, sort_keys=True, default=str)))

    def _transition_in(self, conn: sqlite3.Connection, delivery_id: str, to: str, kind: str, **detail: Any) -> None:
        row = conn.execute("SELECT * FROM bridge_deliveries WHERE delivery_id=?", (delivery_id,)).fetchone()
        if row is None:
            raise NoDeliveryError(delivery_id)
        if to not in _ALLOWED.get(row["certainty"], set()):
            raise IllegalCertaintyTransition(f"{row['certainty']} -> {to} is forbidden (TD-11/TD-26)")
        if to != row["certainty"]:
            conn.execute("UPDATE bridge_deliveries SET certainty=? WHERE delivery_id=?", (to, delivery_id))
        self._evidence(conn, row["stream_id"], row["peer_id"], kind, delivery_id, to, **detail)

    def transition(self, token: ClaimToken, delivery_id: str, to: str, kind: str = "transition", **detail: Any) -> None:
        """Fenced certainty transition; forbidden transitions raise IllegalCertaintyTransition with no write."""
        with self.claims.fenced(token) as conn:
            self._transition_in(conn, delivery_id, to, kind, **detail)

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

    def _resolve_session(self, token: ClaimToken, runtime: RuntimeTarget, record) -> tuple[str, int] | None:
        m = self.current_mapping(token.peer_id, token.stream_id)
        fp = runtime.fingerprint()
        now = self.claims._clock()
        reason = None
        if m is not None and m["adapter_fingerprint"] == fp and m["resumable"] and m["state"] in ("ACTIVE", "FRESH"):
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
            reason = "fingerprint_change" if m["adapter_fingerprint"] != fp else ("not_resumable" if not m["resumable"] else m["state"].lower())
            with self.claims.fenced(token) as conn:
                self._session_event(conn, token, reason, m["state"], "FRESH", m["session_generation"])
        for i in range(self.max_session_attempts):
            try:
                ext = runtime.create_session()
            except SessionError as e:
                with self.claims.fenced(token) as conn:
                    self._session_event(conn, token, "create_failed", (m or {}).get("state", "NONE"), (m or {}).get("state", "NONE"),
                                        (m or {}).get("session_generation", 0), attempt=i + 1, error=str(e))
                continue
            gen = 1 if m is None else m["session_generation"] + 1
            with self.claims.fenced(token) as conn:
                conn.execute("INSERT OR REPLACE INTO bridge_sessions VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (token.stream_id, token.peer_id, runtime.runtime_kind, ext, gen, fp, "default",
                              1 if runtime.resumable else 0, "ACTIVE" if m is None else "FRESH", now))
                self._session_event(conn, token, "created" if m is None else "fresh_generation",
                                    "NONE" if m is None else ("LOST" if reason == "missing" else "ACTIVE"),
                                    "ACTIVE" if m is None else "FRESH", gen, reason=reason)
            return ext, gen
        return None

    def _catch_up(self, stream_id: str, record) -> list:
        prior = self.store.read_records(stream_id, 0, 2**31 - 1)
        return [r for r in prior if r.position < record.position][-self.catch_up_limit:]

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
        peer, stream = token.peer_id, token.stream_id
        infl = self._inflight(token)
        reconcile_of = None
        if infl is not None:
            rec = self._record_by_id(stream, infl["record_position"], infl["record_id"])
            if infl["certainty"] == TERMINAL:  # BRG-015: recognize persisted terminal truth; never re-run the runtime
                rid = self._materialize(token, infl["delivery_id"])
                return CycleResult("recovered_terminal", rec.record_id, infl["delivery_id"], TERMINAL, rid)
            if infl["certainty"] != NOT_STARTED:
                with self.claims._tx() as conn:
                    auth = conn.execute("SELECT * FROM bridge_reconciliations WHERE delivery_id=? AND consumed_attempt IS NULL",
                                        (infl["delivery_id"],)).fetchone()
                if auth is None or infl["certainty"] != MAY_HAVE_STARTED:
                    return CycleResult("blocked_uncertain", rec.record_id, infl["delivery_id"], infl["certainty"])
                reconcile_of = infl["delivery_id"]
        else:
            rec = self._next_record(token)
            if rec is None:
                return CycleResult("idle")
        did = self.begin_attempt(token, rec, reconcile_of=reconcile_of)
        sess = self._resolve_session(token, runtime, rec)
        if sess is None:
            self._note(token, did, "session_unavailable", NOT_STARTED, attempts=self.max_session_attempts)
            return CycleResult("session_unavailable", rec.record_id, did, NOT_STARTED,
                               detail={"attempts": self.max_session_attempts})
        ext, gen = sess
        with self.claims.fenced(token) as conn:
            conn.execute("UPDATE bridge_deliveries SET external_session_id=?, session_generation=? WHERE delivery_id=?", (ext, gen, did))
        catch_up = self._catch_up(stream, rec) if self._fresh_generation(peer, stream) else []
        return self._run_runtime(token, runtime, rec, did, ext, gen, catch_up)

    def _fresh_generation(self, peer: str, stream: str) -> bool:
        ev = self.session_events(peer, stream)
        return bool(ev) and ev[-1]["event"] in ("created", "fresh_generation")

    def _next_record(self, token: ClaimToken):
        peer, stream = token.peer_id, token.stream_id
        while True:
            off = self.store.get_offset(peer, stream)
            recs = self.store.read_records(stream, off.read_through_position, 50)
            if not recs:
                return None
            for r in recs:
                if r.author_peer_id != peer and r.kind != "control.reconcile":  # reconcile Records are consumed via reconcile_uncertain
                    return r
                self.store.advance_offset_cas(peer, stream, r.position, self.store.get_offset(peer, stream).revision,
                                              guard=self.claims.guard(token))  # skip own response Records
            # all fetched were own; loop for more

    def _run_runtime(self, token, runtime, rec, did, ext, gen, catch_up) -> CycleResult:
        started = False
        terminal: Any = None
        have_terminal = False
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
                return CycleResult("uncertain", rec.record_id, did, STARTED, session_generation=gen)
            self.transition(token, did, NOT_STARTED, "prespawn_failure", error=str(e))
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

    def finalize_terminal(self, token: ClaimToken, result: dict) -> "Bridge.FinalizeOutcome":
        with self.claims._tx() as conn:
            row = conn.execute("SELECT delivery_id FROM bridge_deliveries WHERE stream_id=? AND peer_id=? ORDER BY seq DESC LIMIT 1",
                               (token.stream_id, token.peer_id)).fetchone()
        if row is None:
            raise NoDeliveryError("no delivery for claim scope")
        return self._finalize(token, row["delivery_id"], result)

    def _finalize(self, token: ClaimToken, did: str, result: dict) -> "Bridge.FinalizeOutcome":
        dig = _digest(result)
        conflict = False
        try:
            with self.claims.fenced(token) as conn:
                row = conn.execute("SELECT * FROM bridge_deliveries WHERE delivery_id=?", (did,)).fetchone()
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
                    self._transition_in(conn, did, TERMINAL, "terminal", digest=dig)  # illegal source state rolls the tx back
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
        for _ in range(8):
            off = self.store.get_offset(d["peer_id"], d["stream_id"])
            if off.read_through_position >= d["record_position"]:
                break
            try:
                self.store.advance_offset_cas(d["peer_id"], d["stream_id"], d["record_position"], off.revision,
                                              guard=self.claims.guard(token))
                break
            except CasMismatchError:
                continue
        with self.claims.fenced(token) as conn:
            if conn.execute("UPDATE bridge_deliveries SET acked=1 WHERE delivery_id=? AND acked=0", (did,)).rowcount:
                self._evidence(conn, d["stream_id"], d["peer_id"], "offset_acked", did, TERMINAL, position=d["record_position"])
        return resp.record_id

    # ------------------------------------------------------------------ reconciliation (TD-26, CERT-002/003)
    def reconcile_uncertain(self, record_id: str, reconciliation_record) -> dict:
        """Register a durable control.reconcile RETRY for the uncertain delivery of `record_id`; idempotent, one authorization per delivery."""
        rr = reconciliation_record
        body = rr.body if isinstance(rr.body, dict) else {}
        if rr.kind != "control.reconcile":
            raise ReconcileRejectedError(f"kind {rr.kind!r} is not control.reconcile")
        if body.get("decision") != "RETRY":
            raise ReconcileRejectedError(f"decision {body.get('decision')!r} is not authorized in M1 (only RETRY, TD-26)")
        durable = [r for r in self.store.read_records(rr.stream_id, rr.position - 1, 1) if r.record_id == rr.record_id]
        if not durable:
            raise ReconcileRejectedError("reconciliation Record is not durable in its Stream")
        did = body.get("delivery_id")
        with self.claims._tx() as conn:
            d = conn.execute("SELECT * FROM bridge_deliveries WHERE delivery_id=?", (did,)).fetchone()
            if d is None or d["record_id"] != record_id or d["stream_id"] != rr.stream_id:
                raise ReconcileRejectedError("reconcile does not reference a delivery of this Record in this Stream")
            if d["certainty"] != MAY_HAVE_STARTED:
                raise ReconcileRejectedError(f"delivery is {d['certainty']}; only MAY_HAVE_STARTED can be reconciled")
            conn.execute("INSERT OR IGNORE INTO bridge_reconciliations (delivery_id, reconcile_record_id, decision) VALUES (?,?,?)",
                         (did, rr.record_id, "RETRY"))
            auth = dict(conn.execute("SELECT * FROM bridge_reconciliations WHERE delivery_id=?", (did,)).fetchone())
            self._evidence(conn, d["stream_id"], d["peer_id"], "reconcile_registered", did, d["certainty"],
                           reconcile_record_id=rr.record_id, authorized_by=auth["reconcile_record_id"])
        self._fire("bridge.after_reconcile_commit")
        return auth
