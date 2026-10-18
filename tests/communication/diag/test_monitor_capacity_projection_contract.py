"""RED contract for capacity's current-window quota projection."""
from __future__ import annotations

import pytest

from peerhub.extensions.monitor_read_model import render_monitor_frame
from peerhub.extensions.observation_model import EvidenceState, Observation, epoch_to_iso
from peerhub.extensions.quota_projection import project_exhaustion
from tests.communication.harness.observation import T0, ObservationHarness

pytestmark = [pytest.mark.unit, pytest.mark.diag, pytest.mark.platform_sensitive]

WEEK = 7 * 86_400
WINDOW_START = T0
MEASURED_AT = T0 + 3 * 86_400
RESET_AT = WINDOW_START + WEEK
READ_AT = MEASURED_AT + 10


def _pool(pool_id: str, kind: str = "QUOTA") -> dict:
    return {"schema_version": "1.0", "resource_pool_id": pool_id,
            "provider": "openai", "kind": kind, "metadata": {}}


def _persist(h: ObservationHarness, *, observation_id: str = "quota-current",
             pool: str = "weekly", observed_at: float = MEASURED_AT,
             captured_at: float = READ_AT, state: EvidenceState = EvidenceState.MEASURED,
             remaining_fraction: float = 0.6, window_start=WINDOW_START,
             resets_at=RESET_AT, anchor_basis: str | None = None) -> None:
    payload = {"remaining_fraction": remaining_fraction}
    if window_start is not None:
        payload["window_started_at"] = window_start
    if resets_at is not None:
        payload["resets_at"] = resets_at
    if anchor_basis is not None:
        payload["anchor_basis"] = anchor_basis
    h.obs.persist(Observation(
        observation_id=observation_id, subject_ref="peer:cx", resource_pool_ref=pool,
        kind="quota", source="fixture-quota-probe", observed_at=epoch_to_iso(observed_at),
        captured_at=epoch_to_iso(captured_at), state=state, payload=payload,
    ))


def _row(h: ObservationHarness, *, read_at: float = READ_AT, pool: str = "weekly") -> dict:
    frame = render_monitor_frame(h.db_path, ["capacity"], read_at=read_at)
    assert frame["groups"]["capacity"]["status"] == "OK", frame
    return next(row for row in frame["groups"]["capacity"]["data"]["observations"]
                if row["pool_ref"] == pool)


def test_capacity_projection_uses_one_fresh_measurement_and_exact_week_window(tmp_path):
    h = ObservationHarness(tmp_path / "workspace")
    h.register_pool(_pool("weekly"))
    _persist(h)

    row = _row(h)
    expected = project_exhaustion(
        0.4, WINDOW_START, MEASURED_AT, RESET_AT, READ_AT,
        evidence_state="MEASURED", anchor_basis=None,
    )
    assert row["projection"] == expected
    assert row["projection"]["pace_pt"] == pytest.approx(40 - 100 * 3 / 7)
    assert row["projection"]["state"] == "on_pace"
    assert row["projection"]["anchor_basis"] == "reset_minus_duration_inferred"
    assert row["projection"]["exhaustion_at"] is not None


@pytest.mark.parametrize("case", ["missing_anchor", "stale", "future", "expired"])
def test_capacity_projection_preserves_typed_unknown_stale_or_expired_state(tmp_path, case):
    h = ObservationHarness(tmp_path / "workspace")
    h.register_pool(_pool("weekly"))
    read_at = READ_AT
    if case == "missing_anchor":
        _persist(h, window_start=None, resets_at=None)
    elif case == "stale":
        _persist(h, captured_at=READ_AT - 301)
        read_at = READ_AT
    elif case == "future":
        _persist(h, observed_at=READ_AT + 1, captured_at=READ_AT)
    else:
        _persist(h)
        read_at = RESET_AT

    projection = _row(h, read_at=read_at)["projection"]
    assert set(projection) == {
        "pace_pt", "projected_at_reset", "state", "exhaustion_at",
        "measured_at", "anchor_basis", "reason",
    }
    assert projection["state"] == {
        "missing_anchor": "unknown", "stale": "stale",
        "future": "unknown", "expired": "expired",
    }[case]
    if case in {"missing_anchor", "stale", "future", "expired"}:
        assert projection["reason"]
    if case in {"missing_anchor", "future", "expired"}:
        assert projection["exhaustion_at"] is None
        assert projection["projected_at_reset"] is None


def test_capacity_projection_does_not_claim_provider_fixed_anchor_without_source_provenance(tmp_path):
    h = ObservationHarness(tmp_path / "workspace")
    h.register_pool(_pool("weekly"))
    _persist(h)

    row = _row(h)
    assert row["source"] == "fixture-quota-probe"
    assert row["projection"]["anchor_basis"] == "reset_minus_duration_inferred"
    assert row["projection"]["anchor_basis"] != "provider_fixed_window"


@pytest.mark.parametrize(("resets_at", "expected"), [
    (READ_AT + 17880, 17880), (READ_AT - 1, 0), (None, None),
    ("malformed", None), (True, None), ({"unexpected": "timestamp"}, None),
])
def test_capacity_reset_countdown_is_additive_and_uses_frame_time(tmp_path, resets_at, expected):
    h = ObservationHarness(tmp_path / "workspace")
    h.register_pool(_pool("weekly"))
    _persist(h, resets_at=resets_at)
    before = h.full_state()

    row = _row(h)

    assert row["reset_in_seconds"] == expected
    assert row["measurements"] == {"remaining_fraction": 0.6}
    assert h.full_state() == before


@pytest.mark.parametrize("state", [EvidenceState.STALE, EvidenceState.UNKNOWN, EvidenceState.ABSENT])
def test_capacity_reset_countdown_requires_measured_evidence(tmp_path, state):
    h = ObservationHarness(tmp_path / "workspace")
    h.register_pool(_pool("weekly"))
    if state == EvidenceState.STALE:
        _persist(h, captured_at=READ_AT - 301, resets_at=READ_AT + 17880)
    else:
        h.obs.persist(Observation(
            observation_id="unmeasured", subject_ref="peer:cx", resource_pool_ref="weekly",
            kind="quota", source="fixture-quota-probe", observed_at=epoch_to_iso(MEASURED_AT),
            captured_at=epoch_to_iso(READ_AT), state=state,
            payload={"resets_at": READ_AT + 17880, "condition": "unavailable"},
        ))

    row = _row(h)
    assert row["state"] == state.value
    assert row["reset_in_seconds"] is None
