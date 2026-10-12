"""Quota JSON consumes evaluated snapshots without deriving a second projection."""

import json

import pytest

from peerhub.extensions import diag_quota
from peerhub.extensions.diag import DiagnosticReport, Section
from peerhub.extensions.quota_projection import project_exhaustion

pytestmark = [pytest.mark.unit, pytest.mark.diag]
FIELDS = {"pace_pt", "projected_at_reset", "state", "exhaustion_at", "measured_at", "anchor_basis", "reason"}


@pytest.mark.parametrize("used,measured,frame,evidence,anchors,state", [
    (0.01, 79, 120, "MEASURED", True, "insufficient"),
    (0.4, 200, 200, "MEASURED", True, "threat"),
    (0.4, 200, 250, "MEASURED", True, "threat"),  # idle frame
    (0.4, 200, 500, "STALE", True, "stale"),
    (0.4, 200, 1000, "STALE", True, "expired"),
    (1.0, 1, 1, "MEASURED", True, "exhausted"),
    (1.0, 1, 301, "STALE", True, "stale"),
    (0.4, 200, 200, "MEASURED", False, "threat"),
    (None, 200, 200, "UNAVAILABLE", False, "unknown"),
])
def test_quota_json_projection_parity(monkeypatch, used, measured, frame, evidence, anchors, state):
    payload = {"window_started_at": 0, "resets_at": 1000,
               "anchor_basis": "provider_fixed_window"}
    if used is not None:
        payload["remaining_fraction"] = 1 - used
    item = {"kind": "quota", "state": evidence, "stored_state": "MEASURED",
            "subject_ref": "cx", "resource_pool_ref": "P", "source": "fixture",
            "observed_at": f"1970-01-01T00:{measured // 60:02d}:{measured % 60:02d}Z",
            "age_seconds": frame - measured, "payload": payload}
    report = DiagnosticReport("OK", frame, {}, {
        "observations": Section("observations", "OK", {"items": [item]}),
        "resource_pools": Section("resource_pools", "OK", {"pools": [
            {"resource_pool_id": "P", "kind": "QUOTA"},
            {"resource_pool_id": "empty", "kind": "QUOTA"},
        ]}),
    })

    def render(_self, sections, *, read_at):
        assert sections == ["resource_pools", "observations"]
        assert read_at == frame
        return report

    monkeypatch.setattr(diag_quota.ReadonlyDiag, "render", render)
    # An existing source file satisfies the existence guard; the reader is mocked.
    result = json.loads(json.dumps(diag_quota.quota_report(__file__, read_at=frame)))
    row = result["pools"][0]["items"][0]
    projection = row["projection"]
    assert set(projection) == FIELDS
    assert projection["state"] == state
    expected_used = None if used is None else 1 - payload["remaining_fraction"]
    assert projection == project_exhaustion(expected_used, 0, measured, 1000, frame,
                                            evidence_state=evidence, anchor_basis="provider_fixed_window")
    assert not FIELDS.intersection(row.keys() - {"state", "projection"})
    missing = result["pools"][1]["items"][0]["projection"]
    assert set(missing) == FIELDS
    assert missing["state"] == "unknown"
    assert all(missing[k] is None for k in ("pace_pt", "projected_at_reset", "exhaustion_at", "measured_at", "anchor_basis"))


def test_observed_time_is_not_reconstructed_from_capture_age():
    # Freshness may use captured_at; window-average math must use observed_at.
    item = {"state": "MEASURED", "observed_at": "1970-01-01T00:03:20Z", "age_seconds": 0,
            "payload": {"remaining_fraction": 0.6, "window_started_at": 0, "resets_at": 1000,
                        "anchor_basis": "provider_fixed_window"}}
    result = diag_quota.projection(item, 250)
    assert result["measured_at"] == 200
    assert result["pace_pt"] == 20
    item["observed_at"] = "invalid"
    assert diag_quota.projection(item, 250)["state"] == "unknown"
