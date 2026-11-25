"""Fenced delivery orchestration, including read-only dispatch preview."""
from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Any, Callable, cast

from peerhub.core.models import Record, json_digest
from peerhub.extensions import ask_budgets, session_receipts
from peerhub.extensions.bridge_claims import ClaimStore, ClaimToken, StaleClaimError
from peerhub.extensions.bridge_control import MAY_HAVE_STARTED, NOT_STARTED, STARTED, TERMINAL, is_control_kind
from peerhub.extensions.bridge_errors import InvocationMarkerError, NoDeliveryError, OffsetAckConflictError, SessionBridgeError
from peerhub.extensions.bridge_ledger import CycleResult
from peerhub.extensions.bridge_sessions import BridgeSessions
from peerhub.extensions.runtime_contract import ContextLostError, PrespawnError, RuntimeTarget, RuntimeTargetError


class BridgeDelivery(BridgeSessions):
    """Orchestrate dispatch and recovery through sessions and the delivery ledger."""

    owner_id: str
    _stream_budget: tuple[int, int] | None
    _on_stream_budget: Callable[[], None] | None

    @staticmethod
    def check_preview_claim(conn: sqlite3.Connection, tables: set[str], peer: str, stream: str,
                            workspace_generation: str | None) -> None:
        if "bridge_claims" in tables:
            row = ClaimStore.read_claim(conn, peer, stream)
            if row is not None:
                ClaimStore.check_available(row, peer, stream, None, workspace_generation, time.time())

    @staticmethod
    def preview_gate(conn: sqlite3.Connection, tables: set[str], peer: str, stream: str) -> CycleResult | None:
        """Read pre-dispatch pause and invocation state without applying controls or claiming work."""
        row = BridgeDelivery._read_inflight(conn, peer, stream) if "bridge_deliveries" in tables else None
        certainty = row["certainty"] if row is not None else None
        if row is not None and certainty == NOT_STARTED and {"bridge_invocations", "bridge_invocation_resolutions"} <= tables:
            if BridgeDelivery.open_invocation(conn, row["delivery_id"]) is not None:
                certainty = MAY_HAVE_STARTED
        if row is not None and (certainty == TERMINAL or BridgeDelivery._stored_incomplete(row)):
            return None  # dispatch settles an already recorded result before checking pause
        if row is not None and certainty == STARTED and "bridge_evidence" in tables:
            if BridgeDelivery._read_delivery_details(conn, row["delivery_id"]).get("task_outcome") == "NOT_EXECUTED":
                return None
        pause = BridgeDelivery._preview_pause(conn, tables, peer, stream)
        if pause is not None:
            return CycleResult("paused", row["record_id"] if row is not None else None,
                               row["delivery_id"] if row is not None else None, certainty, detail={"pause_record_id": pause})
        if row is not None and certainty in (STARTED, MAY_HAVE_STARTED):
            if "bridge_reconciliations" not in tables or BridgeDelivery._retry_authorization(conn, row["delivery_id"], certainty) is None:
                return CycleResult("blocked_uncertain", row["record_id"], row["delivery_id"], certainty)
        return None

    @staticmethod
    def _preview_pause(conn: sqlite3.Connection, tables: set[str], peer: str, stream: str) -> str | None:
        last = BridgeDelivery._latest_pause_control(conn, peer, stream) if "bridge_controls" in tables else None
        position = last["position"] if last is not None else 0
        pause = last["record_id"] if last is not None and last["kind"] == "control.pause" else None
        offset = conn.execute("SELECT read_through_position FROM offsets WHERE stream_id=? AND peer_id=?",
                              (stream, peer)).fetchone()
        for row in conn.execute("SELECT kind, record_id, author_peer_id, targets_json FROM records "
                                "WHERE stream_id=? AND position>? AND kind IN ('control.pause','control.resume') ORDER BY position",
                                (stream, max(position, offset[0] if offset is not None else 0))):
            if BridgeDelivery._addresses_control(peer, row["author_peer_id"], row["kind"], json.loads(row["targets_json"])):
                pause = row["record_id"] if row["kind"] == "control.pause" else None
        return pause

    # ------------------------------------------------------------------ delivery cycle

    def delivery_cycle(self, peer_id: str, stream_id: str, runtime: RuntimeTarget) -> CycleResult:
        token = self.claims.acquire(peer_id, stream_id, self.owner_id, self.lease_sec)
        self._fire("claim.after_acquire")
        return self.run_cycle(token, runtime)

    def run_cycle(self, token: ClaimToken, runtime: RuntimeTarget) -> CycleResult:
        try:
            result = self._run_cycle(token, runtime)
        except OffsetAckConflictError as e:  # never report delivered/acked; terminal truth stays, next cycle re-acks
            infl = self._inflight(token)
            result = CycleResult("ack_failed", infl["record_id"] if infl else None, infl["delivery_id"] if infl else None,
                               infl["certainty"] if infl else None, detail={"error": str(e)})
        except Exception as error:
            try:
                with self.claims.transaction() as conn:
                    self.claims.check(conn, token)
                    row = conn.execute("SELECT * FROM bridge_deliveries WHERE stream_id=? AND peer_id=? ORDER BY seq DESC LIMIT 1",
                                       (token.stream_id, token.peer_id)).fetchone()
                    if row is not None and not row["acked"]:
                        setattr(error, "session", session_receipts.snapshot(row))
            except Exception:
                pass  # unavailable/fenced evidence cannot justify an accepted identity
            raise
        if result.delivery_id is not None:
            with self.claims.transaction() as conn:
                row = conn.execute("SELECT * FROM bridge_deliveries WHERE delivery_id=?", (result.delivery_id,)).fetchone()
                result.session = session_receipts.snapshot(row) if row is not None else None
        return result

    def _run_cycle(self, token: ClaimToken, runtime: RuntimeTarget) -> CycleResult:
        peer, stream = token.peer_id, token.stream_id
        self._promote_unresolved(token)  # D-OWN-A4: recovery never re-invokes; a dangling invocation marker is MAY_HAVE_STARTED
        self._handle_unread_controls(token, runtime)  # durable intent first: before any session/runtime side effect
        infl = self._inflight(token)
        if infl is not None and infl["certainty"] == NOT_STARTED:
            rec = self._record_by_id(stream, infl["record_position"], infl["record_id"])
            if rec.metadata.get("binding", runtime.binding()) != runtime.binding():
                with self.claims.fenced(token) as conn:
                    failure = conn.execute("SELECT detail FROM bridge_evidence WHERE delivery_id=? AND kind='prespawn_failure' ORDER BY seq DESC LIMIT 1",
                                           (infl["delivery_id"],)).fetchone()
                if failure is not None:
                    # Preserve same-binding retries, but never rerun a proven pre-spawn
                    # failure under a different binding. The consumer rechecks certainty
                    # and unresolved invocation markers in its fenced transaction.
                    self._reject_selector(token, rec, infl["delivery_id"], dict(
                        code="delivery_failed", message=json.loads(failure["detail"]).get("error", "prespawn delivery failed")))
                    infl = None
        reconcile_of = None
        if infl is not None:
            rec = self._record_by_id(stream, infl["record_position"], infl["record_id"])
            if infl["certainty"] == TERMINAL:  # BRG-015: recognize persisted terminal truth; never re-run the runtime
                rid = self._materialize(token, infl["delivery_id"])
                return CycleResult("recovered_terminal", rec.record_id, infl["delivery_id"], TERMINAL, rid)
            refusal_detail = self.delivery_details(infl["delivery_id"])
            if infl["certainty"] == STARTED and refusal_detail.get("task_outcome") == "NOT_EXECUTED":
                return self._settle_refusal(token, rec, infl["delivery_id"], infl["session_generation"], refusal_detail)
            stored = self._stored_incomplete(infl)
            if stored:
                # API peers (#378): the incomplete reply is committed; finish its settlement, never invoke the provider again.
                detail: dict[str, Any] = {key: stored[key] for key in ("reason", "message", "partial_response", "usage") if key in stored}
                self._settle_without_response(token, rec, infl["delivery_id"], detail, str(detail["reason"]))
                return self._incomplete_result(rec, infl["delivery_id"], infl["session_generation"], detail)
            if infl["certainty"] in (STARTED, MAY_HAVE_STARTED):
                self._mark_lost(token, "tainted")
            st = self.control_state(peer, stream)
            if st["paused"]:
                return CycleResult("paused", rec.record_id, infl["delivery_id"], infl["certainty"], detail={"pause_record_id": st["pause_record_id"]})
            if infl["certainty"] != NOT_STARTED:
                with self.claims.transaction() as conn:
                    auth = self._retry_authorization(conn, infl["delivery_id"], infl["certainty"])
                if auth is None:
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
        execution_metadata = getattr(runtime, "execution_metadata", None)
        if callable(execution_metadata):
            metadata = cast("dict[str, Any]", execution_metadata())
            if metadata:
                self._note(token, did, "execution_metadata", NOT_STARTED, **metadata)
        if rec.kind == "prompt":
            selector = rec.metadata.get("session_selector")
            if selector not in ("latest", "fresh") and not (isinstance(selector, str) and re.fullmatch(r"sess:sess_[0-9a-f]{16}", selector)):
                return self._reject_selector(token, rec, did, dict(code="legacy_selector_unsupported", message="Request lacks supported session selector metadata."))
        self._fire("bridge.before_session_resolve")
        gated = self._gate(token, runtime, rec, did)
        if gated is not None:
            return gated
        try:
            sess = self._resolve_session(token, runtime, rec, did)
        except SessionBridgeError as exc:
            return self._reject_selector(token, rec, did, dict(code=exc.code, message=str(exc)))
        if isinstance(sess, CycleResult):
            return sess
        if sess is None:
            self._note(token, did, "session_unavailable", NOT_STARTED, attempts=self.max_session_attempts)
            return CycleResult("session_unavailable", rec.record_id, did, NOT_STARTED,
                               detail={"attempts": self.max_session_attempts})
        ext, gen = sess
        self._fire("bridge.after_session_resolved")
        catch_up = self._catch_up_for(token, rec, gen)
        self._fire("bridge.before_marker")
        gated = self._gate(token, runtime, rec, did)
        if gated is not None:
            return gated
        return self._run_runtime(token, runtime, rec, did, ext, gen, catch_up)

    def _reject_selector(self, token: ClaimToken, rec: Record, did: str, error: dict[str, str]) -> CycleResult:
        """Atomically consume only definitive unstarted request failures."""
        with self.claims.fenced(token) as conn:
            row = conn.execute("SELECT * FROM bridge_deliveries WHERE delivery_id=?", (did,)).fetchone()
            if row is None:
                raise NoDeliveryError(did)
            self._check_scope(row, token)
            if row["certainty"] != NOT_STARTED or self.open_invocation(conn, did) is not None:
                raise InvocationMarkerError("selector failure cannot be consumed after possible execution")
            result = dict(response=None, error=error, session=session_receipts.snapshot(row))
            if row["result_digest"] is None:
                conn.execute("UPDATE bridge_deliveries SET result_json=?,result_digest=? WHERE delivery_id=?",
                             (session_receipts.dumps(result), json_digest(result, strict=False), did))
                self._evidence(conn, token.stream_id, token.peer_id, "request_rejected", did, NOT_STARTED, **result)
            off = conn.execute("SELECT revision,read_through_position FROM offsets WHERE stream_id=? AND peer_id=?", (token.stream_id, token.peer_id)).fetchone()
            if off is None:
                conn.execute("INSERT INTO offsets(peer_id,stream_id,read_through_position,revision) VALUES (?,?,?,2)", (token.peer_id, token.stream_id, rec.position))
            elif off["read_through_position"] < rec.position:
                # Core CAS uses its own connection; use the same fenced writer transaction here.
                conn.execute("UPDATE offsets SET read_through_position=?,revision=revision+1 WHERE stream_id=? AND peer_id=? AND revision=?",
                             (rec.position, token.stream_id, token.peer_id, off["revision"]))
            conn.execute("UPDATE bridge_deliveries SET acked=1 WHERE delivery_id=?", (did,))
            self._evidence(conn, token.stream_id, token.peer_id, "offset_acked", did, NOT_STARTED, position=rec.position)
        return CycleResult("failed_not_started", rec.record_id, did, NOT_STARTED, detail=result)

    def _next_record(self, token: ClaimToken, runtime: RuntimeTarget, consumed: list[str]):
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

    def _run_runtime(self, token: ClaimToken, runtime: RuntimeTarget, rec: Record, did: str, ext: str, gen: int, catch_up: list[Any]) -> CycleResult:
        started = False
        terminal: Any = None
        have_terminal = False
        have_refusal = False
        refusal_payload: dict[str, Any] | None = None
        incomplete: dict[str, Any] | None = None
        last_termination_confirmed: bool | None = None
        # D-OWN-A4/TD-11: durable invocation marker BEFORE the runtime can possibly act; certainty stays NOT_STARTED while pending,
        # an unresolved marker after a crash is promoted to MAY_HAVE_STARTED by recovery (never re-invoked)
        quota_scope: Callable[[], list[str] | None] | None = getattr(runtime, "quota_pools", None)
        self._mark_invocation(token, did, external_session_id=ext, session_generation=gen,
                              quota_pools=quota_scope() if quota_scope is not None else None)
        self._fire("bridge.before_runtime_invoke")
        gated = self._gate(token, runtime, rec, did, after_marker=True)  # intent committed up to the very invoke still wins
        if gated is not None:
            return gated
        try:
            with self.claims.fenced(token) as conn:  # D-OWN-A4: the claim must still be current/unexpired at invocation
                warn_budget = self._stream_budget is not None and ask_budgets.resumed_ask_budget(conn, did, self._stream_budget)
        except StaleClaimError as e:  # lost: nothing was invoked; the unresolved marker is promoted conservatively by the new owner
            self._late(token, did, "fenced_before_invoke", error=str(e))
            return CycleResult("fenced", rec.record_id, did, NOT_STARTED, session_generation=gen)
        if warn_budget and self._on_stream_budget is not None:
            self._on_stream_budget()
        try:
            for ev in runtime.deliver(ext, rec, catch_up):
                try:
                    self.claims.heartbeat(token)
                except StaleClaimError as e:
                    self._late(token, did, "fenced_mid_delivery", error=str(e))
                    return CycleResult("fenced", rec.record_id, did, STARTED if started else NOT_STARTED, session_generation=gen)
                kind = ev[0]
                if kind == "started":
                    process = cast(dict[str, Any], ev[2]) if len(ev) > 2 and isinstance(ev[2], dict) else {}
                    with self.claims.fenced(token) as conn:
                        self._transition_in(conn, did, STARTED, "started", token=token, execution_id=ev[1],
                                            external_session_id=ext, session_generation=gen,
                                            **{key: process[key] for key in ("provider_pid", "provider_created") if key in process})
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
                elif kind == "refusal":
                    refusal_payload = cast("dict[str, Any]", ev[1]) if len(ev) > 1 and isinstance(ev[1], dict) else {}
                    if (not started or refusal_payload.get("outcome") != "NOT_EXECUTED"
                            or refusal_payload.get("reason") not in ("quota_exhausted", "usage_limit", "invalid_model", "auth_failure")):
                        raise RuntimeTargetError("invalid provider refusal evidence")
                    have_refusal = True
                    break
                elif kind == "incomplete":  # API peers (#378): the reply ended but is not complete; never a response Record
                    incomplete = cast("dict[str, Any]", ev[1]) if len(ev) > 1 and isinstance(ev[1], dict) else {}
                    if not started or incomplete.get("code") not in ("truncated", "incomplete"):
                        raise RuntimeTargetError("invalid incomplete reply evidence")
                    break
                else:  # timeout / disconnect / error: evidence only, certainty by durable start evidence
                    term_conf = ev[2] if len(ev) > 2 and (isinstance(ev[2], bool) or ev[2] is None) else None
                    if term_conf is not None:
                        last_termination_confirmed = term_conf
                        self._note(token, did, f"runtime_{kind}", STARTED if started else None, payload=list(ev[1:]),
                                   termination_confirmed=term_conf)
                    else:
                        self._note(token, did, f"runtime_{kind}", STARTED if started else None, payload=list(ev[1:]))
                    break
        except PrespawnError as e:
            term_conf = getattr(e, "termination_confirmed", None)
            detail = {"error": str(e)}
            if term_conf is not None:
                detail["termination_confirmed"] = term_conf
            if started:
                self._note(token, did, "runtime_error", STARTED, **detail)
                if isinstance(e, ContextLostError):
                    self._mark_lost(token, "context_lost")
                else:
                    self._mark_lost(token, "tainted")
                return CycleResult("uncertain", rec.record_id, did, STARTED, session_generation=gen, detail=detail)
            self._resolve_invocation(token, did, "prespawn_failure", str(e), "prespawn_failure")
            if isinstance(e, ContextLostError):  # explicit pre-run loss: safe retry on a fresh generation (catch-up, not blind replay)
                self._mark_lost(token, "context_lost")
            return CycleResult("failed_not_started", rec.record_id, did, NOT_STARTED, session_generation=gen, detail=detail)
        except RuntimeTargetError as e:
            term_conf = getattr(e, "termination_confirmed", None)
            detail = {"error": str(e)}
            if term_conf is not None:
                detail["termination_confirmed"] = term_conf
            if not started:
                self.transition(token, did, MAY_HAVE_STARTED, "start_uncertain", **detail)
                return CycleResult("uncertain", rec.record_id, did, MAY_HAVE_STARTED, session_generation=gen, detail=detail)
            self._note(token, did, "runtime_error", STARTED, **detail)
            self._mark_lost(token, "tainted")
            return CycleResult("uncertain", rec.record_id, did, STARTED, session_generation=gen, detail=detail)
        if have_refusal and refusal_payload is not None:
            reason = refusal_payload["reason"]
            reset_time = refusal_payload.get("reset_time")
            term_conf = refusal_payload.get("termination_confirmed")
            detail = {
                "task_outcome": "NOT_EXECUTED",
                "reason": reason,
                "refusal_reason": reason,
                "message": refusal_payload.get("message", ""),
            }
            if reset_time is not None:
                detail["reset_time"] = reset_time
                detail["resets_at"] = reset_time
            if term_conf is not None:
                detail["termination_confirmed"] = term_conf
            self._note(token, did, "refusal_detected", STARTED, **detail)
            self._fire("bridge.after_refusal_evidence")
            return self._settle_refusal(token, rec, did, gen, detail)
        if incomplete is not None:  # settled like a refusal (STARTED, acked, an error and no response); the text stays in the delivery
            detail = {"reason": incomplete["code"], "message": str(incomplete.get("message") or incomplete["code"]),
                      "partial_response": incomplete.get("response")}
            if isinstance(incomplete.get("usage"), dict):
                detail["usage"] = incomplete["usage"]
            self._settle_without_response(token, rec, did, detail, incomplete["code"], incomplete.get("provider_telemetry"))
            return self._incomplete_result(rec, did, gen, detail)
        if have_terminal:
            mode = getattr(catch_up, "boundary", {}).get("mode")
            provenance = dict(effective_mode="resumed" if mode == "resumed" else "fresh",
                              fallback_reason=None if mode == "resumed" else self._fresh_info(token.peer_id, token.stream_id, gen).get("reason"),
                              injected_record_ids=[r.record_id for r in catch_up],
                              bootstrap_truncated=getattr(catch_up, "boundary", {}).get("truncated", False))
            out = self._finalize(token, did, terminal, provenance)
            return CycleResult("delivered", rec.record_id, did, TERMINAL, out.response_record_id, gen)
        if not started:  # delivery was attempted but no start evidence either way
            self.transition(token, did, MAY_HAVE_STARTED, "start_uncertain")
            return CycleResult("uncertain", rec.record_id, did, MAY_HAVE_STARTED, session_generation=gen)
        no_term_detail: dict[str, Any] = {}
        if last_termination_confirmed is not None:
            no_term_detail["termination_confirmed"] = last_termination_confirmed
        self._note(token, did, "no_terminal", STARTED, **no_term_detail)
        self._mark_lost(token, "tainted")
        return CycleResult("uncertain", rec.record_id, did, STARTED, session_generation=gen, detail=no_term_detail)

    def _settle_refusal(self, token: ClaimToken, rec: Record, did: str, gen: int,
                        detail: dict[str, Any]) -> CycleResult:
        self._settle_without_response(token, rec, did, detail, "refused")
        return CycleResult("not_executed", rec.record_id, did, STARTED, session_generation=gen, detail=detail)

    @staticmethod
    def _incomplete_result(rec: Record, did: str, gen: int | None, detail: dict[str, Any]) -> CycleResult:
        """The result of an incomplete reply of an API peer (#378), fresh or recovered; `detail["reason"]` is its status."""
        # Two literal returns, not one computed status: the recovery inventory test (test_cli_recovery.py) collects the
        # statuses of this file from literal `CycleResult("...")` calls, so a computed one would drop out of its coverage.
        if detail["reason"] == "truncated":
            return CycleResult("truncated", rec.record_id, did, STARTED, session_generation=gen, detail=detail)
        return CycleResult("incomplete", rec.record_id, did, STARTED, session_generation=gen, detail=detail)

    def _settle_without_response(self, token: ClaimToken, rec: Record, did: str, detail: dict[str, Any], lost_reason: str,
                                 provider_candidate: object = None) -> None:
        """Settle a started delivery that has an error and no response: a provider refusal, or an incomplete reply of
        an API peer (#378, with its usage)."""
        with self.claims.fenced(token) as conn:
            row = conn.execute("SELECT * FROM bridge_deliveries WHERE delivery_id=?", (did,)).fetchone()
            if row["result_digest"] is None:
                refused = dict(response=None, session=session_receipts.snapshot(row), error=dict(code=detail["reason"], message=detail.get("message", detail["reason"])), **detail)
                conn.execute("UPDATE bridge_deliveries SET result_json=?,result_digest=? WHERE delivery_id=?",
                             (session_receipts.dumps(refused), json_digest(refused, strict=False), did))
                session_receipts.telemetry(conn, row, self.claims.now(), provider_candidate)
        # Durable refusal evidence authorizes recovery at each boundary. Keep the
        # delivery in flight until both session invalidation and offset commit.
        self._mark_lost(token, lost_reason)
        self._advance_to(token, rec.position)
        self._fire("bridge.after_refusal_offset_before_ack")
        with self.claims.fenced(token) as conn:
            if conn.execute("UPDATE bridge_deliveries SET acked=1 WHERE delivery_id=? AND acked=0", (did,)).rowcount:
                self._evidence(conn, token.stream_id, token.peer_id, "offset_acked", did, STARTED, position=rec.position)

    # ------------------------------------------------------------------ delivery gating

    def _gate(self, token: ClaimToken, runtime: RuntimeTarget, rec: Record, did: str, *, after_marker: bool = False) -> CycleResult | None:
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
        if cancel is None:  # not reachable (not paused and no cancel returned above); keeps the narrowing explicit
            return None
        self._cancel_attempt(token, rec, did, cancel)
        return CycleResult("cancelled", rec.record_id, did, NOT_STARTED, detail={"cancel_record_id": cancel})
