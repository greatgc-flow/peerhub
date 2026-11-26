"""An unused agy pool reports reset_time 1970-01-01 (no running window): it must not fail the whole probe."""
from __future__ import annotations

import pytest

from peerhub.extensions.observation_model import EvidenceState
from tests.communication.observation.test_agy_disabled_bucket import OBSERVED, by_pool, poll, usage

pytestmark = [pytest.mark.unit]

EPOCH = "1970-01-01T00:00:00Z"


def test_unused_pool_with_epoch_reset_reads_as_a_full_window_starting_now(monkeypatch, tmp_path):
    text = usage({"id": "3p-5h", "remaining_fraction": 1, "reset_time": EPOCH}, five_h_ok=False)
    text = text.replace('"remaining_fraction": 0.0, "reset_time": "2026-10-05T04:24:42Z"',
                        f'"remaining_fraction": 1, "reset_time": "{EPOCH}"')
    pools = by_pool(poll(monkeypatch, tmp_path, text))
    assert all(pools[pool].evidence.state == EvidenceState.MEASURED for pool in ("G-7D", "G-5H", "3P-7D", "3P-5H"))
    for pool, hours in (("3P-5H", 5), ("3P-7D", 168)):
        value = pools[pool].evidence.value
        assert value.remaining_fraction == 1.0
        assert (value.window_started_at, value.resets_at) == (OBSERVED, OBSERVED + hours * 3600)
    assert pools["G-7D"].evidence.value.remaining_fraction == 0.5


def test_a_used_pool_with_epoch_reset_is_malformed_without_dropping_the_others(monkeypatch, tmp_path):
    text = usage({"id": "3p-5h", "remaining_fraction": 0.4, "reset_time": EPOCH}, five_h_ok=False)
    pools = by_pool(poll(monkeypatch, tmp_path, text))
    assert "3P-5H" not in pools  # malformed: no reading and no ABSENT inference
    assert pools["G-7D"].evidence.state == EvidenceState.MEASURED
    assert pools["3P-7D"].evidence.state == EvidenceState.MEASURED
