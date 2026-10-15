"""Context sample coherence at the public monitor boundary."""
from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.diag, pytest.mark.platform_sensitive]
READ_AT = 2_000_000_000.0
ANCIENT_SAMPLE = READ_AT - 31_536_000  # One year old: clearly stale without defining an exact TTL.


def _context_from_public_frame(tmp_path, *, patch_detail=None, read_at: float = READ_AT):
    from tests.communication.bridge.test_retained_sessions import _ProviderTelemetryRuntime
    from tests.communication.bridge_helpers import bseed
    from tests.communication.harness.bridge import BridgeHarness
    from peerhub.extensions.monitor_read_model import render_monitor_frame

    harness = BridgeHarness(tmp_path / "workspace")
    bseed(harness)
    result = harness.delivery_cycle("b", "s", _ProviderTelemetryRuntime())
    with harness.cs.transaction() as conn:
        receipt = conn.execute(
            "SELECT seq,detail FROM bridge_evidence WHERE delivery_id=? AND kind='session_telemetry'",
            (result.delivery_id,),
        ).fetchone()
        detail = json.loads(receipt["detail"])
        if patch_detail is not None:
            patch_detail(detail)
            # These adversarial fixtures model corrupted/legacy persisted facts;
            # production receipts remain append-only.
            conn.execute("DROP TRIGGER bridge_evidence_no_update")
            conn.execute("UPDATE bridge_evidence SET detail=? WHERE seq=?",
                         (json.dumps(detail, separators=(",", ":")), receipt["seq"]))
    frame = render_monitor_frame(harness.db_path, ["context"], read_at=read_at)
    return frame["groups"]["context"]["data"]["sessions"][0]["context"]


def _candidate(detail: dict[str, object]) -> None:
    """Attach an explicit sample identity; current writer data has no such sample."""
    handle = str(detail["handle"])
    generation = int(detail["generation"])
    sample_at = 1_999_999_900.0
    detail.update({
        "configured_model": "claude-sonnet",
        "resolved_model": "claude-sonnet",
        "last_input_tokens": 80,
        "context_window": 100,
        "context_pct": 80.0,
        "source": "cc.context.v1",
        "observed_at": sample_at,
        "window_reported_at": sample_at,
        "context_sample": {
            "sample_id": "ctx-1",
            "handle": handle,
            "generation": generation,
            "model": "claude-sonnet",
            "source": "cc.context.v1",
            "observed_at": sample_at,
            "window_sample_id": "ctx-1",
        },
    })


@pytest.mark.parametrize(("name", "mutation"), [
    ("unsupported source", lambda d: (d.update(source="unknown.context.v99"),
                                        d["context_sample"].update(source="unknown.context.v99"))),
    ("stale sample", lambda d: (d.update(observed_at=ANCIENT_SAMPLE, window_reported_at=ANCIENT_SAMPLE),
                                 d["context_sample"].update(observed_at=ANCIENT_SAMPLE))),
    ("future sample", lambda d: (d.update(observed_at=READ_AT + 1, window_reported_at=READ_AT + 1),
                                  d["context_sample"].update(observed_at=READ_AT + 1))),
    ("malformed timestamp", lambda d: (d.update(observed_at="yesterday", window_reported_at="yesterday"),
                                        d["context_sample"].update(observed_at="yesterday"))),
    ("window from different sample", lambda d: d["context_sample"].update(window_sample_id="ctx-other")),
    ("model mismatch", lambda d: d["context_sample"].update(model="gpt-unrelated")),
    ("session mismatch", lambda d: d["context_sample"].update(handle="another-session")),
    ("generation mismatch", lambda d: d["context_sample"].update(generation=int(d["generation"]) + 1)),
    ("occupancy exceeds window", lambda d: d.update(last_input_tokens=101)),
    ("percentage inconsistent", lambda d: d.update(context_pct=79.0)),
])
def test_incoherent_context_is_unknown_with_a_reason(tmp_path, name, mutation):
    def patch(detail: dict[str, object]) -> None:
        _candidate(detail)
        mutation(detail)

    context = _context_from_public_frame(tmp_path, patch_detail=patch)
    assert context["occupancy_tokens"] is None, name
    assert context["window_tokens"] is None, name
    assert context["percent"] is None, name
    assert context["state"] == "UNKNOWN", name
    assert isinstance(context["reason"], str) and context["reason"], name


def test_writer_null_context_stays_unknown_with_reason_on_public_monitor_path(tmp_path):
    context = _context_from_public_frame(tmp_path)
    assert context["occupancy_tokens"] is None
    assert context["window_tokens"] is None
    assert context["percent"] is None
    assert context["state"] == "UNKNOWN"
    assert isinstance(context["reason"], str) and context["reason"]
