"""cx plan-window addendum (X1-X5): a window the provider does not report is evidence, never usage."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from peerhub.core.store import CoreStore
from peerhub.extensions.adapters.profiles import packaged_defaults_meta
from peerhub.extensions.monitor_read_model import mapped_quota, render_monitor_frame
from peerhub.extensions.observation_model import (
    NOT_REPORTED_REASON, EvidenceState, FreshnessPolicy, Observation, evaluate, instant_us,
)
from peerhub.extensions.quota_capture import refresh_quota

pytestmark = [pytest.mark.unit]

REASON = f"{NOT_REPORTED_REASON}; plan=prolite"


def _absent_observation(reason: str | None = REASON) -> Observation:
    return Observation(observation_id="o1", subject_ref="cx", resource_pool_ref="quota:cx:X-5H", kind="quota",
                       source="codex_app_server", state=EvidenceState.ABSENT,
                       payload={"reason": reason} if reason else {},
                       observed_at="2026-11-08T10:00:00Z", captured_at="2026-11-08T10:00:00Z")


def test_not_reported_absent_is_fresh_then_stale_after_ttl():
    obs = _absent_observation()
    base = instant_us("2026-11-08T10:00:00Z")
    policy = FreshnessPolicy(default_ttl_seconds=300)
    assert evaluate(obs, 1, base + 299 * 1_000_000, policy).state == EvidenceState.ABSENT
    aged = evaluate(obs, 1, base + 300 * 1_000_000, policy)
    assert aged.state == EvidenceState.STALE and aged.stored_state == EvidenceState.ABSENT


def test_other_absent_rows_keep_their_stored_state():
    for reason in (None, "something else"):
        view = evaluate(_absent_observation(reason), 1, instant_us("2026-11-09T10:00:00Z"), FreshnessPolicy())
        assert view.state == EvidenceState.ABSENT


def _row(pool, state, remaining=None, effective=1, reason=None):
    return {"pool_ref": f"quota:cx:{pool}", "pool_kind": "QUOTA", "state": state, "effective_at_us": effective,
            "measurements": {"remaining_fraction": remaining} if remaining is not None else None,
            "source": "codex_app_server", "age_seconds": 5.0, "reason": reason}


def test_mapped_quota_skips_not_reported_pool_and_uses_reported_one():
    q = mapped_quota(["X-5H", "X-7D"], [_row("X-5H", "ABSENT", reason=REASON), _row("X-7D", "MEASURED", 0.89)])
    assert q is not None
    assert (q["state"], q["pool_ref"], q["percent"]) == ("MEASURED", "quota:cx:X-7D", 89)


def test_mapped_quota_all_not_reported_is_unknown_never_full():
    q = mapped_quota(["X-5H", "X-7D"], [_row("X-5H", "ABSENT", reason=REASON), _row("X-7D", "ABSENT", reason=REASON)])
    assert q is not None
    assert q["state"] == "UNKNOWN" and q["pool_ref"] == "not_reported" and q["percent"] is None
    assert q["remaining_fraction"] is None


def test_mapped_quota_picks_freshest_row_per_pool_not_first():
    stale_first = _row("X-5H", "STALE", effective=1)
    fresh_absent = _row("X-5H", "ABSENT", effective=2, reason=REASON)
    q = mapped_quota(["X-5H", "X-7D"], [stale_first, fresh_absent, _row("X-7D", "MEASURED", 0.5)])
    assert q is not None and q["state"] == "MEASURED" and q["percent"] == 50


def test_mapped_quota_still_fails_closed_on_stale_missing_or_other_absent():
    measured = _row("X-7D", "MEASURED", 0.5)
    assert mapped_quota(["X-5H", "X-7D"], [_row("X-5H", "STALE"), measured])["state"] == "INCOMPLETE"
    assert mapped_quota(["X-5H", "X-7D"], [measured])["state"] == "INCOMPLETE"
    assert mapped_quota(["X-5H", "X-7D"], [_row("X-5H", "ABSENT"), measured])["state"] == "INCOMPLETE"  # no reason
    expired_not_reported = _row("X-5H", "STALE", reason=REASON)
    assert mapped_quota(["X-5H", "X-7D"], [expired_not_reported, measured])["state"] == "INCOMPLETE"


def test_refresh_persists_absent_pool_row_that_supersedes_the_stale_one(tmp_path):
    db = tmp_path / "core.db"
    CoreStore(db)

    def reading(state, scope, value=None, extra=None):
        return SimpleNamespace(
            evidence=SimpleNamespace(state=state, value=value, evidence_ref="probe:cx:app-server",
                                     source_tag="codex_app_server", observed_at=100, captured_at=100),
            extra=extra or {})

    measured = reading(EvidenceState.MEASURED, "X-7D", SimpleNamespace(
        quota_pool_scope="X-7D", remaining_fraction=0.89, window_started_at=100, resets_at=604900))
    absent = reading(EvidenceState.ABSENT, "X-5H", extra={"pool_scope": "X-5H", "reason": REASON})
    result = refresh_quota(db, ["cx"], pollers={"cx": lambda **kw: [measured, absent]},
                           version_checker=lambda kind: packaged_defaults_meta()["observed_versions"]["cx"])
    by_pool = {o["resource_pool_ref"]: o for o in result["observations"]}
    assert by_pool["quota:cx:X-5H"]["state"] == "ABSENT"
    assert by_pool["quota:cx:X-5H"]["payload"]["reason"] == REASON
    assert "remaining_fraction" not in by_pool["quota:cx:X-5H"]["payload"]
    assert by_pool["quota:cx:X-7D"]["state"] == "MEASURED"

    fresh = render_monitor_frame(str(db), ["capacity"], read_at=150)["groups"]["capacity"]["data"]["observations"]
    row = next(o for o in fresh if o["pool_ref"] == "quota:cx:X-5H")
    assert row["state"] == "ABSENT" and row["reason"] == REASON and row["measurements"] is None
    later = render_monitor_frame(str(db), ["capacity"], read_at=100 + 301)["groups"]["capacity"]["data"]["observations"]
    assert next(o for o in later if o["pool_ref"] == "quota:cx:X-5H")["state"] == "STALE"
