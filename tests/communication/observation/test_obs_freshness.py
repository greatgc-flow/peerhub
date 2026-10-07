"""Wave 5 OBS-005, 009, 010, 011, 017, 018: freshness at read time (TD-13), capture-time ordering and tie-break (TD-23)."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, settings, strategies as st

from peerhub.extensions.observation_model import EvidenceState, FreshnessPolicy
from tests.communication.fakes.observation import FakeObservationSource, measured
from tests.communication.harness.clock import ManualClock
from tests.communication.harness.observation import T0, ObservationHarness

pytestmark = [pytest.mark.unit, pytest.mark.observation]
SET = settings(max_examples=40, deadline=None)
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def iso(offset_us: int) -> str:
    """Independent oracle: ISO instant T0 + offset (microseconds) via datetime arithmetic."""
    return (EPOCH + timedelta(seconds=T0, microseconds=offset_us)).isoformat(timespec="microseconds").replace("+00:00", "Z")


def wire(oid, observed_at, captured_at=None, kind="quota", subject="peer:cx-01"):
    return {"schema_version": "1.0", "observation_id": oid, "subject_ref": subject, "kind": kind, "source": "wire",
            "observed_at": observed_at, "captured_at": captured_at, "state": "MEASURED", "payload": {"remaining_fraction": 0.5}}


def row_hash(h):
    return hashlib.sha256(json.dumps(h.obs_rows()).encode()).hexdigest()


def mk(tmp_path, ttl, name="ws"):
    return ObservationHarness(tmp_path / name, policy=FreshnessPolicy(default_ttl_seconds=ttl, by_kind={}))


@pytest.mark.catalog_id("OBS-005")
def test_obs_005_freshness_is_derived_at_read_without_rewriting_evidence(tmp_path):
    h = mk(tmp_path, 300)
    h.capture("peer:cx-01", "quota", FakeObservationSource(measured({"remaining_fraction": 0.5})))
    digest, rows = row_hash(h), h.obs_rows()
    assert rows[0][6] == "MEASURED" and rows[0][9] == iso(0)
    fresh = h.latest("peer:cx-01", "quota", read_at=T0 + 299)
    assert (fresh.state, fresh.stored_state, fresh.age_seconds) == (EvidenceState.MEASURED, EvidenceState.MEASURED, 299.0)
    stale = h.latest("peer:cx-01", "quota", read_at=T0 + 301)
    assert (stale.state, stale.stored_state, stale.age_seconds) == (EvidenceState.STALE, EvidenceState.MEASURED, 301.0)
    assert h.obs_rows() == rows and row_hash(h) == digest  # reading (both views) rewrote nothing
    # reading again at an earlier time still derives from the unchanged row: no sticky STALE
    assert h.latest("peer:cx-01", "quota", read_at=T0 + 10).state == EvidenceState.MEASURED
    h.reopen()
    assert h.latest("peer:cx-01", "quota", read_at=T0 + 301).state == EvidenceState.STALE and row_hash(h) == digest
    # control: a stored non-MEASURED state is never upgraded to MEASURED by freshness
    h.capture("peer:cx-02", "quota", FakeObservationSource(SourceReadingAbsent()))
    assert h.latest("peer:cx-02", "quota", read_at=T0).state == EvidenceState.ABSENT


def SourceReadingAbsent():
    from tests.communication.fakes.observation import absent
    return absent()


@pytest.mark.catalog_id("OBS-009")
def test_obs_009_exact_ttl_boundary_is_deterministic(tmp_path):
    h = mk(tmp_path, 300)
    h.capture("peer:cx-01", "quota", FakeObservationSource(measured({"remaining_fraction": 0.5})))
    rows = h.obs_rows()
    cases = [(300 - 1e-6, EvidenceState.MEASURED), (300.0, EvidenceState.STALE), (300 + 1e-6, EvidenceState.STALE), (0.0, EvidenceState.MEASURED)]
    for off, expect in cases:
        assert h.latest("peer:cx-01", "quota", read_at=T0 + off).state == expect, off
    assert h.obs_rows() == rows


@pytest.mark.catalog_id("OBS-009")
@SET
@given(ttl=st.integers(1, 100000), delta_us=st.sampled_from([-1000000, -2, -1, 0, 1, 2, 1000000]), start=st.integers(0, 10**7))
def test_obs_009_boundary_property_matches_integer_oracle(tmp_path_factory, ttl, delta_us, start):
    h = ObservationHarness(tmp_path_factory.mktemp("b") / "ws", clock=ManualClock(T0 + start), policy=FreshnessPolicy(default_ttl_seconds=ttl))
    h.capture("peer:a", "quota", FakeObservationSource(measured({"remaining_fraction": 1.0})))
    read_us = ttl * 10**6 + delta_us  # age in microseconds
    view = h.latest("peer:a", "quota", read_at=T0 + start + read_us / 1e6)
    assert view is not None  # the property cannot pass by rejecting everything
    assert view.state == (EvidenceState.STALE if read_us >= ttl * 10**6 else EvidenceState.MEASURED), (ttl, delta_us)


@pytest.mark.catalog_id("OBS-009")
def test_obs_009_per_kind_ttl_policy_applies(tmp_path):
    h = ObservationHarness(tmp_path / "ws", policy=FreshnessPolicy(default_ttl_seconds=300, by_kind={"rate_limit": 10}))
    h.capture("peer:a", "rate_limit", FakeObservationSource(measured({"requests_per_minute": 5})))
    h.capture("peer:a", "quota", FakeObservationSource(measured({"remaining_fraction": 1.0})))
    assert h.latest("peer:a", "rate_limit", read_at=T0 + 10).state == EvidenceState.STALE
    assert h.latest("peer:a", "quota", read_at=T0 + 10).state == EvidenceState.MEASURED
    assert h.latest("peer:a", "rate_limit", read_at=T0 + 10).ttl_seconds == 10.0


@pytest.mark.catalog_id("OBS-010")
def test_obs_010_out_of_order_source_time_does_not_reorder_local_capture(tmp_path):
    h = mk(tmp_path, 300)
    # captured order A (t=0), B (t=5); source observed_at claims B is OLDER than A
    h.clock.set(T0)
    a = h.capture("peer:a", "quota", FakeObservationSource(measured({"remaining_fraction": 0.9}, observed_at=iso(100 * 10**6))))
    h.clock.set(T0 + 5)
    b = h.capture("peer:a", "quota", FakeObservationSource(measured({"remaining_fraction": 0.1}, observed_at=iso(-100 * 10**6))))
    view = h.latest("peer:a", "quota", read_at=T0 + 6)
    assert view.observation.observation_id == b.observation_id != a.observation_id
    assert view.skew_seconds == -105.0  # source skew retained as evidence (observed - captured)
    assert h.latest("peer:a", "quota", read_at=T0 + 6).observation.observed_at == iso(-100 * 10**6)
    assert [r[9] for r in h.obs_rows()] == [iso(0), iso(5 * 10**6)]  # local history order intact


@pytest.mark.catalog_id("OBS-010")
@SET
@given(st.lists(st.tuples(st.integers(-50, 50), st.integers(-10**4, 10**4)), min_size=1, max_size=7))
def test_obs_010_latest_follows_capture_basis_for_any_source_order(tmp_path_factory, items):
    """items: (local captured offset seconds, source observed offset seconds). Reference: max by (captured_us, ordinal)."""
    h = ObservationHarness(tmp_path_factory.mktemp("p") / "ws", policy=FreshnessPolicy(default_ttl_seconds=10**9))
    ids = []
    for cap, src in items:
        h.clock.set(T0 + cap)
        ids.append(h.capture("peer:a", "quota", FakeObservationSource(measured({"remaining_fraction": 0.5}, observed_at=iso(src * 10**6)))).observation_id)
    assert len(h.obs_rows()) == len(items)
    expected = max(range(len(items)), key=lambda i: (items[i][0], i))
    view = h.latest("peer:a", "quota", read_at=T0 + 10**4)
    assert view is not None and view.observation.observation_id == ids[expected]
    assert view.skew_seconds == float(items[expected][1] - items[expected][0])


@pytest.mark.catalog_id("OBS-011")
def test_obs_011_future_skewed_source_time_is_evidence_not_freshness(tmp_path):
    h = mk(tmp_path, 300)
    ten_years = 10 * 365 * 24 * 3600
    skewed = h.capture("peer:a", "quota", FakeObservationSource(measured({"remaining_fraction": 0.5}, observed_at=iso(ten_years * 10**6))))
    assert skewed.observed_at == iso(ten_years * 10**6)  # retained verbatim
    assert h.latest("peer:a", "quota", read_at=T0 + 299).state == EvidenceState.MEASURED
    v = h.latest("peer:a", "quota", read_at=T0 + 300)
    assert v.state == EvidenceState.STALE and v.basis == "captured_at" and v.skew_seconds == float(ten_years)  # no negative age
    # a later honest capture wins over the skewed one although its source time is "older"
    h.clock.set(T0 + 100)
    honest = h.capture("peer:a", "quota", FakeObservationSource(measured({"remaining_fraction": 0.4})))
    assert h.latest("peer:a", "quota", read_at=T0 + 101).observation.observation_id == honest.observation_id
    # fallback basis (no captured_at): a source time in the reader's future yields UNKNOWN, never indefinite freshness
    h.persist_wire("peer-observation", wire("w-future", iso(ten_years * 10**6), None, subject="peer:z"))
    f = h.latest("peer:z", "quota", read_at=T0)
    assert f.state == EvidenceState.UNKNOWN and f.age_seconds is None and f.basis == "observed_at"
    assert h.latest("peer:z", "quota", read_at=T0 + ten_years + 300).state == EvidenceState.STALE  # and it does expire


@pytest.mark.catalog_id("OBS-017")
def test_obs_017_missing_captured_at_falls_back_to_observed_at(tmp_path):
    h = mk(tmp_path, 300)
    o = h.persist_wire("peer-observation", wire("w1", iso(-1000 * 10**6), None))  # observed 1000 s before T0
    assert o.captured_at is None and h.obs_rows()[0][9] is None  # stays absent, not invented
    for off, expect in ((-1000 + 299.999999, EvidenceState.MEASURED), (-1000 + 300, EvidenceState.STALE)):
        v = h.latest("peer:cx-01", "quota", read_at=T0 + off)
        assert (v.state, v.basis) == (expect, "observed_at"), off
    # contrast: with captured_at present the capture basis wins (same observed_at would already be stale at T0)
    h.persist_wire("peer-observation", wire("w2", iso(-1000 * 10**6), iso(0), subject="peer:cx-02"))
    v2 = h.latest("peer:cx-02", "quota", read_at=T0 + 100)
    assert (v2.state, v2.basis) == (EvidenceState.MEASURED, "captured_at")


@pytest.mark.catalog_id("OBS-018")
def test_obs_018_equal_capture_times_use_persisted_ordinal(tmp_path):
    h = mk(tmp_path, 300)  # clock never advances: every captured_at equal
    observed = [iso(5 * 10**6), iso(-5 * 10**6), iso(9 * 10**6), iso(0)]  # source order deliberately scrambled
    ids = [h.capture("peer:a", "quota", FakeObservationSource(measured({"remaining_fraction": 0.5}, observed_at=o))).observation_id for o in observed]
    assert [r[0] for r in h.obs_rows()] == [1, 2, 3, 4] and len({r[9] for r in h.obs_rows()}) == 1
    for _ in range(5):
        h.reopen()
        assert h.latest("peer:a", "quota").observation.observation_id == ids[-1]
    # query-plan independence: replace the lookup index by an unrelated one so SQLite must scan + sort; the winner must not change
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(h.db_path)) as c:
        c.execute("DROP INDEX idx_obs_subject_kind")
        c.execute("DROP INDEX idx_obs_pool")
        c.execute("CREATE INDEX idx_obs_subject_kind ON observations(source)")  # same name: store init keeps it (IF NOT EXISTS)
        c.commit()
    h.reopen()
    plan = " ".join(r[3] for r in sqlite3.connect(h.db_path).execute(
        "EXPLAIN QUERY PLAN SELECT * FROM observations WHERE subject_ref='peer:a' AND kind='quota' ORDER BY effective_at_us DESC, capture_seq DESC LIMIT 1"))
    assert "USE TEMP B-TREE" in plan or "SCAN" in plan, plan
    assert h.latest("peer:a", "quota").observation.observation_id == ids[-1]
    # equal effective time through the wire path as well: the later persisted one wins regardless of observed_at
    h.persist_wire("peer-observation", wire("w-a", iso(7 * 10**6), iso(0), subject="peer:b"))
    h.persist_wire("peer-observation", wire("w-b", iso(-7 * 10**6), iso(0), subject="peer:b"))
    assert h.latest("peer:b", "quota").observation.observation_id == "w-b"


@pytest.mark.catalog_id("OBS-018")
@SET
@given(st.lists(st.integers(-10**6, 10**6), min_size=2, max_size=6))
def test_obs_018_tie_break_property(tmp_path_factory, observed_secs):
    h = ObservationHarness(tmp_path_factory.mktemp("t") / "ws", policy=FreshnessPolicy(default_ttl_seconds=10**9))
    ids = [h.capture("peer:a", "quota", FakeObservationSource(measured({"remaining_fraction": 0.5}, observed_at=iso(s * 10**6)))).observation_id
           for s in observed_secs]
    h.reopen()
    v = h.latest("peer:a", "quota")
    assert v is not None and v.observation.observation_id == ids[-1] and v.capture_seq == len(ids)
