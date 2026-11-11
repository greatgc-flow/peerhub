"""Offline benchmark validation, no latency thresholds or paid provider calls."""
from __future__ import annotations

import pytest

from tools.bridge_init_bench import benchmark

pytestmark = [pytest.mark.unit, pytest.mark.sqlite, pytest.mark.platform_sensitive]


def test_init_benchmark_reports_measured_counts_and_provenance():
    report = benchmark(rounds=1, workers=(1, 2))
    assert report["provider_calls"] == 0
    assert report["rounds"] == 1
    assert all(len(value) == 64 for value in report["source_sha256"].values())
    assert [row["samples"] for row in report["cases"]] == [1, 2]
    for row in report["cases"]:
        assert row["p95_ms"] >= row["p50_ms"] >= 0
        assert row["schema_cookie_delta"] == 0


@pytest.mark.parametrize("rounds,workers", [(0, (1,)), (True, (1,)), (21, (1,)),
                                           (1, ()), (1, (True,)), (1, (9,)), (1, (1, 1))])
def test_invalid_benchmark_arguments_fail_before_initialization(rounds, workers, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid arguments must not create a store")
    monkeypatch.setattr("tools.bridge_init_bench.TemporaryDirectory", forbidden)
    with pytest.raises(ValueError):
        benchmark(rounds=rounds, workers=workers)
