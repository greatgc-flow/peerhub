"""Fenced execution evidence, certainty transitions, terminal settlement, and reconciliation.

A separate invocation marker is written before invocation (TD-11/TD-26; D-OWN-A2/A4).
Recovery promotes unresolved markers to MAY_HAVE_STARTED; proven pre-spawn failure and
control halt resolve them. Certainty only advances, and late evidence can promote
MAY_HAVE_STARTED to STARTED or TERMINAL. RETRY authorizes a new attempt row.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, cast

from peerhub.core.models import Record
from peerhub.core.peer_kinds import BUILTIN_PEER_KINDS
from peerhub.core.store import CasMismatchError
from peerhub.extensions import ask_budgets, bridge_registry, delivery_projections, session_receipts
from peerhub.extensions.bridge_claims import ClaimScopeError, ClaimToken, StaleClaimError
from peerhub.extensions.bridge_control import BUILTIN_PEER_MARKS, MAY_HAVE_STARTED, NOT_STARTED, STARTED, TERMINAL, BridgeControl
from peerhub.extensions.bridge_errors import (
    IllegalCertaintyTransition, InvalidTerminalResultError, InvocationMarkerError, NoDeliveryError,
    OffsetAckConflictError, ReconcileRejectedError, RecordRejectedError, TerminalConflictError,
)
from peerhub.extensions.liveness import PROCESSES_DEAD, ask_process_state, claim_wrapper_alive
from peerhub.extensions.schema_guard import refuse_future_schema
from peerhub.extensions.sqlite_tx import sqlite_tx

_ALLOWED = {
    NOT_STARTED: {NOT_STARTED, MAY_HAVE_STARTED, STARTED},
    MAY_HAVE_STARTED: {MAY_HAVE_STARTED, STARTED, TERMINAL},  # forward edges on late evidence only (D-W3-3)
    STARTED: {STARTED, TERMINAL},
    TERMINAL: {TERMINAL},
}

# These reads are correlated to the selected delivery. Each invocation commits
# its marker and caller snapshot together. Older stores have separate caller
# evidence and unnumbered starts, which are bounded by the marker's sequence.
LATEST_INVOCATION = (
    "LEFT JOIN bridge_invocations i ON i.delivery_id=d.delivery_id AND i.invocation_no=("
    "SELECT MAX(n.invocation_no) FROM bridge_invocations n WHERE n.delivery_id=d.delivery_id) "
)


def invocation_marker_join(delivery: str) -> str:
    return (
        "LEFT JOIN bridge_evidence m ON m.seq=(SELECT e.seq FROM bridge_evidence e "
        f"WHERE e.delivery_id={delivery} AND e.kind='about_to_invoke' ORDER BY e.seq DESC LIMIT 1) "
    )


def invocation_process_fields(delivery: str) -> str:
    return (
        "CASE WHEN json_type(CASE WHEN json_valid(m.detail) THEN m.detail END,'$.caller') IS NOT NULL "
        "THEN json_extract(m.detail,'$.caller') ELSE "
        f"(SELECT e.detail FROM bridge_evidence e WHERE e.delivery_id={delivery} AND e.kind='caller' "
        "AND (m.seq IS NULL OR e.seq<m.seq) ORDER BY e.seq DESC LIMIT 1) END AS caller_detail,"
        f"(SELECT e.detail FROM bridge_evidence e WHERE e.delivery_id={delivery} AND e.kind IN ('started','launch_reserved') "
        "AND (m.seq IS NULL OR e.seq>m.seq) AND ("
        "json_type(CASE WHEN json_valid(e.detail) THEN e.detail END,'$.invocation_no') IS NULL OR "
        "json_extract(CASE WHEN json_valid(e.detail) THEN e.detail END,'$.invocation_no')="
        "json_extract(CASE WHEN json_valid(m.detail) THEN m.detail END,'$.invocation_no')) "
        "ORDER BY e.seq DESC LIMIT 1) AS provider_detail "
    )


@dataclass(frozen=True)
class PersistedRecord:
    """Facts of the Core-persisted Record a delivery is bound to (never the caller's copy)."""
    record_id: str
    position: int


@dataclass(frozen=True)
class _PersistedReconcile:
    record_id: str
    stream_id: str
    author: str


@dataclass
class CycleResult:
    status: str  # idle|delivered|recovered_terminal|denied_empty|not_executed|truncated|incomplete|failed_not_started|uncertain|blocked_uncertain|session_unavailable|fenced|paused|cancelled
    record_id: str | None = None
    delivery_id: str | None = None
    certainty: str | None = None
    response_record_id: str | None = None
    session_generation: int | None = None
    detail: dict[str, Any] = field(default_factory=dict[str, Any])
    session: dict[str, Any] | None = None


def digest(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def iso_timestamp(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reap_dead_asks(db_path: str | Path) -> int:
    """Release abandoned asks in an existing workspace, without initializing a store."""
    db = Path(db_path).resolve()
    generation = db.parent / "workspace.generation"
    if not db.is_file() or not generation.is_file():
        return 0
    refuse_future_schema(db)
    with sqlite_tx(db.as_uri() + "?mode=rw", uri=True, timeout=30.0) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"bridge_claims", "bridge_deliveries", "bridge_evidence", "bridge_invocations",
                "bridge_invocation_resolutions", "bridge_monitor_delivery_scope", "bridge_sessions",
                "bridge_session_history", "bridge_session_events"} <= tables:
            return 0
        return BridgeLedger.release_dead_asks(conn, generation.read_text(encoding="utf-8"), time.time())


class BridgeLedger(BridgeControl):
    """Durable delivery facts and their fenced writes."""

    ack_retries: int
    caller: dict[str, Any] | None

    # ------------------------------------------------------------------ ledger primitives

    @staticmethod
    def _evidence(conn: sqlite3.Connection, stream_id: str, peer_id: str, kind: str, delivery_id: str | None = None,
                  certainty: str | None = None, **detail: Any) -> None:
        conn.execute("INSERT INTO bridge_evidence (delivery_id, stream_id, peer_id, kind, certainty, detail) VALUES (?,?,?,?,?,?)",
                     (delivery_id, stream_id, peer_id, kind, certainty, json.dumps(detail, sort_keys=True, default=str)))

    @staticmethod
    def _check_scope(row: sqlite3.Row | Mapping[str, Any], token: ClaimToken) -> None:
        if (row["stream_id"], row["peer_id"]) != (token.stream_id, token.peer_id):
            raise ClaimScopeError(f"claim for ({token.peer_id}, {token.stream_id}) cannot mutate delivery {row['delivery_id']}")

    def _transition_in(self, conn: sqlite3.Connection, delivery_id: str, to: str, kind: str, *,
                       token: ClaimToken | None = None, **detail: Any) -> None:
        self._transition_at(conn, delivery_id, to, kind, self.claims.now(), token=token, **detail)

    @classmethod
    def _transition_at(cls, conn: sqlite3.Connection, delivery_id: str, to: str, kind: str, observed_at: float, *,
                       token: ClaimToken | None = None, **detail: Any) -> None:
        row = conn.execute("SELECT * FROM bridge_deliveries WHERE delivery_id=?", (delivery_id,)).fetchone()
        if row is None:
            raise NoDeliveryError(delivery_id)
        if token is not None:
            cls._check_scope(row, token)  # BEFORE any mutation
        old_certainty = str(row["certainty"])
        if to not in _ALLOWED.get(old_certainty, set()):
            raise IllegalCertaintyTransition(f"{row['certainty']} -> {to} is forbidden (TD-11/TD-26)")
        if to != row["certainty"]:
            conn.execute("UPDATE bridge_deliveries SET certainty=? WHERE delivery_id=?", (to, delivery_id))
        if to == TERMINAL and kind == "terminal" and old_certainty != to:
            detail["peerhub_timing"] = session_receipts.terminal_timing(conn, row)
        cls._evidence(conn, row["stream_id"], row["peer_id"], kind, delivery_id, to, **detail)
        current_row = row
        if old_certainty != to or (to == TERMINAL and kind == "terminal"):
            current_row = conn.execute("SELECT * FROM bridge_deliveries WHERE delivery_id=?", (delivery_id,)).fetchone()
            assert current_row is not None
        if old_certainty != to:
            delivery_projections.record_delivery_transition(conn, current_row, old_certainty, to, observed_at=observed_at)
        if to == TERMINAL and kind == "terminal" and old_certainty != to:
            delivery_projections.record_latency(conn, current_row, observed_at, detail["peerhub_timing"])
        if to == STARTED:
            session_receipts.call(conn, row, observed_at)
        if to == MAY_HAVE_STARTED:
            mapping = bridge_registry.generation(conn, row["stream_id"], row["peer_id"], row["session_generation"])
            won = conn.execute("UPDATE bridge_session_history SET state='LOST', vendor_session_id=NULL WHERE stream_id=? AND peer_id=? "
                               "AND session_generation=? AND external_session_id=? AND state NOT IN ('LOST','TOMBSTONED') "
                               f"AND (vendor_session_id IS NOT NULL OR runtime_kind IN ({BUILTIN_PEER_MARKS}))",
                               (row["stream_id"], row["peer_id"], row["session_generation"], row["external_session_id"],
                                *BUILTIN_PEER_KINDS)).rowcount
            if won:
                bridge_registry.repair(conn, row["stream_id"], row["peer_id"], observed_at)
                assert mapping is not None
                conn.execute("INSERT INTO bridge_session_events(stream_id,peer_id,event,from_state,to_state,generation,detail) VALUES (?,?,'session_lost',?,'LOST',?,?)",
                             (row["stream_id"], row["peer_id"], mapping["state"], row["session_generation"], session_receipts.dumps({"reason": "tainted"})))
                cls._evidence(conn, row["stream_id"], row["peer_id"], "session_tainted", delivery_id, to,
                               reason="tainted", session_generation=row["session_generation"])

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
            if row["certainty"] != NOT_STARTED or self.open_invocation(conn, delivery_id) is not None:
                raise InvocationMarkerError(f"cannot mark an invocation for a {row['certainty']} delivery or one with an unresolved marker")
            n = conn.execute("SELECT COALESCE(MAX(invocation_no),0)+1 FROM bridge_invocations WHERE delivery_id=?", (delivery_id,)).fetchone()[0]
            conn.execute("INSERT INTO bridge_invocations (delivery_id, invocation_no, stream_id, peer_id, claim_generation, "
                         "external_session_id, session_generation) VALUES (?,?,?,?,?,?,?)",
                         (delivery_id, n, token.stream_id, token.peer_id, token.generation, detail.get("external_session_id"),
                          detail.get("session_generation")))
            # #374: durable before the provider can start; once per delivery and process. Another ask process may run a later
            # invocation of the same NOT_STARTED delivery (begin_attempt reuses it), so a new identity gets its own row.
            if self.caller is not None and self._caller_changed(conn, delivery_id):
                conn.execute("INSERT INTO bridge_evidence (delivery_id, stream_id, peer_id, kind, certainty, detail) VALUES (?,?,?,?,?,?)",
                             (delivery_id, token.stream_id, token.peer_id, "caller", NOT_STARTED,
                              json.dumps(self.caller, sort_keys=True, default=str)))
            self._evidence(conn, token.stream_id, token.peer_id, "about_to_invoke", delivery_id, NOT_STARTED,
                           invocation_no=n, claim_generation=token.generation, caller=self.caller,
                           quota_invoked_at=self.claims.now(), **detail)
            return n

    _CALLER_IDENTITY = ("host", "ns", "ask_pid", "ask_created")

    def _caller_changed(self, conn: sqlite3.Connection, delivery_id: str) -> bool:
        """True when the delivery has no caller row yet or its newest one names another process (or no readable identity). An unknown
        (None) identity value on either side never establishes continuity: two unreadable identities are not one process."""
        assert self.caller is not None
        row = conn.execute("SELECT detail FROM bridge_evidence WHERE delivery_id=? AND kind='caller' ORDER BY seq DESC LIMIT 1",
                           (delivery_id,)).fetchone()
        if row is None:
            return True
        try:
            newest: Any = json.loads(row[0])
        except (ValueError, TypeError):
            return True
        if not isinstance(newest, dict):
            return True
        newest = cast("dict[str, Any]", newest)
        keys = self._CALLER_IDENTITY
        if newest.get("ask_identity") is not None or self.caller.get("ask_identity") is not None:
            keys = ("host", "ns", "ask_pid", "ask_identity")
        if any(newest.get(key) is None or self.caller.get(key) is None for key in keys):
            return True
        return any(newest[key] != self.caller[key] for key in keys)

    @staticmethod
    def open_invocation(conn: sqlite3.Connection, delivery_id: str) -> int | None:
        """Read the latest unresolved invocation marker in the caller's snapshot."""
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
            n = self.open_invocation(conn, delivery_id)
            if row["certainty"] != NOT_STARTED or n is None or row["execution_id"] is not None:
                raise InvocationMarkerError(f"no unresolved invocation marker to resolve on a {row['certainty']} delivery")
            self._evidence(conn, row["stream_id"], row["peer_id"], kind, delivery_id, NOT_STARTED, error=error, invocation_no=n)
            conn.execute("INSERT INTO bridge_invocation_resolutions (delivery_id, invocation_no, reason, claim_generation) VALUES (?,?,?,?)",
                         (delivery_id, n, reason, token.generation))
        self._fire("bridge.after_invocation_resolved")

    def _promote_unresolved(self, token: ClaimToken) -> None:
        """Recovery: an unresolved marker means the runtime may have been invoked -> durable MAY_HAVE_STARTED with evidence."""
        q = ("SELECT d.delivery_id, i.invocation_no FROM bridge_deliveries d JOIN bridge_invocations i ON i.delivery_id=d.delivery_id "
             "WHERE d.stream_id=? AND d.peer_id=? AND d.certainty=? AND NOT EXISTS (SELECT 1 FROM bridge_invocation_resolutions r "
             "WHERE r.delivery_id=i.delivery_id AND r.invocation_no=i.invocation_no)")
        args = (token.stream_id, token.peer_id, NOT_STARTED)
        with self.claims.transaction() as conn:
            if not conn.execute(q, args).fetchall():
                return  # nothing dangling: a read-only (possibly stale) cycle writes nothing
        with self.claims.fenced(token) as conn:
            for did, n in conn.execute(q, args).fetchall():
                self._promote_invocation(conn, did, n, token.generation, self.claims.now(), token=token)

    @classmethod
    def _promote_invocation(cls, conn: sqlite3.Connection, did: str, invocation_no: int, generation: int,
                            now: float, *, token: ClaimToken | None = None) -> None:
        cls._transition_at(conn, did, MAY_HAVE_STARTED, "invocation_unresolved_recovered", now,
                           token=token, invocation_no=invocation_no)
        conn.execute("INSERT INTO bridge_invocation_resolutions (delivery_id, invocation_no, reason, claim_generation) VALUES (?,?,?,?)",
                     (did, invocation_no, "promoted_may_have_started", generation))

    @staticmethod
    def _claim_activity(conn: sqlite3.Connection, workspace_generation: str, now: float,
                        *, include_expired: bool = False) -> sqlite3.Cursor:
        has_callers = conn.execute("SELECT 1 FROM sqlite_master WHERE name='bridge_claim_callers'").fetchone() is not None
        claim_join = ("LEFT JOIN bridge_claim_callers a ON a.stream_id=c.stream_id AND a.peer_id=c.peer_id "
                      "AND a.claim_generation=c.generation " if has_callers else "")
        claim_detail = "a.detail" if has_callers else "NULL"
        return conn.execute(
            "SELECT c.stream_id,c.peer_id,c.generation,c.expires_at,d.delivery_id,d.certainty,i.invocation_no,"
            "i.claim_generation AS invocation_generation,r.reason AS resolution,"
            "d.claim_generation AS delivery_generation,d.acked,d.result_json IS NULL AS no_result,"
            f"{claim_detail} AS claim_detail,"
            "(SELECT e.kind FROM bridge_evidence e WHERE e.delivery_id=d.delivery_id AND e.kind IN "
            "('about_to_invoke','launch_reserved','started','prespawn_failure','control_halt','no_terminal',"
            "'start_uncertain','processes_dead','refusal_detected') ORDER BY e.seq DESC LIMIT 1) AS last_kind,"
            + invocation_process_fields("d.delivery_id")
            + "FROM bridge_claims c "
            + claim_join
            + "LEFT JOIN bridge_deliveries d ON d.delivery_id=(SELECT delivery_id FROM bridge_deliveries "
            "WHERE stream_id=c.stream_id AND peer_id=c.peer_id ORDER BY seq DESC LIMIT 1) "
            + LATEST_INVOCATION
            + "LEFT JOIN bridge_invocation_resolutions r ON r.delivery_id=i.delivery_id AND r.invocation_no=i.invocation_no "
            + invocation_marker_join("d.delivery_id")
            + "WHERE c.workspace_generation=? AND (c.expires_at>? OR "
            "(? AND i.claim_generation=c.generation AND d.acked=0 AND d.result_json IS NULL AND d.certainty!='TERMINAL'))",
            (workspace_generation, now, include_expired))

    @staticmethod
    def _current_invocation(row: sqlite3.Row) -> bool:
        return (row["acked"] == 0 and bool(row["no_result"]) and row["certainty"] != TERMINAL
                and row["invocation_generation"] == row["generation"]
                and row["resolution"] not in ("prespawn_failure", "control_halt")
                and row["last_kind"] not in ("processes_dead", "refusal_detected"))

    @classmethod
    def has_active_asks(cls, conn: sqlite3.Connection, workspace_generation: str, now: float) -> bool:
        """Read activity inside the transaction that will reserve the next launch."""
        for row in cls._claim_activity(conn, workspace_generation, now):
            if cls._current_invocation(row):
                if ask_process_state(row["caller_detail"], row["provider_detail"]) != PROCESSES_DEAD:
                    return True
            elif row["claim_detail"] is not None:
                if claim_wrapper_alive(row["claim_detail"]) is not False:
                    return True
            elif (row["delivery_generation"] == row["generation"] and row["acked"] == 0
                  and row["certainty"] in (NOT_STARTED, STARTED)
                  and row["last_kind"] not in ("processes_dead", "refusal_detected")):
                if ask_process_state(row["caller_detail"], row["provider_detail"]) != PROCESSES_DEAD:
                    return True  # legacy lease-active delivery
        return False

    @classmethod
    def release_dead_asks(cls, conn: sqlite3.Connection, workspace_generation: str, now: float) -> int:
        """Record abandonment and expire the current claim in the caller's BEGIN IMMEDIATE transaction."""
        count = 0
        for row in cls._claim_activity(conn, workspace_generation, now, include_expired=True).fetchall():
            if cls._current_invocation(row):
                if row["expires_at"] <= now and row["last_kind"] not in ("about_to_invoke", "launch_reserved", "started"):
                    continue
                if ask_process_state(row["caller_detail"], row["provider_detail"]) != PROCESSES_DEAD:
                    continue
                certainty = row["certainty"]
                did = row["delivery_id"]
                if certainty == NOT_STARTED:
                    cls._promote_invocation(conn, did, row["invocation_no"], row["generation"], now)
                    certainty = MAY_HAVE_STARTED
                cls._evidence(conn, row["stream_id"], row["peer_id"], "processes_dead", did, certainty,
                              reason="processes_dead", outcome="UNCERTAIN", observed_at_s=now,
                              invocation_no=row["invocation_no"])
            elif row["expires_at"] <= now or claim_wrapper_alive(row["claim_detail"]) is not False:
                continue
            conn.execute("UPDATE bridge_claims SET expires_at=MIN(expires_at,?) WHERE stream_id=? AND peer_id=? "
                         "AND generation=? AND workspace_generation=?",
                         (now, row["stream_id"], row["peer_id"], row["generation"], workspace_generation))
            count += 1
        return count

    @staticmethod
    def _authentic_record(conn: sqlite3.Connection, token: ClaimToken, record: object) -> "PersistedRecord":
        """Never trust a caller copy: the Record must exist in Core, belong to the claim's Stream, be addressed to the claim's peer and match
        the persisted position/author/kind. Returns the persisted facts."""
        pr = conn.execute("SELECT * FROM records WHERE record_id=?", (getattr(record, "record_id", None),)).fetchone()
        if pr is None:
            raise RecordRejectedError(f"record {getattr(record, 'record_id', None)!r} is not durable")
        if pr["stream_id"] != token.stream_id:
            raise RecordRejectedError("record belongs to another Stream than the claim")
        if pr["author_peer_id"] == token.peer_id:
            raise RecordRejectedError("a peer's own Record is never delivered to it")
        targets = json.loads(pr["targets_json"] or "[]")
        if targets and token.peer_id not in targets:
            raise RecordRejectedError("record is not addressed to the claim's peer")
        for attr, col in (("position", "position"), ("author_peer_id", "author_peer_id"), ("kind", "kind"), ("stream_id", "stream_id")):
            if getattr(record, attr, pr[col]) != pr[col]:
                raise RecordRejectedError(f"caller copy disagrees with the persisted Record ({attr})")
        return PersistedRecord(pr["record_id"], pr["position"])

    def begin_attempt(self, token: ClaimToken, record: object, *, reconcile_of: str | None = None) -> str:
        """Create (or, for NOT_STARTED retry, reuse) the attempt row for `record`; returns delivery_id."""
        with self.claims.fenced(token) as conn:
            s, p = token.stream_id, token.peer_id
            record = self._authentic_record(conn, token, record)
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
            inserted = conn.execute("SELECT * FROM bridge_deliveries WHERE delivery_id=?", (did,)).fetchone()
            assert inserted is not None
            delivery_projections.record_delivery_created(conn, inserted)
            self._evidence(conn, s, p, "attempt_created", did, NOT_STARTED, attempt=attempt, reconcile_record_id=rec_rec,
                           claim_generation=token.generation)
            return did

    def _note(self, token: ClaimToken, delivery_id: str | None, kind: str, certainty: str | None = None, **detail: Any) -> None:
        with self.claims.fenced(token) as conn:
            self._evidence(conn, token.stream_id, token.peer_id, kind, delivery_id, certainty, **detail)

    def _late(self, token: ClaimToken, did: str | None, kind: str, **detail: Any) -> None:
        """Unfenced evidence-only write for stale/late callbacks (TD-25): never touches delivery state."""
        with self.claims.transaction() as conn:
            self._evidence(conn, token.stream_id, token.peer_id, kind, did, None, claim_generation=token.generation, **detail)

    # ------------------------------------------------------------------ delivery lookup

    def _inflight(self, token: ClaimToken) -> sqlite3.Row | None:
        with self.claims.transaction() as conn:
            return self._read_inflight(conn, token.peer_id, token.stream_id)

    @staticmethod
    def _read_inflight(conn: sqlite3.Connection, peer: str, stream: str) -> sqlite3.Row | None:
        row = conn.execute("SELECT * FROM bridge_deliveries WHERE stream_id=? AND peer_id=? ORDER BY seq DESC LIMIT 1",
                           (stream, peer)).fetchone()
        return row if row is not None and row["acked"] == 0 else None  # sequential per scope: only the newest attempt can be in flight

    @staticmethod
    def has_unsettled_delivery(conn: sqlite3.Connection, peer: str, stream: str) -> bool:
        return conn.execute("SELECT 1 FROM bridge_deliveries WHERE stream_id=? AND peer_id=? AND acked=0 LIMIT 1",
                            (stream, peer)).fetchone() is not None

    def _record_by_id(self, stream_id: str, position: int, record_id: str) -> Record:
        for r in self.store.read_records(stream_id, position - 1, 1):
            if r.record_id == record_id:
                return r
        raise NoDeliveryError(f"record {record_id} not found")

    def latest_delivery_id(self, token: ClaimToken) -> str | None:
        with self.claims.transaction() as conn:
            r = conn.execute("SELECT delivery_id FROM bridge_deliveries WHERE stream_id=? AND peer_id=? ORDER BY seq DESC LIMIT 1",
                             (token.stream_id, token.peer_id)).fetchone()
        return r[0] if r else None

    def execution_evidence(self, record_id: str, peer_id: str) -> dict[str, Any]:
        with self.claims.transaction() as conn:
            dels = [dict(r) for r in conn.execute("SELECT * FROM bridge_deliveries WHERE record_id=? AND peer_id=? ORDER BY attempt",
                                                  (record_id, peer_id))]
            ev = [dict(r) for r in conn.execute(
                "SELECT e.* FROM bridge_evidence e WHERE e.peer_id=? AND e.delivery_id IN "
                "(SELECT delivery_id FROM bridge_deliveries WHERE record_id=? AND peer_id=?) ORDER BY e.seq",
                (peer_id, record_id, peer_id))]
        for e in ev:
            e["detail"] = json.loads(e["detail"])
        return {"deliveries": dels, "events": ev}

    def delivery_details(self, delivery_id: str | None) -> dict[str, Any]:
        with self.claims.transaction() as conn:
            return self._read_delivery_details(conn, delivery_id)

    @staticmethod
    def _read_delivery_details(conn: sqlite3.Connection, delivery_id: str | None) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for kind, detail in conn.execute("SELECT kind, detail FROM bridge_evidence WHERE delivery_id=? ORDER BY seq", (delivery_id,)):
            data = json.loads(detail)
            if kind == "execution_metadata":
                for key in ("env_scrubbed", "ag_transport", "ag_transport_source", "ag_cli_version"):
                    if key in data:
                        out[key] = data[key]
            if kind == "terminal":
                if "effective_mode" in data:
                    out.update({key: data[key] for key in ("effective_mode", "fallback_reason", "injected_record_ids")})
                if "output_truncated" in data:
                    out["output_truncated"] = data["output_truncated"]
                if "display_truncated" in data:
                    out["display_truncated"] = data["display_truncated"]
                if "ag_stream_validation" in data:
                    out["ag_stream_validation"] = data["ag_stream_validation"]
            elif kind == "refusal_detected":
                for key in ("task_outcome", "reason", "refusal_reason", "reset_time", "resets_at", "message", "termination_confirmed"):
                    if key in data:
                        out[key] = data[key]
        return out

    @staticmethod
    def _stored_incomplete(inflight: sqlite3.Row) -> dict[str, Any] | None:
        saved: object = json.loads(inflight["result_json"]) if inflight["certainty"] == STARTED and inflight["result_json"] else None
        stored = cast("dict[str, Any]", saved) if isinstance(saved, dict) else {}
        return stored if stored.get("reason") in ("truncated", "incomplete") else None

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
            return digest(result)
        except (TypeError, ValueError) as e:
            raise InvalidTerminalResultError(f"terminal result is not strict JSON: {e}") from e

    def finalize_terminal(self, token: ClaimToken, result: Any, delivery_id: str) -> "BridgeLedger.FinalizeOutcome":
        """Callbacks are bound to the delivery_id they belong to (never inferred from 'newest')."""
        return self._finalize(token, delivery_id, result)

    def _finalize(self, token: ClaimToken, did: str, result: Any, provenance: dict[str, Any] | None = None) -> "BridgeLedger.FinalizeOutcome":
        # Validate the outer terminal payload before inspecting optional fields;
        # direct callers and malformed runtime callbacks may violate the type hint.
        if not isinstance(result, dict):
            self._validate_result(result)
        typed_result = cast(dict[str, Any], result)
        provider_candidate = typed_result.get("provider_telemetry")
        # Provider telemetry is internal evidence, not part of terminal truth,
        # result digests, replay snapshots, or public response metadata.
        supplied_result = {key: value for key, value in typed_result.items() if key != "provider_telemetry"}
        result = supplied_result
        dig = self._validate_result(result)  # before anything is committed
        conflict = False
        try:
            with self.claims.fenced(token) as conn:
                row = conn.execute("SELECT * FROM bridge_deliveries WHERE delivery_id=?", (did,)).fetchone()
                if row is None:
                    raise NoDeliveryError(did)
                self._check_scope(row, token)
                accepted = session_receipts.snapshot(row)
                if accepted is not None:
                    result = dict(result, session=accepted, error=None)
                    dig = self._validate_result(result)
                if row["certainty"] == TERMINAL:
                    if row["result_digest"] == dig:
                        outcome = "duplicate"
                        self._evidence(conn, row["stream_id"], row["peer_id"], "duplicate_terminal", did, TERMINAL, digest=dig)
                    else:
                        conflict, outcome = True, "conflict"
                        self._evidence(conn, row["stream_id"], row["peer_id"], "conflicting_terminal", did, TERMINAL,
                                       digest=dig, committed_digest=row["result_digest"], result=supplied_result)
                else:
                    stopped = self.claims.now()
                    conn.execute("UPDATE bridge_deliveries SET result_json=?, result_digest=?, terminal_at=? WHERE delivery_id=?",
                                 (json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False), dig,
                                  iso_timestamp(stopped), did))
                    native = self._commit_native_terminal(conn, token, row, result)
                    if native and provenance is not None:
                        native.update(provenance)
                        if provenance["effective_mode"] == "fresh":
                            conn.execute("UPDATE bridge_session_history SET bootstrap_truncated=? WHERE stream_id=? AND peer_id=? AND session_generation=?",
                                         (int(provenance["bootstrap_truncated"]), token.stream_id, token.peer_id, row["session_generation"]))
                    term_detail: dict[str, Any] = dict(digest=dig, **native)
                    for key in ("env_scrubbed", "ag_stream_validation"):
                        if key in result:
                            term_detail[key] = result[key]
                    if result.get("output_truncated"):
                        term_detail["output_truncated"] = True
                    if result.get("display_truncated"):
                        term_detail["display_truncated"] = True
                    self._transition_in(conn, did, TERMINAL, "terminal", token=token, **term_detail)  # illegal source state rolls the tx back
                    completed = conn.execute("SELECT * FROM bridge_deliveries WHERE delivery_id=?", (did,)).fetchone()
                    session_receipts.telemetry(conn, completed, self.claims.now(), provider_candidate)
                    delivery_projections.record_usage_coverage(conn, completed, self.claims.now())
                    ask_budgets.record_ask_pool_delta(conn, completed, stopped)
                    mapping = bridge_registry.generation(conn, token.stream_id, token.peer_id, row["session_generation"])
                    if mapping is not None and mapping["state"] not in ("LOST", "TOMBSTONED") and mapping["external_session_id"] == row["external_session_id"]:
                        conn.execute("UPDATE bridge_session_history SET last_seen=? WHERE session_handle=?", (self.claims.now(), mapping["session_handle"]))
                        bridge_registry.point(conn, mapping, self.claims.now())
                    outcome = "first"
        except StaleClaimError as e:
            self._late(token, did, "stale_terminal_callback", digest=dig, error=str(e))
            raise
        if conflict:
            raise TerminalConflictError(f"delivery {did} already has committed terminal truth; differing result rejected")
        self._fire("bridge.after_terminal_evidence_before_response_append")
        rid = self._materialize(token, did)
        return BridgeLedger.FinalizeOutcome(outcome, rid)

    def _commit_native_terminal(self, conn: sqlite3.Connection, token: ClaimToken,
                                delivery: sqlite3.Row, result: dict[str, Any]) -> dict[str, Any]:
        m = bridge_registry.generation(conn, token.stream_id, token.peer_id, delivery["session_generation"])
        usage = result.get("usage")
        detail: dict[str, Any] = {}
        raw_usage: dict[str, Any] | None = cast("dict[str, Any] | None", result.get("usage"))
        if (m is not None and m["runtime_kind"] in ("cc", "cx", "ag")
                and isinstance(raw_usage, dict)):
            detail.update(usage=raw_usage, usage_scope="session_cumulative", usage_attribution="unattributed")
        elif m is not None and m["runtime_kind"] in ("ca", "oa", "ga") and isinstance(raw_usage, dict):  # one API call per ask (#378)
            detail.update(usage=raw_usage, usage_scope="delivery", usage_attribution="delivery")
        if (m is not None and m["state"] not in ("LOST", "TOMBSTONED") and m["session_generation"] == delivery["session_generation"]
                    and m["external_session_id"] == delivery["external_session_id"]):
            vendor = result.get("vendor_session")
            vendor_id = cast("dict[str, Any]", vendor).get("id") if isinstance(vendor, dict) else None
            if not isinstance(vendor_id, str) or re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", vendor_id) is None:
                vendor_id = m["vendor_session_id"]
            if vendor_id is None:
                return detail
            watermark = max(m["context_watermark"] or 0, delivery["record_position"])
            won = conn.execute(
                "UPDATE bridge_session_history SET vendor_session_id=?, context_watermark=?, usage_json=?, state='ACTIVE' "
                "WHERE stream_id=? AND peer_id=? AND session_generation=? AND external_session_id=? "
                "AND state=? AND vendor_session_id IS ? AND context_watermark IS ? AND usage_json IS ?",
                (vendor_id, watermark, json.dumps(raw_usage) if raw_usage is not None else m["usage_json"],
                 token.stream_id, token.peer_id, m["session_generation"], m["external_session_id"], m["state"],
                 m["vendor_session_id"], m["context_watermark"], m["usage_json"])).rowcount
            if won:
                detail["session_bound"] = dict(
                    vendor_session_digest=digest(vendor_id) if vendor_id else None,
                    watermark=watermark, binding=m["binding"], adapter_fingerprint=m["adapter_fingerprint"],
                    claim_generation=token.generation, session_generation=m["session_generation"],
                    execution_id=delivery["execution_id"])
        if detail:
            detail.setdefault("usage", usage)
            if detail.get("usage") is not None:
                detail.setdefault("usage_scope", "session_cumulative")
                detail.setdefault("usage_attribution", "unattributed")
        return detail

    def _materialize(self, token: ClaimToken, did: str) -> str:
        """Idempotent: append the response Record (fenced, idempotency key), then ack the Offset; each step safe to repeat."""
        with self.claims.transaction() as conn:
            d = dict(conn.execute("SELECT * FROM bridge_deliveries WHERE delivery_id=?", (did,)).fetchone())
        self._check_scope(d, token)
        result = json.loads(d["result_json"])
        with self.claims.transaction() as conn:
            ev = conn.execute("SELECT detail FROM bridge_evidence WHERE delivery_id=? AND kind='terminal' ORDER BY seq LIMIT 1", (did,)).fetchone()
        terminal_detail: dict[str, Any] = json.loads(ev[0]) if ev else {}
        usage: Any = terminal_detail.get("usage", result.get("usage"))
        include_usage = usage is not None if "session_bound" in terminal_detail else "usage" in result
        resp_metadata: dict[str, Any] = {"delivery_id": did, "execution_id": d["execution_id"]}
        if include_usage:
            resp_metadata["usage"] = usage
            if "usage_scope" in terminal_detail:
                resp_metadata["usage_scope"] = terminal_detail.get("usage_scope", "session_cumulative")
                resp_metadata["usage_attribution"] = terminal_detail.get("usage_attribution", "unattributed")
        if result.get("output_truncated") or terminal_detail.get("output_truncated"):
            resp_metadata["output_truncated"] = True
        if result.get("display_truncated") or terminal_detail.get("display_truncated"):
            resp_metadata["display_truncated"] = True
        resp = self.store.append_record(
            guard=self.claims.guard(token), stream_id=d["stream_id"], author_peer_id=d["peer_id"], kind="response",
            body=result.get("response", result), reply_to=d["record_id"], created_at=d["terminal_at"],
            idempotency_key=f"bridge-response:{did}", metadata=resp_metadata)
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

    # ------------------------------------------------------------------ reconciliation (TD-26, CERT-002/003)

    @staticmethod
    def _retry_authorization(conn: sqlite3.Connection, delivery: str, certainty: str) -> sqlite3.Row | None:
        if certainty != MAY_HAVE_STARTED:
            return None
        return conn.execute("SELECT * FROM bridge_reconciliations WHERE delivery_id=? AND consumed_attempt IS NULL",
                            (delivery,)).fetchone()

    def reconcile_uncertain(self, record_id: str, reconciliation_record: Record) -> dict[str, Any]:
        """Register a durable control.reconcile RETRY for the uncertain delivery of `record_id`; idempotent, one authorization per delivery."""
        # authenticate against the PERSISTED Record only; the caller's object contributes nothing but its id
        with self.claims.transaction() as conn:
            pr = conn.execute("SELECT * FROM records WHERE record_id=?", (getattr(reconciliation_record, "record_id", None),)).fetchone()
        if pr is None:
            raise ReconcileRejectedError("reconciliation Record is not durable")
        if pr["kind"] != "control.reconcile":
            raise ReconcileRejectedError(f"persisted kind {pr['kind']!r} is not control.reconcile")
        body_raw: object = json.loads(pr["body_json"])
        body: dict[str, Any] = cast("dict[str, Any]", body_raw) if isinstance(body_raw, dict) else {}
        if body.get("decision") != "RETRY":
            raise ReconcileRejectedError(f"decision {body.get('decision')!r} is not authorized in M1 (only RETRY, TD-26)")
        rr = _PersistedReconcile(pr["record_id"], pr["stream_id"], pr["author_peer_id"])
        did = body.get("delivery_id")
        with self.claims.transaction() as conn:
            d = conn.execute("SELECT * FROM bridge_deliveries WHERE delivery_id=?", (did,)).fetchone()
            if d is None or d["record_id"] != record_id or d["stream_id"] != rr.stream_id:
                raise ReconcileRejectedError("reconcile does not reference a delivery of this Record in this Stream")
            if rr.author == d["peer_id"]:
                raise ReconcileRejectedError("the bridged peer cannot authorize its own retry")
            targets = json.loads(pr["targets_json"] or "[]")
            if targets and d["peer_id"] not in targets:
                raise ReconcileRejectedError("the reconcile Record does not address the delivery's peer")
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
