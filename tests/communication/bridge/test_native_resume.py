"""Default native continuity, tested through durable Records and independent SQL oracles."""
import json

import pytest

from peerhub.extensions.ask import ask
from peerhub.extensions.bridge_claims import StaleClaimError
from peerhub.extensions.catchup import CatchUpBudget
from tests.communication.bridge_helpers import bseed, sql, downgrade_registry
from tests.communication.control_helpers import ctl
from tests.communication.fakes import CrashInjected, CrashInjector, FakeRuntimeTarget
from tests.communication.fakes.runtime import NativeRuntimeTarget
from tests.communication.harness.bridge import BridgeHarness
from tests.communication.helpers import req

pytestmark = [pytest.mark.integration, pytest.mark.bridge, pytest.mark.platform_sensitive]


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
    assert m["context_watermark"] == sql(h, "SELECT position FROM records WHERE record_id=?", (first.record_id,))[0][0]
    h.reopen()
    assert h.current_mapping("b", "s") == m
    add(h, "second")
    rt = NativeRuntimeTarget()
    result = h.delivery_cycle("b", "s", rt)
    assert result.session_generation == 1 and rt.vendor_ids == ["native-ext-1"]
    assert rt.count("create") == 0 and rt.catch_ups == [[first.response_record_id]]
    assert details(h, result) == {"effective_mode": "resumed", "fallback_reason": None, "injected_record_ids": [first.response_record_id]}
    evidence = sql(h, "SELECT detail FROM bridge_evidence WHERE kind='terminal'")
    assert evidence and all("native-ext-1" not in row[0] for row in evidence)


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
            conn.execute("DROP TRIGGER bridge_history_identity_immutable")
            conn.execute("UPDATE bridge_session_history SET workspace_generation='old-lineage'")
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
    first = h.delivery_cycle("b", "s", rt)
    redirect = ctl(h, "control.redirect", "redirect", body={"instruction": "re-read files"})
    other = ctl(h, "control.redirect", "other", targets=["a"], body={"instruction": "for a"})
    own = add(h, "own", author="b", kind="response")
    context = add(h, "context", kind="control.reconcile")
    current = add(h, "second")
    result = h.delivery_cycle("b", "s", rt)
    assert result.record_id == current.record_id
    assert rt.catch_ups[-1] == [first.response_record_id, redirect.record_id, own.record_id, context.record_id]
    assert other.record_id not in rt.catch_ups[-1]
    add(h, "third")
    h.delivery_cycle("b", "s", rt)
    assert rt.catch_ups[-1] == [result.response_record_id]


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
    assert sql(h, "SELECT state,vendor_session_id FROM bridge_session_history") == [("LOST", None)]
    assert sql(h, "SELECT session_handle,session_generation FROM bridge_sessions") == []
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
        assert sql(h, "SELECT state,vendor_session_id FROM bridge_session_history") == [("LOST", None)]
        assert sql(h, "SELECT session_handle,session_generation FROM bridge_sessions") == []
    rt.script_deliver(("started", "execution"), ("call", cancel), ("terminal", {"response": "late", "vendor_session": {"id": "should-not-bind"}}))
    assert h.delivery_cycle("b", "s", rt).status == "delivered"
    assert sql(h, "SELECT state,vendor_session_id FROM bridge_session_history") == [("LOST", None)]
    assert sql(h, "SELECT session_handle,session_generation FROM bridge_sessions") == []
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
    assert rt.calls == calls
    assert sql(h, "SELECT state,vendor_session_id FROM bridge_session_history") == [("LOST", None)]
    assert sql(h, "SELECT session_handle,session_generation FROM bridge_sessions") == []


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


@pytest.mark.parametrize("kind", ["cc", "cx", "ag"])
def test_cumulative_usage_is_unattributed_and_never_differenced(tmp_path, kind):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    rt = NativeRuntimeTarget()
    rt.runtime_kind = kind
    for key, raw in [(None, {"input_tokens": 100, "output_tokens": 20}),
                     ("second", {"input_tokens": 130, "output_tokens": 25}),
                     ("third", {"input_tokens": 5, "output_tokens": 1})]:
        if key:
            add(h, key)
        terminal(rt, raw)
        result = h.delivery_cycle("b", "s", rt)
        meta = json.loads(sql(h, "SELECT metadata_json FROM records WHERE record_id=?", (result.response_record_id,))[0][0])
        assert meta.get("usage") == raw
        assert meta["usage_scope"] == "session_cumulative"
        assert meta["usage_attribution"] == "unattributed"
        assert json.loads(h.current_mapping("b", "s")["usage_json"]) == raw


def test_ask_default_reuses_current_slot_and_exposes_provenance(tmp_path):
    db = tmp_path / "ask.db"
    first = ask(db, "cx", "first", runtime=NativeRuntimeTarget())
    assert first["effective_mode"] == "fresh" and first["fallback_reason"] is None
    second = ask(db, "cx", "second", runtime=NativeRuntimeTarget())
    assert second["effective_mode"] == "resumed" and second["injected_record_ids"] == [first["response_record_id"]]
    third = ask(db, "cx", "third", runtime=NativeRuntimeTarget(binding="other"))
    assert third["effective_mode"] == "fresh"


def test_legacy_rows_migrate_with_null_native_columns_idempotently(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    h.delivery_cycle("b", "s", FakeRuntimeTarget())
    with h.cs.transaction() as conn:
        downgrade_registry(conn)
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
def test_terminal_recovery_keeps_cumulative_usage_scope_and_watermark(tmp_path, point):
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
    (metadata,) = sql(h, "SELECT metadata_json FROM records WHERE record_id=?", (result.response_record_id,))[0]
    assert json.loads(metadata)["usage"] == {"input_tokens": 120}
    assert json.loads(metadata)["usage_scope"] == "session_cumulative"
    assert json.loads(metadata)["usage_attribution"] == "unattributed"
    assert h.current_mapping("b", "s")["context_watermark"] == sql(h, "SELECT record_position FROM bridge_deliveries WHERE delivery_id=?", (result.delivery_id,))[0][0]
    add(h, "third")
    terminal(rt, {"input_tokens": 125})
    third = h.delivery_cycle("b", "s", rt)
    meta = json.loads(sql(h, "SELECT metadata_json FROM records WHERE record_id=?", (third.response_record_id,))[0][0])
    assert meta["usage"] == {"input_tokens": 125}
    assert meta["usage_scope"] == "session_cumulative"
    assert meta["usage_attribution"] == "unattributed"


def test_generic_contract_keeps_local_resume_and_exact_evidence(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    rt = FakeRuntimeTarget()
    first = h.delivery_cycle("b", "s", rt)
    assert details(h, first) == {}
    assert sql(h, "SELECT kind FROM bridge_evidence WHERE delivery_id=? ORDER BY seq", (first.delivery_id,)) == [
        (kind,) for kind in ("attempt_created", "about_to_invoke", "started", "session_call", "terminal", "session_telemetry", "response_appended", "offset_acked")]
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


def test_concurrent_record_between_prompt_and_response_is_not_skipped(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    prompts = bseed(h, n=5)
    h.store.advance_offset_cas("b", "s", prompts[3].position, 1)
    rt = NativeRuntimeTarget()
    intervening = []
    rt.script_deliver(("started", "exec"),
                      ("call", lambda: intervening.append(add(h, "concurrent", author="b", kind="response"))),
                      ("terminal", {"response": "ok", "vendor_session": {"id": "native-1"}}))
    first = h.delivery_cycle("b", "s", rt)
    response_position = sql(h, "SELECT position FROM records WHERE record_id=?", (first.response_record_id,))[0][0]
    assert prompts[-1].position == 5 and intervening[0].position == 6 and response_position == 7
    assert h.current_mapping("b", "s")["context_watermark"] == 5
    h.reopen()
    add(h, "next")
    second = h.delivery_cycle("b", "s", rt)
    assert second.session_generation == first.session_generation
    assert rt.catch_ups[-1] == [intervening[0].record_id, first.response_record_id]


@pytest.mark.parametrize("kind", ["cc", "cx", "ag", "custom-native"])
@pytest.mark.parametrize("certainty", ["TERMINAL", "STARTED", "MAY_HAVE_STARTED"])
def test_old_native_mapping_upgrade_invalidates_unproven_context_without_replaying(tmp_path, kind, certainty):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    rt = NativeRuntimeTarget()
    rt.runtime_kind = kind
    first = h.delivery_cycle("b", "s", rt)
    if certainty != "TERMINAL":
        add(h, "uncertain")
        events = [("started", "exec")] if certainty == "STARTED" else []
        rt.script_deliver(*events, ("runtime_error", "disconnect"))
        assert h.delivery_cycle("b", "s", rt).status == "uncertain"
    # CLI capability was poisoned; a custom runtime's stored capability must stay false.
    # Simulate the pre-PR1 schema and response-position watermark.
    with h.cs.transaction() as conn:
        # The old single slot retained LOST mappings; reconstruct it after PR2 pointer deletion.
        conn.execute("INSERT OR IGNORE INTO bridge_sessions SELECT stream_id,peer_id,session_handle,session_generation,last_seen "
                     "FROM bridge_session_history WHERE session_handle=?", (first.session["handle"],))
        downgrade_registry(conn)
        conn.execute("ALTER TABLE bridge_sessions DROP COLUMN context_watermark_version")
        conn.execute("UPDATE bridge_sessions SET resumable=0, vendor_session_id='old-native', context_watermark=99")
    records = sql(h, "SELECT * FROM records ORDER BY position")
    deliveries = sql(h, "SELECT * FROM bridge_deliveries ORDER BY seq")
    evidence = sql(h, "SELECT * FROM bridge_evidence ORDER BY seq")
    h.reopen()
    m = h.current_mapping("b", "s")
    assert m["resumable"] == (0 if kind == "custom-native" else 1)
    assert m["state"] == "LOST" and m["vendor_session_id"] is None and m["context_watermark"] is None
    events = sql(h, "SELECT * FROM bridge_session_events ORDER BY seq")
    assert json.loads(events[-1][-1])["reason"] == "continuity_unproven"
    h.reopen()
    assert h.current_mapping("b", "s") == m
    assert sql(h, "SELECT * FROM bridge_session_events ORDER BY seq") == events
    assert sql(h, "SELECT * FROM records ORDER BY position") == records
    assert sql(h, "SELECT * FROM bridge_deliveries ORDER BY seq") == deliveries
    assert sql(h, "SELECT * FROM bridge_evidence ORDER BY seq") == evidence
    calls = list(rt.calls)
    if certainty == "TERMINAL":
        add(h, "next")
        second = h.delivery_cycle("b", "s", rt)
        assert second.session_generation == first.session_generation + 1
        expected_reason = "not_resumable" if kind == "custom-native" else "continuity_unproven"
        assert details(h, second)["fallback_reason"] == expected_reason
        assert rt.count("resume") == 0
    else:
        assert h.delivery_cycle("b", "s", rt).status == "blocked_uncertain"
        assert rt.calls == calls


@pytest.mark.parametrize("resumable", [False, True])
def test_upgrade_preserves_custom_local_capability_and_resolution(tmp_path, resumable):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    rt = FakeRuntimeTarget(resumable=resumable)
    first = h.delivery_cycle("b", "s", rt)
    with h.cs.transaction() as conn:
        downgrade_registry(conn)
        conn.execute("ALTER TABLE bridge_sessions DROP COLUMN context_watermark_version")
    h.reopen()
    assert h.current_mapping("b", "s")["resumable"] == int(resumable)
    assert h.current_mapping("b", "s")["state"] == "ACTIVE"
    add(h, "second")
    second = h.delivery_cycle("b", "s", rt)
    assert second.session_generation == (1 if resumable else 2)
    if resumable:
        assert rt.catch_ups[-1] == [] and rt.count("resume") == 1
    assert first.certainty == second.certainty == "TERMINAL"


@pytest.mark.parametrize("kind", ["cc", "cx", "ag"])
def test_upgrade_reconstructs_only_exact_consumed_prompt_evidence(tmp_path, kind):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    rt = NativeRuntimeTarget()
    rt.runtime_kind = kind
    first = h.delivery_cycle("b", "s", rt)
    with h.cs.transaction() as conn:
        downgrade_registry(conn)
        conn.execute("ALTER TABLE bridge_sessions DROP COLUMN context_watermark_version")
        conn.execute("UPDATE bridge_sessions SET resumable=0, context_watermark=(SELECT position FROM records WHERE record_id=?)",
                     (first.response_record_id,))
    h.reopen()
    m = h.current_mapping("b", "s")
    assert m["resumable"] == 1 and m["state"] == "ACTIVE"
    assert m["context_watermark"] == 1 and m["vendor_session_id"] == "native-ext-1"
    h.reopen()
    assert h.current_mapping("b", "s") == m
    add(h, "next")
    second = h.delivery_cycle("b", "s", rt)
    assert second.session_generation == 1 and rt.vendor_ids == ["native-ext-1"]
    assert rt.catch_ups[-1] == [first.response_record_id]


def test_upgrade_rejects_a_chain_that_skipped_intervening_context(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    rt = NativeRuntimeTarget()
    rt.runtime_kind = "cx"
    rt.script_deliver(("started", "exec"), ("call", lambda: add(h, "intervening", author="b", kind="response")),
                      ("terminal", {"response": "ok", "vendor_session": {"id": "native-ext-1"}}))
    first = h.delivery_cycle("b", "s", rt)
    # Simulate the old response-position watermark skipping the concurrent record.
    with h.cs.transaction() as conn:
        conn.execute("UPDATE bridge_session_history SET context_watermark=(SELECT position FROM records WHERE record_id=?)",
                     (first.response_record_id,))
    add(h, "next")
    h.delivery_cycle("b", "s", rt)
    assert rt.catch_ups[-1] == []
    with h.cs.transaction() as conn:
        downgrade_registry(conn)
        conn.execute("ALTER TABLE bridge_sessions DROP COLUMN context_watermark_version")
    h.reopen()
    assert h.current_mapping("b", "s")["state"] == "LOST"
    assert h.current_mapping("b", "s")["context_watermark"] is None
