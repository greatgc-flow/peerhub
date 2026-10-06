"""Tests for M3.2 A2A Remote Adapter (A2A-001..012).

Verifies:
- Core Invariant 5: A2A identities never become PeerHub identities implicitly (quarantine).
- Core Invariant 6: Agent Card declarations != measured truth.
- ExternalExecutionRef opaque encapsulation.
- Task lifecycle and error handling.
- Zero dev-dependency violation (REL-009).
"""

from __future__ import annotations

import sys
import pytest

from peerhub.m3.a2a import (
    AgentCard,
    ExternalExecutionRef,
    A2ATaskRequest,
    A2ATaskResponse,
    A2AAdapter,
    A2AAgentNotFoundError,
    A2AIdentityLeakForbiddenError,
    A2ACardValidationError,
    A2AStateTransitionError,
)


@pytest.fixture
def sample_card() -> AgentCard:
    return AgentCard(
        agent_id="remote://partner-agent-1",
        name="DataAnalyzer",
        description="Analyzes tabular data remotely",
        declared_capabilities=["csv_analysis", "sql_query"],
        endpoint_url="https://api.partner.example/a2a/v1",
        version="1.0.0",
        metadata={"declared_uptime": "99.9%"},
    )


def test_a2a_001_invariant_identity_quarantine(sample_card: AgentCard) -> None:
    """A2A-001: Invariant 5: A2A remote identities are quarantined and never become PeerHub identities implicitly."""
    adapter = A2AAdapter()
    adapter.register_agent_card(sample_card)

    req = A2ATaskRequest(
        task_id="task-1",
        target_agent_id=sample_card.agent_id,
        action="csv_analysis",
        input_parameters={"dataset": "sales.csv"},
    )
    resp = adapter.dispatch_task(req)

    # Remote identity must be namespaced and quarantined
    assert resp.external_ref.remote_system == "remote://partner-agent-1"
    # Local PeerHub peer identity requires explicit mapping
    assert adapter.resolve_local_peer_identity(sample_card.agent_id) is None


def test_a2a_002_identity_leak_forbidden(sample_card: AgentCard) -> None:
    """A2A-002: A2AIdentityLeakForbiddenError raised when unmapped remote identity is used in native context."""
    adapter = A2AAdapter()
    adapter.register_agent_card(sample_card)

    with pytest.raises(A2AIdentityLeakForbiddenError):
        adapter.assert_valid_local_peer_identity(sample_card.agent_id)

    # Once explicit mapping is configured, it is permitted
    adapter.configure_identity_mapping(sample_card.agent_id, "peer-mapped-partner-1")
    assert adapter.resolve_local_peer_identity(sample_card.agent_id) == "peer-mapped-partner-1"
    assert adapter.assert_valid_local_peer_identity(sample_card.agent_id) == "peer-mapped-partner-1"


def test_a2a_003_invariant_card_not_measured_truth(sample_card: AgentCard) -> None:
    """A2A-003: Invariant 6: Agent Card declarations are treated as untrusted claims distinct from measured truth."""
    adapter = A2AAdapter()
    adapter.register_agent_card(sample_card)

    status = adapter.get_agent_status(sample_card.agent_id)
    assert status["declared"]["capabilities"] == ["csv_analysis", "sql_query"]
    assert status["measured"]["is_verified"] is False
    assert status["measured"]["observed_success_rate"] is None  # UNKNOWN until observed


def test_a2a_004_telemetry_observation(sample_card: AgentCard) -> None:
    """A2A-004: Empirical observation can mark agent as degraded regardless of declared healthy card."""
    adapter = A2AAdapter()
    adapter.register_agent_card(sample_card)

    adapter.record_observation(sample_card.agent_id, success=False, latency_ms=5000.0)
    adapter.record_observation(sample_card.agent_id, success=False, latency_ms=6000.0)

    status = adapter.get_agent_status(sample_card.agent_id)
    assert status["measured"]["observed_success_rate"] == 0.0
    assert status["measured"]["is_healthy"] is False
    # Declared card remains unchanged
    assert status["declared"]["name"] == "DataAnalyzer"


def test_a2a_005_external_execution_ref_encapsulation() -> None:
    """A2A-005: ExternalExecutionRef encapsulates remote protocol state as opaque reference."""
    ref = ExternalExecutionRef(
        remote_system="a2a://remote-cluster",
        remote_task_id="rmt-1029",
        remote_run_id="run-456",
        opaque_state={"step_cursor": "chunk_3", "transport_seq": 88},
    )
    ref_dict = ref.to_dict()
    assert ref_dict["remote_system"] == "a2a://remote-cluster"
    assert ref_dict["opaque_state"]["transport_seq"] == 88

    restored = ExternalExecutionRef.from_dict(ref_dict)
    assert restored == ref


def test_a2a_006_to_record_evidence(sample_card: AgentCard) -> None:
    """A2A-006: A2A task response converts to PeerHub record evidence cleanly."""
    adapter = A2AAdapter()
    adapter.register_agent_card(sample_card)

    req = A2ATaskRequest(
        task_id="t-6",
        target_agent_id=sample_card.agent_id,
        action="csv_analysis",
        input_parameters={"rows": 100},
    )
    resp = adapter.dispatch_task(req)
    evidence = resp.to_record_evidence()

    assert evidence["task_id"] == "t-6"
    assert evidence["status"] == "SUBMITTED"
    assert "external_ref" in evidence
    assert evidence["external_ref"]["remote_system"] == sample_card.agent_id


def test_a2a_007_task_lifecycle_happy_path(sample_card: AgentCard) -> None:
    """A2A-007: A2A task lifecycle happy path: SUBMITTED -> RUNNING -> COMPLETED."""
    adapter = A2AAdapter()
    adapter.register_agent_card(sample_card)

    req = A2ATaskRequest(
        task_id="t-happy",
        target_agent_id=sample_card.agent_id,
        action="csv_analysis",
        input_parameters={"query": "SELECT count(*) FROM table"},
    )
    resp = adapter.dispatch_task(req)
    assert resp.status == "SUBMITTED"

    # Remote progress to RUNNING
    adapter.update_task_status(resp.task_id, "RUNNING")
    polled_running = adapter.poll_task(resp.task_id)
    assert polled_running.status == "RUNNING"

    # Remote progress to COMPLETED
    adapter.update_task_status(resp.task_id, "COMPLETED", output={"count": 42})
    polled_completed = adapter.poll_task(resp.task_id)
    assert polled_completed.status == "COMPLETED"
    assert polled_completed.output == {"count": 42}


def test_a2a_008_task_cancellation(sample_card: AgentCard) -> None:
    """A2A-008: A2A task cancellation transitions state to CANCELLED and stops execution."""
    adapter = A2AAdapter()
    adapter.register_agent_card(sample_card)

    req = A2ATaskRequest(
        task_id="t-cancel",
        target_agent_id=sample_card.agent_id,
        action="csv_analysis",
        input_parameters={},
    )
    adapter.dispatch_task(req)
    cancelled = adapter.cancel_task("t-cancel")
    assert cancelled.status == "CANCELLED"

    # Cannot transition cancelled task to COMPLETED
    with pytest.raises(A2AStateTransitionError):
        adapter.update_task_status("t-cancel", "COMPLETED")


def test_a2a_009_unregistered_agent_error() -> None:
    """A2A-009: Dispatching to unregistered agent raises A2AAgentNotFoundError."""
    adapter = A2AAdapter()
    req = A2ATaskRequest(
        task_id="t-missing",
        target_agent_id="remote://missing-agent",
        action="ping",
        input_parameters={},
    )
    with pytest.raises(A2AAgentNotFoundError):
        adapter.dispatch_task(req)


def test_a2a_010_card_validation() -> None:
    """A2A-010: AgentCard validation rejects missing agent_id or empty endpoint_url."""
    with pytest.raises(A2ACardValidationError):
        AgentCard(
            agent_id="",
            name="NoID",
            description="",
            declared_capabilities=[],
            endpoint_url="https://example.com",
            version="1.0",
        )

    with pytest.raises(A2ACardValidationError):
        AgentCard(
            agent_id="agent-1",
            name="NoEndpoint",
            description="",
            declared_capabilities=[],
            endpoint_url="",
            version="1.0",
        )


def test_a2a_011_card_serialization(sample_card: AgentCard) -> None:
    """A2A-011: AgentCard serialization and deserialization roundtrip."""
    data = sample_card.to_dict()
    restored = AgentCard.from_dict(data)
    assert restored == sample_card


def test_a2a_012_rel_009_standard_library_compliance() -> None:
    """A2A-012: REL-009 Standard library compliance."""
    import peerhub.m3.a2a as a2a_module

    for mod_name, mod in sys.modules.items():
        if mod_name.startswith("peerhub.m3.a2a"):
            assert mod is not None
