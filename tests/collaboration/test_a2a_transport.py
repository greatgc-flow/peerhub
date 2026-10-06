"""A declared card is not a configured/measured runtime transport."""
import dataclasses

import pytest

from peerhub.extensions.a2a import (A2AAdapter, A2ATaskRequest, AgentCard, LoopbackA2ATransport,
                                    A2ATransportUnavailableError, A2AExecutionUncertainError,
                                    A2AStateTransitionError)


CARD = AgentCard("remote", "Remote", "declared", ["review"], "https://example.invalid/a2a")
REQUEST = A2ATaskRequest("task", "remote", "review", {"input": "text"})


def test_unbound_card_never_fabricates_submission():
    port = A2AAdapter()
    port.register_agent_card(CARD)
    with pytest.raises(A2ATransportUnavailableError):
        port.dispatch_task(REQUEST)
    assert port.get_agent_status("remote")["measured"]["is_verified"] is False


class CountingTransport(LoopbackA2ATransport):
    calls = 0
    fail = False
    invalid = False
    confirm_cancel = True
    def submit(self, card, request, timeout_s):
        self.calls += 1
        assert timeout_s == 3 and card.endpoint_url == CARD.endpoint_url
        if self.fail:
            raise TimeoutError("response lost after remote effect")
        response = super().submit(card, request, timeout_s)
        return dataclasses.replace(response, task_id="wrong" if self.invalid else request.task_id, status="RUNNING")
    def poll(self, card, current, timeout_s):
        return dataclasses.replace(current, status="COMPLETED", output={"result": "done"})
    def cancel(self, card, current, timeout_s):
        return dataclasses.replace(current, status="CANCELLED" if self.confirm_cancel else "RUNNING")


def test_transport_invoked_once_duplicate_reuses_binding_and_poll_observes_remote_result():
    transport = CountingTransport()
    port = A2AAdapter(transport, timeout_s=3)
    port.register_agent_card(CARD)
    response = port.dispatch_task(REQUEST)
    response.external_ref.opaque_state["injected"] = True
    assert "injected" not in port.dispatch_task(REQUEST).external_ref.opaque_state
    assert transport.calls == 1
    assert port.poll_task("task").output == {"result": "done"}
    assert port.cancel_task("task").status == "COMPLETED"  # no terminal rewrite


@pytest.mark.parametrize("failure", ["fail", "invalid"])
def test_uncertain_submit_never_blindly_replays(failure):
    transport = CountingTransport()
    setattr(transport, failure, True)
    port = A2AAdapter(transport, timeout_s=3)
    port.register_agent_card(CARD)
    with pytest.raises(A2AExecutionUncertainError):
        port.dispatch_task(REQUEST)
    with pytest.raises(A2AExecutionUncertainError):
        port.dispatch_task(REQUEST)
    assert transport.calls == 1


def test_unconfirmed_cancel_preserves_running_evidence():
    transport = CountingTransport()
    transport.confirm_cancel = False
    transport.poll = lambda *args: None
    port = A2AAdapter(transport, timeout_s=3)
    port.register_agent_card(CARD)
    port.dispatch_task(REQUEST)
    with pytest.raises(A2AStateTransitionError):
        port.cancel_task("task")
    assert port.poll_task("task").status == "RUNNING"
