"""Local A2A bookkeeping guards; these tests do not prove remote execution."""
from __future__ import annotations

import pytest

from peerhub.extensions.a2a import A2AAdapter, A2AStateTransitionError, A2ATaskRequest, AgentCard, LoopbackA2ATransport


def adapter() -> A2AAdapter:
    result = A2AAdapter(LoopbackA2ATransport())
    result.register_agent_card(AgentCard("remote", "Remote", "Declared", [], "https://example.invalid/a2a"))
    return result


def test_duplicate_task_keeps_original_identity_and_terminal_result():
    port = adapter()
    request = A2ATaskRequest("task", "remote", "review", {"text": "input"})
    original = port.dispatch_task(request)
    assert port.dispatch_task(request) == original
    port.update_task_status("task", "RUNNING")
    terminal = port.update_task_status("task", "COMPLETED", {"text": "output"})
    assert port.dispatch_task(request) == terminal
    with pytest.raises(A2AStateTransitionError):
        port.dispatch_task(A2ATaskRequest("task", "remote", "other", {}))


@pytest.mark.parametrize("state", ["UNKNOWN", "COMPLETED", "SUBMITTED"])
def test_invalid_submitted_transition_rejected(state: str):
    port = adapter()
    port.dispatch_task(A2ATaskRequest("task", "remote", "review", {}))
    with pytest.raises(A2AStateTransitionError):
        port.update_task_status("task", state)
    assert port.poll_task("task").status == "SUBMITTED"


@pytest.mark.parametrize("latency", [float("nan"), float("inf"), -1, True])
def test_invalid_measured_latencies_rejected(latency):
    port = adapter()
    with pytest.raises(ValueError):
        port.record_observation("remote", True, latency)
    assert port.get_agent_status("remote")["measured"]["is_verified"] is False
