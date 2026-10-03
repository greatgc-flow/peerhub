"""Wave 5 OBS-013/014 (fake runtime only). Catalog tier e2e, kept in the default gate like BRG-015 (D-W3-1, D-W5-1)."""
import json
import sqlite3
from contextlib import closing

import pytest

from peerhub.extensions.observation import ObservationStore
from peerhub.extensions.observation_model import EvidenceState
from tests.m1.bridge_helpers import bseed, delivery_rows, offset_row, records, sql
from tests.m1.fakes import FakeRuntimeTarget
from tests.m1.fakes.observation import FakeObservationSource, measured
from tests.m1.harness.bridge import BridgeHarness
from tests.m1.harness.observation import CORE_TABLES

pytestmark = [pytest.mark.integration, pytest.mark.observation]


def _core(h):
    return {t: sql(h, f"SELECT * FROM {t} ORDER BY 1,2") for t in CORE_TABLES}


class ProviderQuotaProbe:
    """Source adapter reading the fake provider's account state; it never calls the runtime's delivery/session methods."""
    name = "provider-quota-probe"

    def __init__(self, reading):
        self.reading, self.calls = reading, 0

    def probe(self):
        self.calls += 1
        return self.reading


@pytest.mark.m1_id("OBS-013")
def test_obs_013_quota_exhaustion_leaves_core_and_peer_choice_unchanged(tmp_path):
    h = BridgeHarness(tmp_path / "ws")
    (rec,) = bseed(h, peers=("a", "b", "c"))
    obs = ObservationStore(h.db_path, clock=h.clock.now)
    rb, rc = FakeRuntimeTarget(), FakeRuntimeTarget()
    rb.script_deliver(("started", "x1"), ("runtime_error", "429 account quota exhausted"))
    res = h.delivery_cycle("b", "s", rb)
    assert res.status != "delivered"  # the bridge cycle itself hit the exhausted provider
    core_after_cycle, offset_b = _core(h), offset_row(h, "b")
    b_calls, deliveries = list(rb.calls), delivery_rows(h)
    probe = ProviderQuotaProbe(measured({"remaining_fraction": 0.0, "exhausted": True}, semantic="quota"))
    o = obs.capture("peer:b", "quota", probe, resource_pool_ref=None)
    assert o.state == EvidenceState.MEASURED and o.payload == {"remaining_fraction": 0.0, "exhausted": True}  # exhausted is recorded as measured zero
    assert probe.calls == 1
    # Core untouched by the observation; the runtime was not called again; no alternate peer got anything
    assert _core(h) == core_after_cycle and offset_row(h, "b") == offset_b
    assert rb.calls == b_calls and rc.calls == [] and delivery_rows(h) == deliveries
    # a follow-up cycle for the same peer does not reroute either: peer c's runtime is never involved, no c rows exist
    rb.script_deliver(("started", "x2"), ("terminal", {"response": "ok"}))
    h.delivery_cycle("b", "s", rb)
    assert rc.calls == [] and offset_row(h, "c") == (0, 1)
    with closing(sqlite3.connect(h.db_path)) as c:
        assert c.execute("SELECT COUNT(*) FROM bridge_deliveries WHERE peer_id <> 'b'").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM records WHERE author_peer_id = 'c'").fetchone()[0] == 0
    assert obs.latest("peer:b", "quota").state == EvidenceState.MEASURED


@pytest.mark.m1_id("OBS-014")
def test_obs_014_rate_limit_does_not_masquerade_as_quota(tmp_path):
    from tests.m1.harness.observation import ObservationHarness

    h = ObservationHarness(tmp_path / "ws")
    h.create_peer({"peer_id": "cx-01"})
    rl = {"requests_per_minute": 60, "retry_after_seconds": 7}
    h.capture("peer:cx-01", "rate_limit", FakeObservationSource(measured(rl, semantic="rate_limit")))
    report = h.render_diag(sections=["observations"])
    items = report.sections["observations"].data["items"]
    assert [(i["kind"], i["state"]) for i in items] == [("rate_limit", "MEASURED")]  # no quota entry synthesised from the rate limit
    assert items[0]["payload"] == rl
    h.capture("peer:cx-01", "quota", FakeObservationSource(measured({"remaining_fraction": 0.4}, semantic="quota")))
    items = {i["kind"]: i for i in h.render_diag(sections=["observations"]).sections["observations"].data["items"]}
    assert set(items) == {"rate_limit", "quota"}
    assert items["quota"]["payload"] == {"remaining_fraction": 0.4} and items["rate_limit"]["payload"] == rl  # distinct, uncontaminated
    assert not (set(items["quota"]["payload"]) & set(items["rate_limit"]["payload"]))
    assert sorted(r[4] for r in h.obs_rows()) == ["quota", "rate_limit"]
