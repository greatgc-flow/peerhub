from peerhub.extensions.observation_model import oldest_measured_quota_age


def _item(ref: str, age: float, state: str = "MEASURED") -> dict:
    return {"kind": "quota", "resource_pool_ref": ref, "state": state, "age_seconds": age,
            "measurements": {"remaining_fraction": 0.5}}


def test_a_pool_the_provider_stopped_reporting_does_not_age_the_peer():
    items = [_item("quota:cx:X-7D", 600), _item("quota:cx:X-30D", 700_000), _item("quota:cc:C-7D", 9_000)]
    assert oldest_measured_quota_age(items) == 9_000


def test_the_stalest_peer_sets_the_age_and_no_reading_is_none():
    assert oldest_measured_quota_age([_item("quota:cx:X-7D", 60), _item("quota:ag:G-7D", 5_000, "STALE")]) == 5_000
    assert oldest_measured_quota_age([]) is None
