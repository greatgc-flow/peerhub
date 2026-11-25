"""Durable control intent, runtime effects, and cancellation for the session bridge."""
from __future__ import annotations

import json
import sqlite3
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, cast

from peerhub.core.models import Record
from peerhub.core.peer_kinds import BUILTIN_PEER_KINDS
from peerhub.core.store import CoreStore
from peerhub.extensions import bridge_registry
from peerhub.extensions.bridge_claims import ClaimStore, ClaimToken, StaleClaimError
from peerhub.extensions.bridge_errors import ControlRejectedError
from peerhub.extensions.runtime_contract import RuntimeTarget, RuntimeTargetError, SteerableTarget

NOT_STARTED, MAY_HAVE_STARTED, STARTED, TERMINAL = "NOT_STARTED", "MAY_HAVE_STARTED", "STARTED", "TERMINAL"
BUILTIN_PEER_MARKS = ",".join("?" for _ in BUILTIN_PEER_KINDS)
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
    detail: dict[str, Any] = field(default_factory=dict[str, Any])


class BridgeControl(ABC):
    """Control operations backed by the delivery bridge's store and ledger."""

    claims: ClaimStore
    store: CoreStore
    lease_sec: float

    @staticmethod
    @abstractmethod
    def _evidence(conn: sqlite3.Connection, stream_id: str, peer_id: str, kind: str, delivery_id: str | None = None,
                  certainty: str | None = None, **detail: Any) -> None: ...

    @abstractmethod
    def _fire(self, point: str) -> None: ...

    @abstractmethod
    def _record_by_id(self, stream_id: str, position: int, record_id: str) -> Record: ...

    @abstractmethod
    def _advance_to(self, token: ClaimToken, position: int) -> None: ...

    @staticmethod
    def _latest_pause_control(conn: sqlite3.Connection, peer: str, stream: str) -> sqlite3.Row | None:
        return conn.execute("SELECT kind, record_id, position FROM bridge_controls WHERE stream_id=? AND peer_id=? "
                            "AND kind IN ('control.pause','control.resume') ORDER BY position DESC LIMIT 1", (stream, peer)).fetchone()

    def control_state(self, peer_id: str, stream_id: str) -> dict[str, Any]:
        """Gating state derived from ACCEPTED control intent, ordered by Record position (never by handling order)."""
        with self.claims.transaction() as conn:
            last = self._latest_pause_control(conn, peer_id, stream_id)
            cancel = conn.execute("SELECT COALESCE(MAX(position),0) FROM bridge_controls WHERE stream_id=? AND peer_id=? "
                                  "AND kind='control.cancel'", (stream_id, peer_id)).fetchone()[0]
        paused = last is not None and last["kind"] == "control.pause"
        return {"paused": paused, "pause_record_id": last["record_id"] if last is not None and paused else None, "last_cancel_position": cancel}

    def handle_control(self, record_id: str, runtime: RuntimeTarget, peer_id: str | None = None) -> ControlResult:
        """Apply one durable control Record to `runtime` for the addressed peer. Authenticated ONLY against the persisted Record
        (kind, stream, author, targets); idempotent per (Record, peer): a replay or a concurrent duplicate never repeats the effect."""
        if not isinstance(cast(object, record_id), str):  # untyped caller boundary
            raise TypeError("handle_control takes a persisted record_id (str), never a caller copy of the Record")
        with self.claims.transaction() as conn:
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
            peer_id = cast(str, cands[0])
        if peer_id not in members:
            raise ControlRejectedError(f"peer {peer_id!r} is not a member of stream {pr['stream_id']!r}")
        if peer_id == pr["author_peer_id"]:
            raise ControlRejectedError("the bridged peer cannot issue control Records for its own delivery")
        if targets and peer_id not in targets:
            raise ControlRejectedError(f"control Record is not addressed to peer {peer_id!r}")
        return self._apply_control(dict(pr), peer_id, runtime)

    def _control_result(self, status: str, row: sqlite3.Row, outcome: str | None, **detail: Any) -> ControlResult:
        paused = self.control_state(row["peer_id"], row["stream_id"])["paused"]
        return ControlResult(status, row["record_id"], row["kind"], row["peer_id"], row["stream_id"], row["effect"], outcome, paused, detail)

    def _apply_control(self, pr: dict[str, Any], peer: str, runtime: RuntimeTarget, token: ClaimToken | None = None) -> ControlResult:
        kind, rid, stream = pr["kind"], pr["record_id"], pr["stream_id"]
        effect = CONTROL_EFFECT.get(kind, "none")
        lease_id = uuid.uuid4().hex
        now = self.claims.now()
        sel = "SELECT * FROM bridge_controls WHERE record_id=? AND peer_id=?"
        # 1. durable intent acceptance + lease + persisted target (all commit BEFORE any runtime effect, TD-05, D-W4-1)
        early: tuple[str, str | None, dict[str, Any]] | None = None
        attempts = 0
        with self.claims.transaction() as conn:
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
                if kind == "control.cancel":
                    target = conn.execute(sel, (rid, peer)).fetchone()
                    tainted = conn.execute("UPDATE bridge_session_history SET state='LOST', vendor_session_id=NULL WHERE stream_id=? AND peer_id=? "
                                 "AND state NOT IN ('LOST','TOMBSTONED') "
                                 f"AND (vendor_session_id IS NOT NULL OR runtime_kind IN ({BUILTIN_PEER_MARKS})) "
                                 "AND external_session_id=? AND session_generation=(SELECT session_generation FROM bridge_deliveries WHERE delivery_id=?) "
                                 "AND EXISTS (SELECT 1 FROM bridge_claims c WHERE c.stream_id=? AND c.peer_id=? "
                                 "AND c.generation=? AND c.workspace_generation=? AND c.expires_at>?)",
                                 (stream, peer, *BUILTIN_PEER_KINDS, target["target_session_id"], target["target_delivery_id"], stream, peer,
                                  target["target_claim_generation"], self.claims.workspace_generation(), now)).rowcount
                    if tainted:
                        bridge_registry.repair(conn, stream, peer, now)
                        self._evidence(conn, stream, peer, "session_tainted", target["target_delivery_id"],
                                       reason="control.cancel", control_record_id=rid, claim_generation=target["target_claim_generation"])
                conn.execute("UPDATE bridge_controls SET lease_id=?, lease_expires=?, attempts=? WHERE record_id=? AND peer_id=?",
                             (lease_id, now + self.lease_sec, attempts, rid, peer))
        if early is not None:
            return self._control_result(early[0], row, early[1], **early[2])
        self._fire("control.after_intent_commit")
        # 2. fence EXECUTION: current lease AND (when called from a delivery cycle) the current claim token AND a still-current target
        status, outcome, error = None, None, None
        with self.claims.transaction() as conn:
            row = conn.execute(sel, (rid, peer)).fetchone()
            if not (row["outcome"] is None and row["lease_id"] == lease_id and row["attempts"] == attempts
                    and self.claims.now() < row["lease_expires"]):
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
        term_conf: bool | None = None
        if outcome is None:
            try:
                if effect == "steer":
                    self._steer(runtime, row["target_session_id"], pr)
                else:
                    getattr(runtime, effect)(row["target_session_id"])
                outcome = "done"
                if effect == "terminate":
                    term_conf = True
            except RuntimeTargetError as e:
                outcome, error = "failed", str(e)
                if effect == "terminate":
                    term_conf = getattr(e, "termination_confirmed", False)
        self._fire("control.after_effect_before_outcome")
        # 3. fence COMPLETION: outcome is write-once; only the CURRENT, UNEXPIRED lease holder (and current token) may write it
        with self.claims.transaction() as conn:
            won = 0
            if token is None or self._token_current(conn, token):
                won = conn.execute("UPDATE bridge_controls SET outcome=?, error=?, delivery_id=? WHERE record_id=? AND peer_id=? "
                                   "AND outcome IS NULL AND lease_id=? AND attempts=? AND lease_expires > ?",
                                   (outcome, error, did, rid, peer, lease_id, attempts, self.claims.now())).rowcount
            if won:
                ev_detail: dict[str, Any] = dict(control_record_id=rid, control_kind=kind, effect=effect,
                                                 runtime_outcome=outcome, error=error)
                if term_conf is not None:
                    ev_detail["termination_confirmed"] = term_conf
                self._evidence(conn, stream, peer, "control_applied", did, None, **ev_detail)
            row = conn.execute(sel, (rid, peer)).fetchone()
        if not won:
            return self._control_result("superseded", row, None)
        ctrl_detail: dict[str, Any] = {"error": error} if error else {}
        if term_conf is not None:
            ctrl_detail["termination_confirmed"] = term_conf
        return self._control_result("applied", row, outcome, **ctrl_detail)

    def _token_current(self, conn: sqlite3.Connection, token: ClaimToken) -> bool:
        try:
            self.claims.check(conn, token)
            return True
        except StaleClaimError:
            return False

    def _steer(self, runtime: RuntimeTarget, ext: str, pr: dict[str, Any]) -> None:
        rec = self._record_by_id(pr["stream_id"], pr["position"], pr["record_id"])
        cast(SteerableTarget, runtime).steer(ext, rec)  # only reached after `supports_steer` was verified

    def _recover_stranded(self, token: ClaimToken, runtime: RuntimeTarget) -> None:
        """A control whose lease expired without an outcome is revisited regardless of the Offset (D-W4-3)."""
        with self.claims.transaction() as conn:
            rows = [dict(r) for r in conn.execute(
                "SELECT record_id, stream_id, position, kind FROM bridge_controls WHERE stream_id=? AND peer_id=? AND outcome IS NULL "
                "AND (lease_id IS NULL OR lease_expires <= ?) ORDER BY position", (token.stream_id, token.peer_id, self.claims.now()))]
        for r in rows:
            self._apply_control(r, token.peer_id, runtime, token)

    @staticmethod
    def _addresses_control(peer: str, author: str, kind: str, targets: list[str]) -> bool:
        return author != peer and is_control_kind(kind) and (not targets or peer in targets)

    def _handle_unread_controls(self, token: ClaimToken, runtime: RuntimeTarget) -> None:
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
                if self._addresses_control(peer, r.author_peer_id, r.kind, r.targets):
                    self._apply_control({"record_id": r.record_id, "stream_id": stream, "position": r.position, "kind": r.kind},
                                        peer, runtime, token)

    def _note_once(self, token: ClaimToken, did: str | None, kind: str, record_id: str, **detail: Any) -> None:
        with self.claims.fenced(token) as conn:
            for r in conn.execute("SELECT detail FROM bridge_evidence WHERE stream_id=? AND peer_id=? AND kind=?",
                                  (token.stream_id, token.peer_id, kind)):
                if json.loads(r[0]).get("record_id") == record_id:
                    return
            self._evidence(conn, token.stream_id, token.peer_id, kind, did, None, record_id=record_id, **detail)

    def _cancel_applies(self, token: ClaimToken, rec: Record, did: str) -> str | None:
        """A cancel accepted AFTER this attempt was created (and, for a reconcile retry, positioned after the RETRY Record) cancels
        this attempt before it runs. Earlier unread Records are NOT affected (D-W4-7). Deterministic from persisted rows."""
        with self.claims.transaction() as conn:
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
        with self.claims.transaction() as conn:
            pr = conn.execute("SELECT position FROM records WHERE record_id=?", (reconcile_record_id,)).fetchone()
            hit = conn.execute("SELECT record_id FROM bridge_controls WHERE stream_id=? AND peer_id=? AND kind='control.cancel' AND position>?",
                               (token.stream_id, token.peer_id, pr[0] if pr else 0)).fetchone()
        return hit[0] if hit else None

    def _cancel_attempt(self, token: ClaimToken, rec: Record, did: str, cancel_id: str) -> None:
        """Consume ONE cancelled in-flight attempt: evidence once, mark the attempt done, ack only this Record."""
        self._note_once(token, did, "cancelled_before_invoke", rec.record_id, cancel_record_id=cancel_id)
        self._fire("bridge.cancel_skip_before_ack")
        with self.claims.fenced(token) as conn:
            conn.execute("UPDATE bridge_deliveries SET acked=1 WHERE delivery_id=? AND certainty=?", (did, NOT_STARTED))
        self._advance_to(token, rec.position)
