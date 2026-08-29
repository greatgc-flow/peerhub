from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from fakes import FakeClock, FakeIdSource
from peerhub.core.errors import InvalidMutationError
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.operational_errors import OperationalErrorService
from peerhub.persistence.sqlite import SqliteStateStore

_NOW = 1_788_134_400
_PATTERN = "sandbox violation"


def _service(
    tmp_path: Path,
) -> tuple[OperationalErrorService, GovernanceBroker]:
    store = SqliteStateStore(
        tmp_path / "operational-errors.sqlite3",
        workspace_home_id="operational-errors-test",
    )
    store.initialize()
    broker = GovernanceBroker(
        store,
        clock=FakeClock([_NOW] * 1_000),
        ids=FakeIdSource([f"broker-{i}" for i in range(1, 2_000)]),
    )
    service = OperationalErrorService(
        broker,
        clock=FakeClock([_NOW + i for i in range(1_000)]),
        ids=FakeIdSource([f"domain-{i}" for i in range(1, 2_000)]),
    )
    return service, broker


def _hash(pattern: str = _PATTERN) -> str:
    return hashlib.sha256(pattern.encode("utf-8")).hexdigest()


def _series_id(pattern: str = _PATTERN) -> str:
    return f"operational-error-series:cx:{_hash(pattern)}"


def _report(
    service: OperationalErrorService,
    *,
    severity: str = "warn",
    detail: str = "details",
    threshold: int = 3,
) -> None:
    service.report_error(
        peer_key="cx",
        pattern=_PATTERN,
        severity=severity,
        detail=detail,
        actor_id="cc",
        threshold=threshold,
    )


def test_first_report_creates_series_without_review(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)

    submission = service.report_error(
        peer_key="cx",
        pattern=_PATTERN,
        severity="error",
        detail="first failure",
        actor_id="cc",
    )

    assert submission.receipt.target_id == _series_id()
    series = broker.get_target(_series_id())
    assert series is not None
    assert series.revision == 1
    assert series.state["kind"] == "operational-error-series"
    assert series.state["scope"] is None
    assert series.state["schema_version"] == 1
    assert series.state["peer_key"] == "cx"
    assert series.state["pattern"] == _PATTERN
    assert series.state["pattern_hash"] == _hash()
    assert series.state["threshold"] == 3
    assert series.state["count"] == 1
    assert series.state["quarantine_review_id"] is None
    reports = series.state["reports"]
    assert isinstance(reports, tuple)
    assert len(reports) == 1
    assert reports[0]["severity"] == "error"
    assert reports[0]["detail"] == "first failure"
    assert reports[0]["actor_id"] == "cc"
    assert broker.list_targets("quarantine-review", None) == ()


def test_reports_below_threshold_do_not_create_reviews(
    tmp_path: Path,
) -> None:
    service, broker = _service(tmp_path)

    _report(service)
    _report(service)

    series = broker.get_target(_series_id())
    assert series is not None
    assert series.state["count"] == 2
    assert len(series.state["reports"]) == 2
    assert broker.list_targets("quarantine-review", None) == ()


def test_threshold_report_creates_one_review_with_full_snapshot(
    tmp_path: Path,
) -> None:
    service, broker = _service(tmp_path)
    _report(service, detail="one")
    _report(service, detail="two")

    _report(service, severity="error", detail="three")

    review_id = f"cx:{_hash()}:3:3"
    review = broker.get_target(f"quarantine-review:{review_id}")
    assert review is not None
    assert review.revision == 1
    assert review.state["kind"] == "quarantine-review"
    assert review.state["scope"] is None
    assert review.state["schema_version"] == 1
    assert review.state["review_id"] == review_id
    assert review.state["peer_key"] == "cx"
    assert review.state["pattern"] == _PATTERN
    assert review.state["pattern_hash"] == _hash()
    assert review.state["threshold"] == 3
    assert review.state["trigger_count"] == 3
    assert review.state["series_target_id"] == _series_id()
    assert review.state["series_revision"] == 3
    assert review.state["status"] == "REQUESTED"
    assert review.state["actor_id"] == "cc"
    snapshot = review.state["reports_snapshot"]
    assert isinstance(snapshot, tuple)
    assert [item["detail"] for item in snapshot] == ["one", "two", "three"]

    series = broker.get_target(_series_id())
    assert series is not None
    assert series.state["quarantine_review_id"] == review_id
    assert len(broker.list_targets("quarantine-review", None)) == 1


def test_report_past_threshold_creates_second_distinct_review(
    tmp_path: Path,
) -> None:
    service, broker = _service(tmp_path)
    for detail in ("one", "two", "three", "four"):
        _report(service, detail=detail)

    reviews = broker.list_targets("quarantine-review", None)
    assert len(reviews) == 2
    assert {review.state["trigger_count"] for review in reviews} == {3, 4}
    fourth_id = f"cx:{_hash()}:3:4"
    fourth = broker.get_target(f"quarantine-review:{fourth_id}")
    assert fourth is not None
    assert len(fourth.state["reports_snapshot"]) == 4
    assert fourth.state["series_revision"] == 4

    series = broker.get_target(_series_id())
    assert series is not None
    assert series.state["count"] == 4
    assert series.state["quarantine_review_id"] == fourth_id


def test_existing_series_rejects_a_different_threshold(
    tmp_path: Path,
) -> None:
    service, broker = _service(tmp_path)
    _report(service, threshold=3)
    before = broker.get_target(_series_id())
    assert before is not None

    with pytest.raises(InvalidMutationError, match="threshold cannot change"):
        _report(service, threshold=4)

    after = broker.get_target(_series_id())
    assert after is not None
    assert after.revision == before.revision
    assert after.state["count"] == 1
    assert len(after.state["reports"]) == 1


def test_back_to_back_reports_share_the_counter_without_losing_history(
    tmp_path: Path,
) -> None:
    service, broker = _service(tmp_path)

    _report(service, detail="first")
    _report(service, detail="second")

    series = broker.get_target(_series_id())
    assert series is not None
    assert series.revision == 2
    assert series.state["count"] == 2
    reports = series.state["reports"]
    assert isinstance(reports, tuple)
    assert [item["detail"] for item in reports] == ["first", "second"]
    assert reports[0]["report_id"] != reports[1]["report_id"]


def test_empty_detail_is_allowed_and_threshold_must_be_positive(
    tmp_path: Path,
) -> None:
    service, broker = _service(tmp_path)
    _report(service, detail="")

    series = broker.get_target(_series_id())
    assert series is not None
    assert series.state["reports"][0]["detail"] == ""

    with pytest.raises(ValueError, match="threshold must be a positive"):
        _report(service, threshold=0)
    with pytest.raises(ValueError, match="threshold must be a positive"):
        _report(service, threshold=True)  # pyright: ignore[reportArgumentType]
