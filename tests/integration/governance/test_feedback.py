from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from fakes import FakeClock, FakeIdSource
from peerhub.core.errors import RecordNotFoundError
from peerhub.governance.broker import GovernanceBroker
from peerhub.governance.feedback import FeedbackService
from peerhub.persistence.sqlite import SqliteStateStore

# Fixed UTC instants, so the allocated GAP date component is asserted
# against a literal rather than recomputed by the code under test.
_DAY_ONE = 1_787_961_600  # 2026-08-29T00:00:00Z
_DAY_TWO = 1_788_048_000  # 2026-08-30T00:00:00Z


def _service(
    tmp_path: Path,
    *,
    timestamps: Sequence[int] | None = None,
) -> tuple[FeedbackService, GovernanceBroker]:
    store = SqliteStateStore(
        tmp_path / "feedback.sqlite3",
        workspace_home_id="feedback-test",
    )
    store.initialize()
    broker = GovernanceBroker(
        store,
        clock=FakeClock([_DAY_ONE] * 200),
        ids=FakeIdSource([f"id-{i}" for i in range(1, 400)]),
    )
    service = FeedbackService(
        broker,
        clock=FakeClock(
            [_DAY_ONE] * 200 if timestamps is None else list(timestamps)
        ),
        ids=FakeIdSource([f"domain-{i}" for i in range(1, 400)]),
    )
    return service, broker


def _add(service: FeedbackService, title: str = "CLI flag parse error") -> None:
    service.add_feedback(
        source_peer="cc",
        category="tooling",
        severity="high",
        title=title,
        detail="details here",
        actor_id="cc",
    )


def test_add_allocates_first_id_of_the_utc_day(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    _add(service)

    target = broker.get_target("feedback:GAP-20260829-001")
    assert target is not None
    assert target.revision == 1
    assert target.state["kind"] == "feedback"
    assert target.state["scope"] is None
    assert target.state["feedback_id"] == "GAP-20260829-001"
    assert target.state["source_peer"] == "cc"
    assert target.state["category"] == "tooling"
    assert target.state["severity"] == "high"
    assert target.state["title"] == "CLI flag parse error"
    assert target.state["detail"] == "details here"
    assert target.state["status"] == "open"
    assert target.state["owner"] is None
    assert target.state["created_at"] == _DAY_ONE
    assert target.state["created_by"] == "cc"
    assert target.state["resolved_at"] is None


def test_second_add_same_utc_day_allocates_002(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    _add(service, "first")
    _add(service, "second")

    assert broker.get_target("feedback:GAP-20260829-001") is not None
    second = broker.get_target("feedback:GAP-20260829-002")
    assert second is not None
    assert second.state["title"] == "second"


def test_sequence_restarts_on_a_new_utc_day(tmp_path: Path) -> None:
    service, broker = _service(tmp_path, timestamps=[_DAY_ONE, _DAY_TWO])
    _add(service, "day one")
    _add(service, "day two")

    assert broker.get_target("feedback:GAP-20260829-001") is not None
    next_day = broker.get_target("feedback:GAP-20260830-001")
    assert next_day is not None
    assert next_day.state["title"] == "day two"


def test_add_accepts_an_empty_detail(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    service.add_feedback(
        source_peer="unknown",
        category="other",
        severity="medium",
        title="unknown gap",
        detail="",
        actor_id="cc",
    )

    target = broker.get_target("feedback:GAP-20260829-001")
    assert target is not None
    assert target.state["detail"] == ""


def test_list_returns_every_record_including_resolved(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    _add(service, "first")
    _add(service, "second")
    service.resolve_feedback(
        "GAP-20260829-001", status="done", actor_id="cc"
    )

    listed = service.list_feedback()
    assert [item.state["feedback_id"] for item in listed] == [
        "GAP-20260829-001",
        "GAP-20260829-002",
    ]
    assert [item.state["status"] for item in listed] == ["done", "open"]


def test_get_feedback_raises_for_a_missing_id(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    with pytest.raises(RecordNotFoundError):
        service.get_feedback("GAP-99999999-999")


def test_resolve_missing_id_raises_record_not_found(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    _add(service)

    with pytest.raises(RecordNotFoundError):
        service.resolve_feedback(
            "GAP-99999999-999", status="done", actor_id="cc"
        )


def test_resolve_preserves_owner_when_omitted(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    _add(service)
    service.resolve_feedback(
        "GAP-20260829-001", status="done", owner="cx", actor_id="cc"
    )
    service.resolve_feedback(
        "GAP-20260829-001", status="dismissed", actor_id="cc"
    )

    target = broker.get_target("feedback:GAP-20260829-001")
    assert target is not None
    assert target.state["status"] == "dismissed"
    assert target.state["owner"] == "cx"


def test_resolve_overwrites_owner_when_supplied(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    _add(service)
    service.resolve_feedback(
        "GAP-20260829-001", status="done", owner="cx", actor_id="cc"
    )
    service.resolve_feedback(
        "GAP-20260829-001", status="done", owner="ag", actor_id="cc"
    )

    target = broker.get_target("feedback:GAP-20260829-001")
    assert target is not None
    assert target.state["owner"] == "ag"


def test_repeat_resolve_refreshes_timestamps(tmp_path: Path) -> None:
    service, broker = _service(
        tmp_path,
        timestamps=[_DAY_ONE, _DAY_ONE + 10, _DAY_ONE + 20],
    )
    _add(service)
    service.resolve_feedback(
        "GAP-20260829-001", status="done", actor_id="cc"
    )
    first = broker.get_target("feedback:GAP-20260829-001")
    assert first is not None
    assert first.state["resolved_at"] == _DAY_ONE + 10
    assert first.state["updated_at"] == _DAY_ONE + 10

    # A repeat call with the same status is neither an error nor a no-op.
    service.resolve_feedback(
        "GAP-20260829-001", status="done", actor_id="cc"
    )
    second = broker.get_target("feedback:GAP-20260829-001")
    assert second is not None
    assert second.revision == first.revision + 1
    assert second.state["status"] == "done"
    assert second.state["resolved_at"] == _DAY_ONE + 20
    assert second.state["updated_at"] == _DAY_ONE + 20


def test_status_accepts_arbitrary_non_empty_text(tmp_path: Path) -> None:
    service, broker = _service(tmp_path)
    _add(service)
    service.resolve_feedback(
        "GAP-20260829-001",
        status="wont-fix/deferred to next round",
        actor_id="cc",
    )

    target = broker.get_target("feedback:GAP-20260829-001")
    assert target is not None
    assert target.state["status"] == "wont-fix/deferred to next round"


def test_status_still_rejects_empty_text(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    _add(service)
    with pytest.raises(ValueError):
        service.resolve_feedback(
            "GAP-20260829-001", status="   ", actor_id="cc"
        )

from peerhub.governance.contract import MutationRequest, EffectIntent
from peerhub.core.protocol import CommandID

def test_add_feedback_rejects_empty_strings(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    with pytest.raises(ValueError):
        service.add_feedback(source_peer="", category="c", severity="s", title="t", detail="d", actor_id="a")
    with pytest.raises(ValueError):
        service.add_feedback(source_peer="sp", category="", severity="s", title="t", detail="d", actor_id="a")
    with pytest.raises(ValueError):
        service.add_feedback(source_peer="sp", category="c", severity="", title="t", detail="d", actor_id="a")
    with pytest.raises(ValueError):
        service.add_feedback(source_peer="sp", category="c", severity="s", title="", detail="d", actor_id="a")
    with pytest.raises(ValueError):
        service.add_feedback(source_peer="sp", category="c", severity="s", title="t", detail="d", actor_id="")

def test_add_feedback_surfaces_stale_revision_error_on_collision(tmp_path: Path) -> None:
    from peerhub.core.errors import StaleRevisionError
    service, broker = _service(tmp_path)
    now = service._clock.now()
    feedback_id = service._next_feedback_id(service._utc_date_token(now))
    broker.submit(
        MutationRequest(
            request_id="fake-request",
            command_id=CommandID("fake-command"),
            correlation_id="fake-correlation",
            client_id="test",
            command_type="test.inject",
            idempotency_key="fake-request",
            actor_id="test",
            policy_revision="test-v1",
            target_id=f"feedback:{feedback_id}",
            expected_revision=0,
            operation="test.inject",
            desired_state={"feedback_id": feedback_id},
            effect_intent=EffectIntent(kind="test.noop", payload={}),
        )
    )
    with pytest.raises(StaleRevisionError):
        _add(service)
