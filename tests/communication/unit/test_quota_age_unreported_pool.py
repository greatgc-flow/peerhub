from peerhub.extensions.observation_model import oldest_measured_quota_age


def _pool(peer: str, pool: str, state: str, age: float) -> dict[str, object]:
    return {"kind": "quota", "subject_ref": peer, "resource_pool_ref": f"quota:{peer}:{pool}", "state": state,
            "age_seconds": age, "payload": {"remaining_fraction": 0.5}}


def test_pool_the_provider_stopped_reporting_does_not_age_the_peer() -> None:
    fresh = _pool("cx", "5H", "MEASURED", 30.0)
    gone = _pool("cx", "X-30D", "STALE", 8 * 86400.0)
    assert oldest_measured_quota_age([fresh, gone]) == 30.0


def test_all_old_readings_still_report_the_oldest() -> None:
    old = _pool("cx", "5H", "STALE", 2700.0)
    older = _pool("cx", "7D", "STALE", 4500.0)
    assert oldest_measured_quota_age([old, older]) == 4500.0


def test_a_fresh_peer_does_not_hide_another_peers_stale_pool() -> None:
    items = [_pool("cx", "5H", "MEASURED", 30.0), _pool("ag", "5H", "STALE", 7200.0)]
    assert oldest_measured_quota_age(items) == 7200.0
