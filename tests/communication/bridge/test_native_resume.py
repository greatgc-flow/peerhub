"""Explicit native continuity, tested through durable Records and independent SQL oracles."""
import json

import pytest

from peerhub.extensions.ask import ask
from peerhub.extensions.bridge_claims import StaleClaimError
from peerhub.extensions.catchup import CatchUpBudget
from tests.communication.bridge_helpers import bseed, sql
from tests.communication.control_helpers import ctl
from tests.communication.fakes import CrashInjected, CrashInjector, FakeRuntimeTarget
from tests.communication.fakes.runtime import NativeRuntimeTarget
from tests.communication.harness.bridge import BridgeHarness
from tests.communication.helpers import req

pytestmark = [pytest.mark.integration, pytest.mark.bridge]


def add(h, key, **kw):
    return h.append_record(req(key=key, body=key, **kw))


def details(h, result):
    return h.bridge.delivery_details(result.delivery_id)


def boundary(h):
    return json.loads(sql(h, "SELECT body_json FROM records WHERE kind='context.boundary' ORDER BY position DESC LIMIT 1")[0][0])


def terminal(rt, usage):
    rt.script_deliver(("started", "execution"),
                      ("terminal", {"response": "ok", "vendor_session": {"id": "thread-1"}, "usage": usage}))


def test_restart_persists_mapping_and_watermark(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    first = h.delivery_cycle("b", "s", NativeRuntimeTarget())
    m = h.current_mapping("b", "s")
    assert m["vendor_session_id"] == "native-ext-1"
    assert m["context_watermark"] == sql(h, "SELECT position FROM records WHERE record_id=?", (first.response_record_id,))[0][0]
    h.reopen()
    assert h.current_mapping("b", "s") == m
    add(h, "second")
    rt = NativeRuntimeTarget()
    result = h.delivery_cycle("b", "s", rt)
    assert result.session_generation == 1 and rt.vendor_ids == ["native-ext-1"]
    assert rt.count("create") == 0 and rt.catch_ups == [[]]
    assert details(h, result) == {"effective_mode": "resumed", "fallback_reason": None, "injected_record_ids": []}
    evidence = sql(h, "SELECT detail FROM bridge_evidence WHERE kind='terminal'")
    assert evidence and all("native-ext-1" not in row[0] for row in evidence)


def test_opt_out_is_fresh_on_each_invocation(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    rt = NativeRuntimeTarget()
    h.delivery_cycle("b", "s", rt)
    rt.resume = False
    current = add(h, "second")
    expected = [r.record_id for r in h.store.read_records("s", 0, 100) if r.position < current.position]
    result = h.delivery_cycle("b", "s", rt)
    assert result.session_generation == 2 and rt.count("resume") == 1
    assert details(h, result)["effective_mode"] == "fresh"
    assert rt.catch_ups[-1] == expected


@pytest.mark.parametrize("change,reason", [("binding", "binding_change"), ("fingerprint", "fingerprint_change"),
                                         ("workspace", "workspace_generation_change"), ("missing", "resume_rejected"),
                                         ("rejected", "resume_rejected")])
def test_fallback_boundary(tmp_path, change, reason):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    rt = NativeRuntimeTarget()
    h.delivery_cycle("b", "s", rt)
    if change == "binding":
        rt.set_binding("new-binding")
    elif change == "fingerprint":
        rt.set_fingerprint("new-adapter")
    elif change == "workspace":
        with h.cs.transaction() as conn:
            conn.execute("UPDATE bridge_sessions SET workspace_generation='old-lineage'")
    elif change == "missing":
        rt.script_resume("missing")
    elif change == "rejected":
        rt.script_resume("rejected")
    add(h, "second")
    result = h.delivery_cycle("b", "s", rt)
    assert result.session_generation == 2
    assert details(h, result)["fallback_reason"] == reason and boundary(h)["reason"] == reason


def test_delta_contains_all_new_records_once_and_addressed_redirects(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    rt = NativeRuntimeTarget()
    h.delivery_cycle("b", "s", rt)
    redirect = ctl(h, "control.redirect", "redirect", body={"instruction": "re-read files"})
    other = ctl(h, "control.redirect", "other", targets=["a"], body={"instruction": "for a"})
    own = add(h, "own", author="b", kind="response")
    context = add(h, "context", kind="control.reconcile")
    current = add(h, "second")
    result = h.delivery_cycle("b", "s", rt)
    assert result.record_id == current.record_id
    assert rt.catch_ups[-1] == [redirect.record_id, context.record_id]
    assert other.record_id not in rt.catch_ups[-1] and own.record_id not in rt.catch_ups[-1]
    add(h, "third")
    h.delivery_cycle("b", "s", rt)
    assert rt.catch_ups[-1] == []


@pytest.mark.parametrize("truncated_bootstrap", [False, True])
def test_overflow_or_truncated_bootstrap_falls_back_without_partial_delta(tmp_path, truncated_bootstrap):
    h = BridgeHarness(tmp_path / "ws", catch_up_budget=CatchUpBudget(max_bytes=200))
    (first,) = bseed(h)
    if truncated_bootstrap:
        h.store.advance_offset_cas("b", "s", first.position, 1)
        add(h, "bootstrap")
        h.bridge.catch_up_budget = CatchUpBudget(max_records=0, max_bytes=200)
    rt = NativeRuntimeTarget()
    h.delivery_cycle("b", "s", rt)
    if not truncated_bootstrap:
        ctl(h, "control.redirect", "large", body={"instruction": "x" * 1000})
    add(h, "second")
    result = h.delivery_cycle("b", "s", rt)
    assert rt.count("resume") == 0 and result.session_generation == 2
    assert details(h, result)["fallback_reason"] == "delta_too_large"
    assert boundary(h)["reason"] == "delta_too_large" and rt.catch_up_meta[-1]["mode"] == "fresh_generation"


@pytest.mark.parametrize("started", [False, True])
def test_uncertain_native_turn_taints_and_never_replays(tmp_path, started):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    rt = NativeRuntimeTarget()
    h.delivery_cycle("b", "s", rt)
    add(h, "second")
    events = [("started", "execution")] if started else []
    rt.script_deliver(*events, ("runtime_error", "disconnected"))
    result = h.delivery_cycle("b", "s", rt)
    assert result.status == "uncertain"
    m = h.current_mapping("b", "s")
    assert m["state"] == "LOST" and m["vendor_session_id"] is None
    calls = list(rt.calls)
    assert h.delivery_cycle("b", "s", rt).status == "blocked_uncertain" and rt.calls == calls
    # A new authorized attempt resolves to a fresh generation; uncertainty is not replayed.
    token = h.acquire_claim("b", "s", h.bridge.owner_id)
    rec = add(h, "later")
    did = h.bridge.begin_attempt(token, rec)
    _, gen = h.bridge._resolve_session(token, rt, h.store.read_records("s", rec.position - 1, 1)[0], did)
    assert gen == 2 and h.bridge._fresh_info("b", "s", gen)["reason"] == "tainted"


def test_cancel_taints_even_when_terminate_is_unsupported(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    rt = NativeRuntimeTarget().capabilities(terminate=False)
    h.delivery_cycle("b", "s", rt)
    add(h, "second")
    def cancel():
        control = ctl(h, "control.cancel", "cancel")
        h.handle_control(control.record_id, rt, "b")
    rt.script_deliver(("started", "execution"), ("call", cancel), ("terminal", {"response": "late", "vendor_session": {"id": "should-not-bind"}}))
    assert h.delivery_cycle("b", "s", rt).status == "delivered"
    assert h.current_mapping("b", "s")["state"] == "LOST"
    assert h.current_mapping("b", "s")["vendor_session_id"] is None
    add(h, "third")
    result = h.delivery_cycle("b", "s", rt)
    assert details(h, result)["fallback_reason"] == "tainted"


@pytest.mark.parametrize("point", ["bridge.after_runtime_start", "bridge.before_runtime_invoke"])
def test_recovery_without_terminal_truth_taints(tmp_path, point):
    crash = CrashInjector()
    h = BridgeHarness(tmp_path / "ws", fault_hook=crash)
    bseed(h)
    rt = NativeRuntimeTarget()
    h.delivery_cycle("b", "s", rt)
    add(h, "second")
    crash.arm(point)
    with pytest.raises(CrashInjected):
        h.delivery_cycle("b", "s", rt)
    h.reopen()
    calls = list(rt.calls)
    assert h.delivery_cycle("b", "s", rt).status == "blocked_uncertain"
    assert rt.calls == calls and h.current_mapping("b", "s")["state"] == "LOST"
    assert h.current_mapping("b", "s")["vendor_session_id"] is None


def test_stale_terminal_owner_cannot_bind_native_mapping(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (rec,) = bseed(h)
    token = h.acquire_claim("b", "s", "old", lease_sec=1)
    did = h.bridge.begin_attempt(token, rec)
    runtime = NativeRuntimeTarget()
    ext, gen = h.bridge._resolve_session(token, runtime, h.store.read_records("s", 0, 1)[0], did)
    with h.cs.fenced(token) as conn:
        conn.execute("UPDATE bridge_deliveries SET external_session_id=?, session_generation=? WHERE delivery_id=?", (ext, gen, did))
    h.bridge.transition(token, did, "STARTED")
    before = h.current_mapping("b", "s")
    h.clock.advance(2)
    h.acquire_claim("b", "s", "new")
    with pytest.raises(StaleClaimError):
        h.finalize_terminal(token, {"response": "late", "vendor_session": {"id": "stale"}}, did)
    assert h.current_mapping("b", "s") == before
    assert sql(h, "SELECT kind FROM bridge_evidence WHERE kind='stale_terminal_callback'")


def test_fresh_projection_remains_bounded(tmp_path):
    h = BridgeHarness(tmp_path / "ws", catch_up_budget=CatchUpBudget(max_records=1))
    recs = bseed(h, n=3)
    h.store.advance_offset_cas("b", "s", recs[1].position, 1)
    rt = NativeRuntimeTarget()
    result = h.delivery_cycle("b", "s", rt)
    assert rt.catch_ups == [[recs[1].record_id]]
    assert rt.catch_up_meta[0]["truncated"] is True
    assert details(h, result)["effective_mode"] == "fresh"


def test_cx_cumulative_usage_reports_delta_and_omits_decreased_counters(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    rt = NativeRuntimeTarget()
    rt.runtime_kind = "cx"
    for key, raw, expected in [(None, {"input_tokens": 100, "output_tokens": 20}, {"input_tokens": 100, "output_tokens": 20}),
                               ("second", {"input_tokens": 130, "output_tokens": 25}, {"input_tokens": 30, "output_tokens": 5}),
                               ("third", {"input_tokens": 5, "output_tokens": 1}, None)]:
        if key:
            add(h, key)
        terminal(rt, raw)
        result = h.delivery_cycle("b", "s", rt)
        meta = json.loads(sql(h, "SELECT metadata_json FROM records WHERE record_id=?", (result.response_record_id,))[0][0])
        assert meta.get("usage") == expected
        assert json.loads(h.current_mapping("b", "s")["usage_json"]) == raw


def test_ask_resume_is_per_invocation_and_exposes_provenance(tmp_path):
    db = tmp_path / "ask.db"
    first = ask(db, "cx", "first", runtime=NativeRuntimeTarget())
    assert first["effective_mode"] == "fresh" and first["fallback_reason"] is None
    second = ask(db, "cx", "second", runtime=NativeRuntimeTarget())
    assert second["effective_mode"] == "resumed" and second["injected_record_ids"] == []
    third = ask(db, "cx", "third", runtime=NativeRuntimeTarget(resume=False))
    assert third["effective_mode"] == "fresh"


def test_legacy_rows_migrate_with_null_native_columns_idempotently(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    h.delivery_cycle("b", "s", FakeRuntimeTarget())
    with h.cs.transaction() as conn:
        for column in ("vendor_session_id", "context_watermark", "bootstrap_truncated", "usage_json"):
            conn.execute(f"ALTER TABLE bridge_sessions DROP COLUMN {column}")
    h.reopen()
    h.reopen()
    m = h.current_mapping("b", "s")
    assert m["vendor_session_id"] is None and m["context_watermark"] is None
    add(h, "second")
    result = h.delivery_cycle("b", "s", NativeRuntimeTarget())
    assert details(h, result)["fallback_reason"] == "unsupported"


@pytest.mark.parametrize("point", ["bridge.after_terminal_evidence_before_response_append",
                                   "bridge.after_terminal_evidence_before_offset_ack"])
def test_terminal_recovery_keeps_usage_delta_and_watermark(tmp_path, point):
    crash = CrashInjector()
    h = BridgeHarness(tmp_path / "ws", fault_hook=crash)
    bseed(h)
    rt = NativeRuntimeTarget()
    rt.runtime_kind = "cx"
    terminal(rt, {"input_tokens": 100})
    h.delivery_cycle("b", "s", rt)
    add(h, "second")
    terminal(rt, {"input_tokens": 120})
    crash.arm(point)
    with pytest.raises(CrashInjected):
        h.delivery_cycle("b", "s", rt)
    crash.disarm()
    h.reopen()
    calls = list(rt.calls)
    result = h.delivery_cycle("b", "s", rt)
    assert result.status == "recovered_terminal" and rt.calls == calls
    metadata, position = sql(h, "SELECT metadata_json, position FROM records WHERE record_id=?", (result.response_record_id,))[0]
    assert json.loads(metadata)["usage"] == {"input_tokens": 20}
    assert h.current_mapping("b", "s")["context_watermark"] == position
    add(h, "third")
    terminal(rt, {"input_tokens": 125})
    third = h.delivery_cycle("b", "s", rt)
    meta = json.loads(sql(h, "SELECT metadata_json FROM records WHERE record_id=?", (third.response_record_id,))[0][0])
    assert meta["usage"] == {"input_tokens": 5}


def test_generic_contract_keeps_local_resume_and_exact_evidence(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    rt = FakeRuntimeTarget()
    first = h.delivery_cycle("b", "s", rt)
    assert details(h, first) == {}
    assert sql(h, "SELECT kind FROM bridge_evidence WHERE delivery_id=? ORDER BY seq", (first.delivery_id,)) == [
        (kind,) for kind in ("attempt_created", "about_to_invoke", "started", "terminal", "response_appended", "offset_acked")]
    m = h.current_mapping("b", "s")
    assert all(m[key] is None for key in ("vendor_session_id", "context_watermark", "bootstrap_truncated", "usage_json"))
    add(h, "second")
    second = h.delivery_cycle("b", "s", rt)
    assert second.session_generation == 1 and rt.catch_ups[-1] == []
    assert sql(h, "SELECT record_id FROM records WHERE kind='context.boundary'") == []


def test_native_mapping_supports_legacy_one_argument_resume(tmp_path):
    class LegacyNative(NativeRuntimeTarget):
        def resume_session(self, external_session_id):
            self.calls.append(("resume", external_session_id))
            return "ok"

    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    h.delivery_cycle("b", "s", NativeRuntimeTarget())
    add(h, "second")
    rt = LegacyNative()
    result = h.delivery_cycle("b", "s", rt)
    assert result.session_generation == 1 and rt.count("resume") == 1
    assert details(h, result)["effective_mode"] == "resumed"
