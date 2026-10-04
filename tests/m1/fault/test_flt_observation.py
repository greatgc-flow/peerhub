"""Wave 6 FLT-010: probe failures stay ERROR/UNAVAILABLE evidence; no synthetic healthy zero (fake sources only)."""
import json

import pytest

from peerhub.extensions.observation_model import EvidenceState
from tests.m1.fakes.observation import FakeObservationSource, measured, unavailable

pytestmark = [pytest.mark.fault, pytest.mark.observation]
NUMERIC_KEYS = {"remaining", "remaining_fraction", "limit", "used", "requests_per_minute", "healthy", "latency_ms", "exhausted"}


@pytest.mark.parametrize("item,cond", [(TimeoutError("probe timed out"), "timeout"), ({"state": "MEASURED", "oops": 1}, "malformed_source_reading"),
                                       ("garbage", "malformed_source_reading")])
@pytest.mark.m1_id("FLT-010")
def test_flt_010_probe_error_remains_error_never_synthetic_healthy_zero(obs_h, item, cond):
    obs_h.create_peer({"peer_id": "p"})
    obs_h.capture("peer:p", "quota", FakeObservationSource(measured({"remaining_fraction": 0.7})))  # earlier healthy reading
    o = obs_h.capture("peer:p", "quota", FakeObservationSource(item))
    assert o.state in (EvidenceState.ERROR, EvidenceState.UNAVAILABLE) and o.payload["condition"] == cond
    rows = obs_h.obs_rows()
    assert len(rows) == 2 and rows[-1][6] == "ERROR"  # raw SQL: stored state is ERROR with evidence
    stored = json.loads(rows[-1][7])
    assert stored["condition"] == cond and not (set(stored) & NUMERIC_KEYS)  # nothing numeric fabricated (no 0, no 1.0, no copy of 0.7)
    latest = obs_h.latest("peer:p", "quota")
    assert latest.stored_state == EvidenceState.ERROR and latest.state != EvidenceState.MEASURED  # the old healthy value is not resurrected
    item_diag = obs_h.render_diag(sections=["observations"]).sections["observations"].data["items"][0]
    assert item_diag["state"] in ("ERROR", "UNAVAILABLE") and not (set(item_diag["payload"]) & NUMERIC_KEYS)


@pytest.mark.m1_id("FLT-010")
def test_flt_010_unavailable_source_and_positive_control(obs_h):
    obs_h.create_peer({"peer_id": "p"})
    o = obs_h.capture("peer:p", "quota", FakeObservationSource(unavailable("executable_not_found")))
    assert o.state == EvidenceState.UNAVAILABLE and not (set(o.payload) & NUMERIC_KEYS)
    good = obs_h.capture("peer:p", "quota", FakeObservationSource(measured({"remaining_fraction": 0.25})))  # same path can store real numbers
    assert good.state == EvidenceState.MEASURED and json.loads(obs_h.obs_rows()[-1][7]) == {"remaining_fraction": 0.25}
