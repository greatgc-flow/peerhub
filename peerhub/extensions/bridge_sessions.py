"""Retained session selection, mapping lifecycle, and context catch-up."""
from __future__ import annotations

import inspect
import json
import re
import sqlite3
import time
from abc import abstractmethod
from typing import Any, Callable, Iterator, Mapping, cast

from peerhub.core.models import Record, iso_timestamp
from peerhub.core.peer_kinds import BUILTIN_PEER_KINDS
from peerhub.core.store import CoreStore, IdempotencyConflictError
from peerhub.extensions import bridge_registry, session_receipts
from peerhub.extensions.bridge_claims import ClaimToken
from peerhub.extensions.bridge_control import NOT_STARTED
from peerhub.extensions.bridge_errors import (
    BoundaryConflictError, ConflictingSessionFlagsError, InvalidSessionHandleSyntaxError,
    SessionBindingMismatchError, SessionDeltaOverflowError, SessionError, SessionFingerprintMismatchError,
    SessionLostError, SessionNotFoundError, SessionPeerMismatchError, SessionStreamMismatchError,
    SessionTombstonedError, SessionUnavailableError, SessionWorkspaceMismatchError,
)
from peerhub.extensions.bridge_ledger import BridgeLedger, CycleResult, PersistedRecord
from peerhub.extensions.catchup import CatchUpBudget, CatchUpProjection, ProjectedRecords, build_catch_up, project_catch_up
from peerhub.extensions.runtime_contract import RuntimeTarget

DEFAULT_CATCH_UP_BYTES = 24_000
_RecordReader = Callable[[str, int, int], list[Record]]


def normalize_session_selector(*, fresh: bool = False, session: str | None = None) -> str:
    if fresh and session is not None:
        raise ConflictingSessionFlagsError("fresh and session are mutually exclusive")
    if session is not None:
        if not re.fullmatch(r"sess_[0-9a-f]{16}", session):
            raise InvalidSessionHandleSyntaxError("session must be a full handle: sess_ followed by 16 lowercase hexadecimal digits")
        return "sess:" + session
    return "fresh" if fresh else "latest"


class BridgeSessions(BridgeLedger):
    """Resolve and maintain session mappings for fenced deliveries."""

    max_session_attempts: int
    catch_up_budget: CatchUpBudget

    @abstractmethod
    def _gate(self, token: ClaimToken, runtime: RuntimeTarget, rec: Record, did: str, *, after_marker: bool = False) -> CycleResult | None: ...

    def current_mapping(self, peer_id: str, stream_id: str) -> dict[str, Any] | None:
        """Inspect the MRU mapping, falling back to its retained lifecycle history."""
        with self.claims.transaction() as conn:
            r = bridge_registry.current(conn, stream_id, peer_id)
            if r is None:
                r = conn.execute("SELECT * FROM bridge_session_history WHERE stream_id=? AND peer_id=? "
                                 "ORDER BY last_seen DESC,session_generation DESC,session_handle DESC LIMIT 1",
                                 (stream_id, peer_id)).fetchone()
        if r is None:
            return None
        d = dict(r)
        d["resumable"] = bool(d["resumable"])
        return d

    def session_events(self, peer_id: str, stream_id: str) -> list[dict[str, Any]]:
        with self.claims.transaction() as conn:
            rows = conn.execute("SELECT * FROM bridge_session_events WHERE stream_id=? AND peer_id=? ORDER BY seq",
                                (stream_id, peer_id)).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ session resolution

    def _session_event(self, conn: sqlite3.Connection, token: ClaimToken, event: str, frm: str | None, to: str, gen: int, **detail: Any) -> None:
        conn.execute("INSERT INTO bridge_session_events (stream_id, peer_id, event, from_state, to_state, generation, detail) "
                     "VALUES (?,?,?,?,?,?,?)", (token.stream_id, token.peer_id, event, frm, to, gen, json.dumps(detail, sort_keys=True)))

    @staticmethod
    def _public_reason(reason: str | None) -> str:
        return {None: "no_prior", "resume_rejected": "missing", "rejected": "missing", "unsupported": "not_resumable",
                "context_lost": "tainted", "continuity_unproven": "legacy_watermark_untrusted", "lost": "tainted"}.get(reason, reason or "no_prior")

    def _mapping_for_generation(self, peer: str, stream: str, gen: int | None) -> dict[str, Any] | None:
        with self.claims.transaction() as conn:
            row = bridge_registry.generation(conn, stream, peer, gen)
            return dict(row) if row is not None else None

    def _bind_delivery(self, conn: sqlite3.Connection, did: str, mapping: Mapping[str, Any], mode: str,
                       reason: str | None, prior: Mapping[str, Any] | None) -> None:
        row = conn.execute("SELECT * FROM bridge_deliveries WHERE delivery_id=?", (did,)).fetchone()
        accepted = session_receipts.snapshot(row)
        if accepted is None or accepted["handle"] != mapping["session_handle"]:
            accepted = dict(handle=mapping["session_handle"], generation=mapping["session_generation"], mode=mode,
                            reason=None if mode == "resumed" else self._public_reason(reason),
                            prior_handle=prior["session_handle"] if prior is not None else None,
                            telemetry=session_receipts.unknown_telemetry())
        conn.execute("UPDATE bridge_deliveries SET external_session_id=?,session_generation=?,session_handle=?,session_snapshot_json=? WHERE delivery_id=?",
                     (mapping["external_session_id"], mapping["session_generation"], mapping["session_handle"], session_receipts.dumps(accepted), did))

    @staticmethod
    def check_exact_session(mapping: dict[str, Any] | None, runtime: RuntimeTarget, peer: str, stream: str,
                            workspace_generation: str | None, now: float) -> None:
        if mapping is None:
            raise SessionNotFoundError("Requested session handle does not exist in this store.")
        if mapping["stream_id"] != stream:
            raise SessionStreamMismatchError("Requested session belongs to another Stream.")
        if mapping["peer_id"] != peer:
            raise SessionPeerMismatchError("Requested session belongs to another Peer.")
        if mapping["state"] == "TOMBSTONED":
            raise SessionTombstonedError("Requested session is expired or tombstoned.")
        if mapping["state"] == "LOST":
            raise SessionLostError("Requested session continuity is lost or tainted.")
        if not runtime.resumable or mapping["runtime_kind"] != runtime.runtime_kind or not bridge_registry.eligible(mapping, now):
            raise SessionUnavailableError("Requested session lacks compatible resume capability or trusted continuity.")
        if mapping["workspace_generation"] != workspace_generation:
            raise SessionWorkspaceMismatchError("Requested session belongs to another workspace lineage.")
        if mapping["adapter_fingerprint"] != runtime.fingerprint():
            raise SessionFingerprintMismatchError("Requested session adapter fingerprint differs.")
        if mapping["binding"] != runtime.binding():
            raise SessionBindingMismatchError("Requested session effective binding differs.")

    @staticmethod
    def _registry_prior(conn: sqlite3.Connection, stream: str, peer: str) -> dict[str, Any] | None:
        row = bridge_registry.current(conn, stream, peer)
        if row is None:
            row = conn.execute("SELECT * FROM bridge_session_history WHERE stream_id=? AND peer_id=? "
                               "ORDER BY last_seen DESC,session_generation DESC LIMIT 1", (stream, peer)).fetchone()
        return dict(row) if row is not None else None

    @staticmethod
    def _latest_candidate(rows: list[dict[str, Any]], prior: dict[str, Any] | None, runtime: RuntimeTarget,
                          workspace_generation: str | None, fp: str, bnd: str, now: float) -> dict[str, Any] | None:
        # The MRU pointer supplies tie-breaking without restricting compatible history search.
        if prior is not None:
            rows.sort(key=lambda row: row["session_handle"] != prior["session_handle"])
        return next((row for row in rows if runtime.resumable and bridge_registry.eligible(row, now)
                     and row["runtime_kind"] == runtime.runtime_kind and row["workspace_generation"] == workspace_generation
                     and row["adapter_fingerprint"] == fp and row["binding"] == bnd), None)

    @staticmethod
    def _delta_overflows(mapping: dict[str, Any], boundary: Record | PersistedRecord, *, exact: bool,
                         native_delta: Callable[[Mapping[str, Any], Record | PersistedRecord], CatchUpProjection],
                         local_delta: Callable[[Mapping[str, Any], Record | PersistedRecord], CatchUpProjection | None]) -> bool:
        native = mapping["vendor_session_id"] is not None
        if native and not exact and mapping["bootstrap_truncated"]:
            return True
        delta = native_delta(mapping, boundary) if native else local_delta(mapping, boundary)
        return bool((exact and mapping["bootstrap_truncated"]) or (delta is not None and delta.truncated))

    @staticmethod
    def preview_session(conn: sqlite3.Connection, tables: set[str], runtime: RuntimeTarget, peer: str, stream: str,
                        workspace_generation: str | None, selector: str, previous: sqlite3.Row | None) -> str | None:
        """Return the candidate handle using the same binding and context rules as dispatch."""
        if selector == "fresh":
            return None
        now = time.time()
        exact = selector.startswith("sess:")
        registered = "bridge_session_history" in tables
        retained = bridge_registry.ordered(conn, stream, peer) if registered else []
        removed = bridge_registry.retention_removals(retained, now)
        candidate = None
        if exact:
            row = conn.execute("SELECT * FROM bridge_session_history WHERE session_handle=?", (selector[5:],)).fetchone() if registered else None
            candidate = dict(row) if row is not None else None
            if candidate is not None and candidate["session_handle"] in removed:
                candidate["state"] = "TOMBSTONED"
            BridgeSessions.check_exact_session(candidate, runtime, peer, stream, workspace_generation, now)
        elif registered:
            prior = BridgeSessions._registry_prior(conn, stream, peer)
            rows = [dict(row) for row in retained if row["session_handle"] not in removed]
            candidate = BridgeSessions._latest_candidate(rows, prior, runtime, workspace_generation, runtime.fingerprint(), runtime.binding(), now)
        if candidate is None:
            return None
        position = previous["position"] if previous is not None else conn.execute(
            "SELECT COALESCE(MAX(position),0)+1 FROM records WHERE stream_id=?", (stream,)).fetchone()[0]
        boundary = PersistedRecord("", position)
        budget = CatchUpBudget(max_bytes=DEFAULT_CATCH_UP_BYTES)

        def read_records(stream_id: str, after: int, limit: int) -> list[Record]:
            return CoreStore.read_records_from(conn, stream_id, after, limit)

        if BridgeSessions._delta_overflows(candidate, boundary, exact=exact,
                                   native_delta=lambda m, r: BridgeSessions._project_native_delta(conn, budget, m, r),
                                   local_delta=lambda m, r: BridgeSessions._project_local_delta(
                                       read_records, budget, m, r, BridgeSessions._local_delta_evidence(conn, m, r.position))):
            if exact:
                raise SessionDeltaOverflowError("Requested session catch-up delta exceeds its context budget.")
            return None
        return candidate["session_handle"]

    def _validate_exact(self, token: ClaimToken, runtime: RuntimeTarget, handle: str,
                        boundary: Record | PersistedRecord) -> dict[str, Any]:
        # Commit expiry and pointer repair before raising a selector error.
        with self.claims.fenced(token) as conn:
            now = self.claims.now()
            bridge_registry.prune(conn, token.stream_id, token.peer_id, now)
            row = conn.execute("SELECT * FROM bridge_session_history WHERE session_handle=?", (handle,)).fetchone()
            mapping = dict(row) if row is not None else None
        self.check_exact_session(mapping, runtime, token.peer_id, token.stream_id, token.workspace_generation, now)
        assert mapping is not None
        if self._delta_overflows(mapping, boundary, exact=True, native_delta=self._native_delta, local_delta=self._local_history_delta):
            raise SessionDeltaOverflowError("Requested session catch-up delta exceeds its context budget.")
        return mapping

    def validate_selector_before_append(self, token: ClaimToken, runtime: RuntimeTarget, selector: str) -> None:
        """Preflight exact intent where no earlier execution can change its context."""
        if not selector.startswith("sess:"):
            return
        with self.claims.fenced(token) as conn:
            # Recovery/uncertainty takes precedence over selection. The queued
            # prompt is validated again after its own attempt is proven unstarted.
            if self.has_unsettled_delivery(conn, token.peer_id, token.stream_id):
                return
            position = conn.execute("SELECT COALESCE(MAX(position),0)+1 FROM records WHERE stream_id=?", (token.stream_id,)).fetchone()[0]
        self._validate_exact(token, runtime, selector[5:], PersistedRecord("", position))

    def _resolve_session(self, token: ClaimToken, runtime: RuntimeTarget, record: Record, did: str):
        selector = record.metadata.get("session_selector", "latest")
        exact = isinstance(selector, str) and selector.startswith("sess:")
        fp, bnd, now = runtime.fingerprint(), runtime.binding(), self.claims.now()
        with self.claims.fenced(token):
            if record.metadata.get("runtime_kind", runtime.runtime_kind) != runtime.runtime_kind:
                raise SessionUnavailableError("Queued request runtime kind differs from this runtime.")
            if record.metadata.get("workspace_generation", token.workspace_generation) != token.workspace_generation:
                raise SessionWorkspaceMismatchError("Queued request workspace lineage differs.")
            if record.metadata.get("adapter_fingerprint", fp) != fp:
                raise SessionFingerprintMismatchError("Queued request adapter fingerprint differs.")
            if record.metadata.get("binding", bnd) != bnd:
                raise SessionBindingMismatchError("Queued request effective binding differs from this runtime.")
        with self.claims.fenced(token) as conn:
            prior = self._registry_prior(conn, token.stream_id, token.peer_id)
            bridge_registry.prune(conn, token.stream_id, token.peer_id, now)
            rows = [dict(r) for r in bridge_registry.ordered(conn, token.stream_id, token.peer_id)]
        reason = None
        if prior is not None:
            if now-prior["last_seen"] > bridge_registry.MAX_AGE_S or prior["state"] == "TOMBSTONED":
                reason = "retention_max_age"
            elif not prior["resumable"]:
                reason = "not_resumable"
            elif prior["state"] == "LOST":
                reason = self._lost_reason(token, prior["session_generation"])
            elif prior["workspace_generation"] != token.workspace_generation:
                reason = "workspace_generation_change"
            elif prior["adapter_fingerprint"] != fp:
                reason = "fingerprint_change"
            elif prior["binding"] != bnd:
                reason = "binding_change"
            else:
                reason = "missing"
        if exact:
            candidate = self._validate_exact(token, runtime, selector[5:], record)
        elif selector == "fresh":
            candidate, reason = None, "explicit_fresh"
        else:
            candidate = self._latest_candidate(rows, prior, runtime, token.workspace_generation, fp, bnd, now)
        if candidate is not None:
            m = candidate
            native = m["vendor_session_id"] is not None
            overflow = not exact and self._delta_overflows(m, record, exact=False, native_delta=self._native_delta,
                                                           local_delta=self._local_history_delta)
            if overflow:
                reason = "delta_too_large"
            else:
                parameters: Mapping[str, inspect.Parameter]
                try:
                    parameters = inspect.signature(runtime.resume_session).parameters
                except (TypeError, ValueError):
                    parameters = {}
                accepts_vendor = "vendor_session_id" in parameters and parameters["vendor_session_id"].kind != inspect.Parameter.POSITIONAL_ONLY or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values())
                try:
                    outcome = runtime.resume_session(m["external_session_id"], vendor_session_id=m["vendor_session_id"]) if native and accepts_vendor else runtime.resume_session(m["external_session_id"])
                except SessionError:
                    outcome = "rejected"
                if outcome == "ok":
                    with self.claims.fenced(token) as conn:
                        won = conn.execute("UPDATE bridge_session_history SET state='ACTIVE' WHERE session_handle=? AND state=? AND vendor_session_id IS ? AND context_watermark IS ?",
                                           (m["session_handle"], m["state"], m["vendor_session_id"], m["context_watermark"])).rowcount
                        if won:
                            self._session_event(conn, token, "resume_ok", m["state"], "ACTIVE", m["session_generation"])
                            bridge_registry.point(conn, m, now)
                            self._bind_delivery(conn, did, m, "resumed", None, prior)
                    if won:
                        return m["external_session_id"], m["session_generation"]
                    reason = "tainted"
                else:
                    reason = "resume_rejected" if native else outcome
                    with self.claims.fenced(token) as conn:
                        lost = native or outcome == "missing"
                        if lost:
                            conn.execute("UPDATE bridge_session_history SET state='LOST',vendor_session_id=NULL WHERE session_handle=? AND state!='TOMBSTONED'", (m["session_handle"],))
                            bridge_registry.repair(conn, token.stream_id, token.peer_id, self.claims.now())
                        self._session_event(conn, token, "session_lost" if outcome == "missing" else "resume_rejected", m["state"], "LOST" if outcome == "missing" else "FRESH", m["session_generation"], reason=reason, outcome=outcome)
                if exact:
                    error_cls = SessionLostError if reason in ("tainted", "resume_rejected", "missing") else SessionUnavailableError
                    raise error_cls("Requested session could not be resumed; no substitute was allocated.")
        elif prior is not None:
            if not runtime.resumable and selector != "fresh":
                reason = "not_resumable"
            with self.claims.fenced(token) as conn:
                self._session_event(conn, token, reason or "missing", prior["state"], "FRESH", prior["session_generation"])
        if prior is not None:
            self._fire("bridge.after_resume_failure")
        for i in range(self.max_session_attempts):
            gated = self._gate(token, runtime, record, did)
            if gated is not None:
                return gated
            try:
                ext = runtime.create_session()
            except SessionError as exc:
                with self.claims.fenced(token) as conn:
                    self._session_event(conn, token, "create_failed", (prior or {}).get("state", "NONE"), (prior or {}).get("state", "NONE"),
                                        (prior or {}).get("session_generation", 0), attempt=i+1, error=str(exc))
                self._fire("bridge.after_create_failure")
                continue
            with self.claims.fenced(token) as conn:
                created = bridge_registry.allocate(conn, dict(stream_id=token.stream_id, peer_id=token.peer_id,
                    runtime_kind=runtime.runtime_kind, external_session_id=ext, adapter_fingerprint=fp, binding=bnd,
                    resumable=int(runtime.resumable), state="ACTIVE" if prior is None else "FRESH", workspace_generation=token.workspace_generation),
                    session_receipts.binding_attributes(runtime), now)
                gen = created["session_generation"]
                self._session_event(conn, token, "created" if prior is None else "fresh_generation", "NONE" if prior is None else "LOST" if reason == "missing" else prior["state"],
                                    created["state"], gen, reason=reason, at=iso_timestamp(now), session_handle=created["session_handle"])
                self._bind_delivery(conn, did, created, "fresh", reason, prior)
            return ext, gen
        return None

    def _lost_reason(self, token: ClaimToken, gen: int | None = None) -> str:
        m = self._mapping_for_generation(token.peer_id, token.stream_id, gen) if gen is not None else self.current_mapping(token.peer_id, token.stream_id)
        if m is not None and m["vendor_session_id"] is None and m["context_watermark"] is not None:
            return "tainted"
        for e in reversed(self.session_events(token.peer_id, token.stream_id)):
            if e["event"] == "session_lost" and (gen is None or e["generation"] == gen):
                return json.loads(e["detail"]).get("reason", "missing")
        return "missing"

    def _mark_lost(self, token: ClaimToken, reason: str) -> None:
        """Adapter reported context/session loss: the mapping is LOST; the next resolution creates a fresh generation."""
        inflight = self._inflight(token)
        m = (self._mapping_for_generation(token.peer_id, token.stream_id, inflight["session_generation"])
             if inflight is not None and inflight["session_generation"] is not None else self.current_mapping(token.peer_id, token.stream_id))
        if m is None or m["state"] in ("LOST", "TOMBSTONED") or (reason == "tainted" and m["vendor_session_id"] is None and m["runtime_kind"] not in BUILTIN_PEER_KINDS):
            return
        with self.claims.fenced(token) as conn:
            won = conn.execute("UPDATE bridge_session_history SET state='LOST', vendor_session_id=NULL WHERE stream_id=? AND peer_id=? "
                         "AND session_generation=? AND external_session_id=? AND vendor_session_id IS ? AND state=?",
                         (token.stream_id, token.peer_id, m["session_generation"], m["external_session_id"], m["vendor_session_id"], m["state"])).rowcount
            if not won:
                return
            bridge_registry.repair(conn, token.stream_id, token.peer_id, self.claims.now())
            self._session_event(conn, token, "session_lost", m["state"], "LOST", m["session_generation"], reason=reason)
            if m["vendor_session_id"] is not None:
                self._evidence(conn, token.stream_id, token.peer_id, "session_tainted", reason=reason,
                               session_generation=m["session_generation"], claim_generation=token.generation)

    # ------------------------------------------------------------------ context catch-up

    def _iter_records(self, stream: str, after_position: int, before_position: int, *, page: int = 200) -> Iterator[Record]:
        return self._read_record_range(self.store.read_records, stream, after_position, before_position, page)

    @staticmethod
    def _read_record_range(read_records: _RecordReader, stream: str, after_position: int,
                           before_position: int, page: int) -> Iterator[Record]:
        cursor = after_position
        while cursor < before_position:
            limit = min(page, before_position - cursor)
            batch = read_records(stream, cursor, limit)
            if not batch:
                return
            for r in batch:
                if r.position >= before_position:
                    return
                yield r
            cursor = batch[-1].position

    def _unseen_redirects(self, peer: str, stream: str, rec: Record | PersistedRecord, *, page: int = 200) -> list[Any]:
        """control.redirect Records addressed to this peer that the resumed session has not seen and no steer delivered."""
        with self.claims.transaction() as conn:
            last = conn.execute("SELECT COALESCE(MAX(record_position),0) FROM bridge_deliveries WHERE stream_id=? AND peer_id=? AND certainty != ?",
                                (stream, peer, NOT_STARTED)).fetchone()[0]
            done = {r[0] for r in conn.execute("SELECT record_id FROM bridge_controls WHERE stream_id=? AND peer_id=? AND outcome='done'",
                                               (stream, peer))}
        return [r for r in self._iter_records(stream, last, rec.position, page=page)
                if r.kind == "control.redirect" and r.author_peer_id != peer
                and (not r.targets or peer in r.targets) and r.record_id not in done]

    def _needs_catch_up(self, peer: str, stream: str, gen: int) -> bool:
        """Native continuity uses committed context; local sessions use delivery evidence."""
        m = self._mapping_for_generation(peer, stream, gen)
        if m is not None and m["session_generation"] == gen and m["vendor_session_id"] is not None and m["state"] == "ACTIVE":
            return False
        with self.claims.transaction() as conn:
            seen = conn.execute("SELECT 1 FROM bridge_deliveries WHERE stream_id=? AND peer_id=? AND session_generation=? AND certainty != ? LIMIT 1",
                                (stream, peer, gen, NOT_STARTED)).fetchone()
        return seen is None

    def _native_delta(self, mapping: Mapping[str, Any], rec: Record | PersistedRecord, *, page: int = 200) -> CatchUpProjection:
        with self.store.read_uow() as conn:
            return self._project_native_delta(conn, self.catch_up_budget, mapping, rec, page=page)

    @staticmethod
    def _project_native_delta(conn: sqlite3.Connection, budget: CatchUpBudget, mapping: Mapping[str, Any],
                              rec: Record | PersistedRecord, *, page: int = 200) -> CatchUpProjection:
        peer, stream = mapping["peer_id"], mapping["stream_id"]

        def read_records(stream_id: str, after: int, limit: int) -> list[Record]:
            return CoreStore.read_records_from(conn, stream_id, after, limit)

        candidates = (r for r in BridgeSessions._read_record_range(read_records, stream, mapping["context_watermark"], rec.position, page)
                      if (r.kind != "control.redirect" or (not r.targets or peer in r.targets))
                      and not BridgeSessions._native_reply_seen(conn, r, mapping))
        # Native continuity is all-or-nothing; count limits belong to fresh bootstrap only.
        return project_catch_up(candidates, CatchUpBudget(max_bytes=budget.max_bytes, max_tokens=budget.max_tokens,
                                                          token_estimator=budget.token_estimator),
                                after_position=mapping["context_watermark"], before_position=rec.position)

    def _local_history_delta(self, mapping: Mapping[str, Any], rec: Record | PersistedRecord, *, page: int = 200) -> CatchUpProjection | None:
        with self.claims.transaction() as conn:
            evidence = self._local_delta_evidence(conn, mapping, rec.position)
        return self._project_local_delta(self.store.read_records, self.catch_up_budget, mapping, rec, evidence, page=page)

    @staticmethod
    def _local_delta_evidence(conn: sqlite3.Connection, mapping: Mapping[str, Any], position: int) -> tuple[int, set[str]] | None:
        """Older local sessions use exact delivery evidence, not a native watermark.

        Preserve the ordinary local-session redirect contract. When another
        generation executed meanwhile, include the intervening context and only
        omit responses provably generated by this exact local session.
        """
        stream, peer, gen = mapping["stream_id"], mapping["peer_id"], mapping["session_generation"]
        after = conn.execute("SELECT MAX(record_position) FROM bridge_deliveries WHERE stream_id=? AND peer_id=? AND session_generation=? AND external_session_id=? AND certainty='TERMINAL'",
                             (stream, peer, gen, mapping["external_session_id"])).fetchone()[0]
        if after is None or not conn.execute("SELECT 1 FROM bridge_deliveries WHERE stream_id=? AND peer_id=? AND session_generation!=? AND record_position>? AND record_position<? AND certainty!='NOT_STARTED' LIMIT 1",
                                             (stream, peer, gen, after, position)).fetchone():
            return None
        represented = {r[0] for r in conn.execute("SELECT response_record_id FROM bridge_deliveries WHERE stream_id=? AND peer_id=? AND session_generation=? AND external_session_id=? AND certainty='TERMINAL'",
                                                 (stream, peer, gen, mapping["external_session_id"])) if r[0] is not None}
        return after, represented

    @staticmethod
    def _project_local_delta(read_records: _RecordReader, budget: CatchUpBudget, mapping: Mapping[str, Any],
                             rec: Record | PersistedRecord, evidence: tuple[int, set[str]] | None, *,
                             page: int = 200) -> CatchUpProjection | None:
        if evidence is None:
            return None
        after, represented = evidence
        stream, peer = mapping["stream_id"], mapping["peer_id"]
        candidates = (r for r in BridgeSessions._read_record_range(read_records, stream, after, rec.position, page)
                      if r.record_id not in represented and (r.kind != "control.redirect" or not r.targets or peer in r.targets))
        return project_catch_up(candidates, CatchUpBudget(max_bytes=budget.max_bytes, max_tokens=budget.max_tokens,
                                                          token_estimator=budget.token_estimator),
                                after_position=after, before_position=rec.position)

    @staticmethod
    def _redirect_pin(peer: str) -> Callable[[Any], bool]:
        return lambda r: r.kind == "control.redirect" and r.author_peer_id != peer and (not r.targets or peer in r.targets)

    def _fresh_info(self, peer: str, stream: str, gen: int) -> dict[str, Any]:
        for e in reversed(self.session_events(peer, stream)):
            if e["event"] == "fresh_generation" and e["generation"] == gen:
                return json.loads(e["detail"])
        return {}

    def _catch_up_for(self, token: ClaimToken, rec: Record, gen: int) -> list[Any]:
        peer, stream = token.peer_id, token.stream_id
        if not self._needs_catch_up(peer, stream, gen):
            mapping = self._mapping_for_generation(peer, stream, gen)
            native = mapping is not None and mapping["vendor_session_id"] is not None
            if native:
                assert mapping is not None
                proj = self._native_delta(mapping, rec)
            else:
                assert mapping is not None
                proj = self._local_history_delta(mapping, rec) or project_catch_up(self._unseen_redirects(peer, stream, rec), self.catch_up_budget, before_position=rec.position)
            out = ProjectedRecords(proj.records)
            out.boundary = {**proj.boundary(), "session_generation": gen, "mode": "resumed" if native else "resumed_unseen_redirects"}
            return out
        after = 0  # D-W4-8b: the Offset governs DELIVERY only; a fresh generation is bootstrapped from the Stream history (TD-12 budget)
        proj = build_catch_up(self.store, stream, self.catch_up_budget, after_position=after, before_position=rec.position,
                              pin=self._redirect_pin(peer))  # a fresh session knows no redirect intent: pin ALL addressed ones
        out = ProjectedRecords(proj.records)
        out.boundary = {**proj.boundary(), "session_generation": gen, "mode": "fresh_generation"}
        if gen > 1:
            self._write_boundary(token, gen, proj)
        return out

    @staticmethod
    def _boundary_payload_ok(body_in: object, peer: str, gen: int, reason: str | None, template: dict[str, Any]) -> bool:
        """D-W4-5b: the COMPLETE payload must be this generation's boundary: peer, generation, the recorded reason and a catch_up
        object with exactly the projection-metadata keys and sane types (window/counts may differ when a retry targets another Record)."""
        if not isinstance(body_in, dict):
            return False
        body = cast("dict[str, Any]", body_in)  # JSON object: keys are strings
        if set(body) != {"peer_id", "session_generation", "reason", "catch_up"}:
            return False
        if body["peer_id"] != peer or body["session_generation"] != gen or body["reason"] != reason:
            return False
        cu_in: object = body["catch_up"]
        if not isinstance(cu_in, dict):
            return False
        cu = cast("dict[str, Any]", cu_in)
        if set(cu) != set(template):
            return False
        ints = ("after_position", "candidates", "included_count", "omitted_count", "used_bytes", "used_tokens", "pinned_count")
        opt = ("before_position", "first_position", "last_position", "omitted_through_position")
        def isint(v: object) -> bool:
            return isinstance(v, int) and not isinstance(v, bool) and v >= 0

        return (all(isint(cu[k]) for k in ints) and all(cu[k] is None or isint(cu[k]) for k in opt)
                and isinstance(cu["truncated"], bool) and cu["truncated"] == (cu["omitted_count"] > 0)
                and cu["candidates"] == cu["included_count"] + cu["omitted_count"]
                and isinstance(cu["budget"], dict) and cu["budget"] == template["budget"])

    def _boundary_matches_history(self, peer: str, stream: str, gen: int, cu: dict[str, Any]) -> bool:
        """The claimed metadata must equal the projection recomputed from the durable Stream for the claimed delivered position."""
        bp = cu["before_position"]
        with self.claims.transaction() as conn:  # the Record the generation was FIRST used for (deterministic, survives retries for other Records)
            first = conn.execute("SELECT record_position FROM bridge_deliveries WHERE stream_id=? AND peer_id=? AND session_generation=? "
                                 "ORDER BY seq LIMIT 1", (stream, peer, gen)).fetchone()
        if bp is None or cu["after_position"] != 0 or first is None or first[0] != bp:
            return False
        at = self.store.read_records(stream, bp - 1, 1)
        if not at or at[0].position != bp:  # the delivered Record must really exist at that position
            return False
        return cu == build_catch_up(self.store, stream, self.catch_up_budget, after_position=0, before_position=bp,
                                    pin=self._redirect_pin(peer)).boundary()

    def _write_boundary(self, token: ClaimToken, gen: int, proj: CatchUpProjection) -> None:
        peer, stream = token.peer_id, token.stream_id
        key = f"context-boundary:{peer}:{gen}"
        info = self._fresh_info(peer, stream, gen)
        if info.get("reason") in ("tainted", "delta_too_large", "resume_rejected"):
            with self.claims.transaction() as conn:
                first = conn.execute("SELECT record_position FROM bridge_deliveries WHERE stream_id=? AND peer_id=? AND session_generation=? ORDER BY seq LIMIT 1",
                                     (stream, peer, gen)).fetchone()
            if first is not None:
                proj = build_catch_up(self.store, stream, self.catch_up_budget, after_position=0, before_position=first[0], pin=self._redirect_pin(peer))
        try:
            self.store.append_record(
                guard=self.claims.guard(token), stream_id=stream, author_peer_id=peer, kind="context.boundary",
                body={"peer_id": peer, "session_generation": gen, "reason": info.get("reason"), "catch_up": proj.boundary()},
                idempotency_key=key, created_at=info.get("at") or iso_timestamp(self.claims.now()))
        except IdempotencyConflictError:
            pass  # validated below: only THIS generation's boundary may occupy the key
        with self.claims.transaction() as conn:
            row = conn.execute("SELECT kind, body_json FROM records WHERE stream_id=? AND author_peer_id=? AND idempotency_key=?",
                               (stream, peer, key)).fetchone()
        body = json.loads(row["body_json"]) if row is not None and row["kind"] == "context.boundary" else None
        if not (body is not None and self._boundary_payload_ok(body, peer, gen, info.get("reason"), proj.boundary())
                and self._boundary_matches_history(peer, stream, gen, body["catch_up"])):
            raise BoundaryConflictError(f"idempotency key {key!r} is occupied by a Record that is not this generation's context.boundary")
