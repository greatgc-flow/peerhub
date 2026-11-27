"""Exercise actual writers and failure boundaries without a provider process."""
import pytest

from peerhub.extensions.ask import ask
from peerhub.extensions.runtime_contract import RuntimeTargetError
from peerhub.extensions.usage_normalization import PROVIDER_USAGE_CONTRACT, normalize_provider_usage
from tests.communication.bridge_helpers import bseed
from tests.communication.fakes.runtime import NativeRuntimeTarget
from tests.communication.fault.test_flt_bridge import CRASH_EXIT, crash, reconcile, restart_cycles
from tests.communication.harness.bridge import BridgeHarness
from tests.communication.helpers import req
from tests.communication.integration.test_activity_export import page


def usage(kind, counters):
    scope, source = PROVIDER_USAGE_CONTRACT[kind]
    return dict(schema_version=1, source=source, scope=scope, reported=counters,
                normalized=normalize_provider_usage(kind, counters), coverage={"state": "MEASURED", "reason": None})


def peer(kind):
    runtime = NativeRuntimeTarget(fingerprint=f"adapter:{kind}:v1", native=True)
    runtime.runtime_kind = kind
    return runtime


@pytest.mark.parametrize("kind", ("cc", "cx", "ag", "ca", "oa", "ga"))
def test_measured_provider_usage_is_preserved_once_on_request_replay(tmp_path, capsys, kind):
    db = tmp_path / "core.db"
    runtime = peer(kind)
    facts = usage(kind, {"output_tokens": 0, "input_tokens": 17})
    runtime.script_deliver(("started", "PRIVATE-EXECUTION"), ("terminal", {"response": "PRIVATE-REPLY",
                           "vendor_session": {"id": "PRIVATE-NATIVE-ID"}, "provider_telemetry": facts}))
    params = dict(db_path=db, peer_id=kind, prompt="PRIVATE-PROMPT", request_id="sample", runtime=runtime)
    first = ask(**params)
    ask(**params)
    assert runtime.count("deliver") == 1
    result = page(capsys, db, "show", first["requested_record_id"])
    receipt, = (e for e in result["items"] if e["kind"] == "peer.usage")
    assert receipt["usage"]["source"] == facts["source"]
    assert receipt["usage"]["reported"] == facts["reported"]
    assert receipt["delivery_measurement"]["output_tokens"] == 0


def test_failed_api_retains_attributable_counters_without_verifying_task(tmp_path, capsys):
    db = tmp_path / "core.db"
    runtime = peer("oa")
    def fail():
        raise RuntimeTargetError("PRIVATE-ERROR", provider_telemetry=usage("oa", {"output_tokens": 3}))
    runtime.script_deliver(("started", "exec"), ("call", fail))
    result = ask(db, "oa", "q", request_id="r", runtime=runtime)
    assert result["certainty"] == "STARTED"
    facts = page(capsys, db, "show", result["requested_record_id"])["items"]
    receipt, = (item for item in facts if item["kind"] == "peer.usage")
    assert receipt["delivery_measurement"]["output_tokens"] == 3
    assert not any(item.get("acked") or item.get("verified") for item in facts)


@pytest.mark.parametrize("seam,expected", [
    ("bridge.after_terminal_evidence_before_offset_ack", []),
    ("bridge.after_terminal_evidence_before_response_append", ["ai.response"]),
])
def test_checkpoint_across_recovery_does_not_replay(tmp_path, capsys, seam, expected):
    ws = tmp_path / "ws"
    h = BridgeHarness(ws)
    bseed(h)
    assert crash(ws, seam) == CRASH_EXIT
    first = page(capsys, h.db_path, "export")
    assert any(e.get("event") == "terminal" for e in first["items"])
    assert not any(e.get("acked") for e in first["items"])
    assert restart_cycles(ws)["calls"] == []
    next_page = page(capsys, h.db_path, "export", "--cursor", first["next_cursor"])
    assert [e["kind"] for e in next_page["items"] if e["kind"].startswith("ai.")] == expected
    assert sum(e.get("acked") is True for e in next_page["items"]) == 1


def test_registered_retry_links_distinct_attempts_and_feedback(tmp_path, capsys):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h, n=0)
    rec = h.append_record(req(kind="prompt", metadata={"session_selector": "latest"}))
    runtime = peer("custom").script_deliver(("disconnect",))
    initial = h.delivery_cycle("b", "s", runtime)
    assert initial.certainty == "MAY_HAVE_STARTED"
    auth = reconcile(h, initial.delivery_id)
    h.reconcile_uncertain(rec.record_id, auth)
    assert h.delivery_cycle("b", "s", runtime).certainty == "TERMINAL"
    events = page(capsys, h.db_path, "show", rec.record_id)["items"]
    assert {e["attempt"] for e in events if e.get("event") == "attempt_created"} == {1, 2}
    assert any(e.get("event") == "reconcile_registered" and e["reconcile_record_id"] == auth.record_id for e in events)


def test_cumulative_receipts_do_not_cross_counter_reset_or_new_generation(tmp_path, capsys):
    db = tmp_path / "db"
    runtime = peer("cx")
    model = "synthetic-a"
    runtime.session_binding = lambda: {"profile": "cx.standard", "model": model, "effort": "low"}
    receipts = []
    for index, tokens in enumerate((12, 19, 5, 11, 9)):
        if index == 4:
            model = "synthetic-b"
        runtime.script_deliver(("started", f"x{index}"), ("terminal", {"response": "ok", "vendor_session": {"id": "fixture"},
                               "provider_telemetry": usage("cx", {"input_tokens": tokens})}))
        result = ask(db, "cx", "q", stream_id="s", request_id=f"r{index}", runtime=runtime, fresh=index == 4)
        assert result["status"] == "delivered"
        events = page(capsys, db, "show", result["requested_record_id"])["items"]
        receipt, = (e for e in events if e["kind"] == "peer.usage")
        receipts.append(receipt)
    assert [r["delivery_measurement"]["input_tokens"] for r in receipts] == [12, 7, None, 6, 9]
    assert receipts[-1]["session_handle"] != receipts[-2]["session_handle"]
    assert receipts[1]["baseline_evidence_seq"] == int(receipts[0]["source_event_id"].split(":")[1])


def test_denied_empty_is_exposed_as_a_delivery_outcome(tmp_path, capsys):
    db = tmp_path / "db"
    runtime = peer("cc").script_deliver(("started", "x"), ("terminal", {"response": "", "empty_response": True,
            "denied_actions": ["PRIVATE-DENIED"], "vendor_session": {"id": "PRIVATE-SESSION"}}))
    receipt = ask(db, "cc", "q", request_id="sample", runtime=runtime)
    assert receipt["status"] == "denied_empty"
    events = page(capsys, db, "show", receipt["requested_record_id"])["items"]
    ack, = [item for item in events if item.get("acked")]
    assert ack["status"] == "denied_empty" and ack["certainty"] == "TERMINAL"
