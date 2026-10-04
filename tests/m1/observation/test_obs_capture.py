"""Wave 5 OBS-001..004, OBS-012: capture semantics (honesty, quota vs rate limit, probe isolation). Oracles: literals + raw SQL."""
import json
import subprocess
import sys

import pytest

from peerhub.extensions.observation_model import (EvidenceState, InvalidObservationError, KindSemanticError, Observation,
                                                  SourceReading)
from tests.m1.fakes import CrashInjected, CrashInjector
from tests.m1.fakes.observation import FakeObservationSource, absent, measured, unavailable, unknown
from tests.m1.harness.observation import CORE_TABLES, T0, ObservationHarness
from tests.m1.spec import ROOT

pytestmark = [pytest.mark.unit, pytest.mark.observation]

POOL = {"schema_version": "1.0", "resource_pool_id": "pool:openai-account-X", "provider": "openai", "kind": "ACCOUNT"}


def _seed(h):
    for p in ("cx-01", "cx-02"):
        h.create_peer({"peer_id": p})
    h.register_pool(POOL)


@pytest.mark.m1_id("OBS-001")
def test_obs_001_measured_quota_uses_quota_kind_and_shared_pool(obs_h):
    _seed(obs_h)
    src = FakeObservationSource(measured({"remaining_fraction": 0.25, "unit": "requests"}, semantic="quota"))
    obs = obs_h.capture("peer:cx-01", "quota", src, resource_pool_ref="pool:openai-account-X")
    assert (obs.kind, obs.state, obs.resource_pool_ref) == ("quota", EvidenceState.MEASURED, "pool:openai-account-X")
    (row,) = obs_h.obs_rows()
    assert row[1:7] == (obs.observation_id, "peer:cx-01", "pool:openai-account-X", "quota", "fake-source", "MEASURED")
    assert json.loads(row[7]) == {"remaining_fraction": 0.25, "unit": "requests"}
    view = obs_h.latest("peer:cx-01", "quota")
    assert view.state == EvidenceState.MEASURED and view.observation.payload["remaining_fraction"] == 0.25
    assert view.observation.resource_pool_ref == "pool:openai-account-X"
    # positive control: the pool is optional (no pool -> None, not a default pool)
    o2 = obs_h.capture("peer:cx-02", "quota", FakeObservationSource(measured({"remaining_fraction": 1.0})))
    assert o2.resource_pool_ref is None and obs_h.obs_rows()[1][3] is None


@pytest.mark.m1_id("OBS-002")
def test_obs_002_rate_limit_is_never_stored_as_quota(obs_h):
    _seed(obs_h)
    rl = {"requests_per_minute": 60, "retry_after_seconds": 12}
    obs = obs_h.capture("peer:cx-01", "rate_limit", FakeObservationSource(measured(rl, semantic="rate_limit")))
    assert obs.kind == "rate_limit"
    assert [r[4] for r in obs_h.obs_rows()] == ["rate_limit"]
    assert obs_h.latest("peer:cx-01", "quota") is None  # nothing was filed under quota
    assert obs_h.latest("peer:cx-01", "rate_limit").observation.payload == rl
    # negative: a rate-limit event offered as quota is rejected explicitly, nothing stored, nothing else changes
    before = obs_h.full_state()
    with pytest.raises(KindSemanticError):
        obs_h.capture("peer:cx-01", "quota", FakeObservationSource(measured(rl, semantic="rate_limit")))
    assert obs_h.full_state() == before
    # positive control: a quota-semantic reading is accepted as quota
    obs_h.capture("peer:cx-01", "quota", FakeObservationSource(measured({"remaining_fraction": 0.5}, semantic="quota")))
    assert [r[4] for r in obs_h.obs_rows()] == ["rate_limit", "quota"]


@pytest.mark.m1_id("OBS-003")
def test_obs_003_absent_or_unknown_never_becomes_zero_or_unlimited(obs_h):
    for i, (reading, state) in enumerate(((absent(), "ABSENT"), (unknown(), "UNKNOWN"))):
        obs = obs_h.capture(f"peer:p{i}", "quota", FakeObservationSource(reading))
        assert obs.state.value == state and obs.payload == {}
        row = obs_h.obs_rows()[-1]
        assert row[6] == state and json.loads(row[7]) == {}  # no fabricated remaining/limit key in storage
        assert obs_h.latest(f"peer:p{i}", "quota").state.value == state
    # a source that claims ABSENT but carries a measurement is dishonest: stored as ERROR evidence, the number is not persisted
    obs = obs_h.capture("peer:p9", "quota", FakeObservationSource(absent({"remaining": 0})))
    assert obs.state == EvidenceState.ERROR and obs.payload["condition"] == "malformed_source_reading"
    assert "remaining" not in json.loads(obs_h.obs_rows()[-1][7])
    # the same invariant at the persistence port: invalid evidence is rejected without any state change
    before = obs_h.full_state()
    with pytest.raises(InvalidObservationError):
        obs_h.obs.persist(Observation(observation_id="x", subject_ref="peer:p9", kind="quota", source="s",
                                      observed_at="2026-10-01T00:00:00Z", state=EvidenceState.UNKNOWN, payload={"remaining_fraction": 0.0}))
    assert obs_h.full_state() == before
    # positive control: an honestly MEASURED zero is kept as zero (exhausted), not dropped or rewritten
    obs_h.capture("peer:p8", "quota", FakeObservationSource(measured({"remaining_fraction": 0.0})))
    assert json.loads(obs_h.obs_rows()[-1][7]) == {"remaining_fraction": 0.0}


@pytest.mark.m1_id("OBS-004")
def test_obs_004_unavailable_source_is_explicit_with_condition(obs_h):
    obs = obs_h.capture("peer:cx-01", "reachability", FakeObservationSource(unavailable("executable_not_found", path="codex")))
    assert obs.state == EvidenceState.UNAVAILABLE
    assert json.loads(obs_h.obs_rows()[-1][7]) == {"condition": "executable_not_found", "path": "codex"}
    assert obs_h.latest("peer:cx-01", "reachability").state == EvidenceState.UNAVAILABLE
    # UNAVAILABLE without a source condition is not evidence: recorded as ERROR(malformed), never silently UNAVAILABLE
    bad = obs_h.capture("peer:cx-02", "reachability", FakeObservationSource(SourceReading(EvidenceState.UNAVAILABLE, {})))
    assert bad.state == EvidenceState.ERROR and bad.payload["condition"] == "malformed_source_reading"


@pytest.mark.m1_id("OBS-012")
def test_obs_012_probe_failure_only_adds_error_evidence(obs_h):
    _seed(obs_h)
    obs_h.create_stream({"stream_id": "s", "members": ["cx-01", "cx-02"]})
    core, counts = obs_h.core_digests(), obs_h.row_counts()
    cases = [(TimeoutError("probe timed out"), "timeout"), (RuntimeError("boom"), "exception:RuntimeError"),
             (42, "malformed_source_reading"), ({"state": "NOPE"}, "malformed_source_reading")]
    for i, (item, cond) in enumerate(cases, start=1):
        src = FakeObservationSource(item)
        obs = obs_h.capture("peer:cx-01", "reachability", src)
        assert src.calls == 1 and obs.state == EvidenceState.ERROR and obs.payload["condition"] == cond, (item, obs)
        assert len(obs_h.obs_rows()) == i  # exactly one explicit evidence row per failed probe
        assert obs_h.core_digests() == core  # Peer/Stream/Record/Offset untouched
    assert {t: n for t, n in obs_h.row_counts().items() if t in CORE_TABLES} == {t: counts[t] for t in CORE_TABLES}
    # BaseException (process death/interrupt) is not swallowed into evidence
    n = len(obs_h.obs_rows())
    with pytest.raises(KeyboardInterrupt):
        obs_h.capture("peer:cx-01", "reachability", FakeObservationSource(KeyboardInterrupt()))
    assert len(obs_h.obs_rows()) == n
    # positive control: a healthy probe stores MEASURED evidence through the same path
    assert obs_h.capture("peer:cx-01", "reachability", FakeObservationSource(measured({"ok": True}))).state == EvidenceState.MEASURED


@pytest.mark.m1_id("OBS-012")
def test_obs_012_failure_to_persist_evidence_is_not_a_false_success(tmp_path):
    inj = CrashInjector("observation.capture.before_commit")
    h = ObservationHarness(tmp_path / "ws", fault_hook=inj)
    with pytest.raises(CrashInjected):
        h.capture("peer:a", "reachability", FakeObservationSource(RuntimeError("probe failed")))
    assert inj.fired == ["observation.capture.before_commit"] and h.obs_rows() == []  # complete old state
    inj.disarm()
    obs = h.capture("peer:a", "reachability", FakeObservationSource(measured({"ok": True})))
    assert [r[0] for r in h.obs_rows()] == [1] and obs.observation_id == h.obs_rows()[0][1]  # ordinal not burned by the aborted tx


@pytest.mark.m1_id("OBS-012")
def test_obs_012_capture_path_never_loads_routing_or_runtime_code():
    code = (
        "import sys, tempfile, pathlib\n"
        "from peerhub.extensions.observation import ObservationStore\n"
        "from peerhub.m1.store import CoreStore\n"
        "d = pathlib.Path(tempfile.mkdtemp()) / 'w.db'\n"
        "CoreStore(d)\n"
        "s = ObservationStore(d)\n"
        "class Src:\n"
        "    def probe(self): raise TimeoutError('t')\n"
        "o = s.capture('peer:a', 'reachability', Src())\n"
        "assert o.state.value == 'ERROR', o\n"
        "bad = [m for m in sys.modules if m.startswith(('peerhub.routing', 'peerhub.dispatch', 'peerhub.adapters', 'peerhub.runtime',"
        " 'peerhub.application', 'peerhub.extensions.bridge', 'peerhub.extensions.session_bridge'))]\n"
        "sys.exit(1 if bad else 0)\n")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-800:]
