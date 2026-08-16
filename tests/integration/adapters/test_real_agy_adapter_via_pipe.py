import json
from pathlib import Path

import pytest

from peerhub.adapters.agy_adapter import RealAgyAdapter, _AGY_PROFILE
from peerhub.adapters.contract import AdapterRequest, SessionAction
from peerhub.core.execution import TransportLimits
from peerhub.dispatch.contract import CompletionAssessmentState, RequestState
from peerhub.dispatch.materializer import ArtifactMaterializer
from peerhub.persistence.sqlite import SqliteStateStore
from typing import Iterator

@pytest.fixture
def store(tmp_path: Path) -> Iterator[SqliteStateStore]:
    from tests.integration.dispatch.conftest import _seed_health
    db_path = tmp_path / "state.db"
    store = SqliteStateStore(db_path, workspace_home_id="workspace-vertical-dispatch")
    store.initialize()
    _seed_health(store)
    yield store
    store.close()

from tests.integration.dispatch.test_vertical_dispatch import (
    _admit_and_prepare,
    _completion_contract,
    _envelope,
    _workflows,
)


@pytest.mark.slow
def test_real_agy_adapter_via_pipe(
    tmp_path: Path,
    store: SqliteStateStore,
) -> None:
    """Prove a real PeerAdapter survives peerhub's full supervised dispatch pipeline."""
    workflows, dispatch = _workflows(store, peer_kind="ag")
    cmd_id, cap_lease_id, peer_instance = _admit_and_prepare(
        workflows, _envelope(), profile_id="ag.standard"
    )

    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()

    materializer = ArtifactMaterializer(
        unit_of_work_factory=store.unit_of_work,
        workspace_root=workspace_root,
    )

    contract = _completion_contract()
    adapter_req = AdapterRequest(
        request_id="req-real-agy-01",
        prompt_content="say hello in two words",
        prompt_reference=None,
        workspace_scope="ws-1",
        profile_id="ag.standard",
        requested_session_action=SessionAction.NONE,
        completion_contract=contract,
    )

    adapter = RealAgyAdapter()
    limits = TransportLimits(
        process_timeout_ms=60000,
        silence_timeout_ms=60000,
        max_output_bytes=1000000,
    )

    res = workflows.dispatch_and_execute(
        cmd_id,
        capability_lease_id=cap_lease_id,
        peer_instance_id=peer_instance,
        current_policy_revision=7,
        materializer=materializer,
        adapter_request=adapter_req,
        peer_adapter=adapter,
        profile=_AGY_PROFILE,
        limits=limits,
        workspace_roots={"ws-1": workspace_root},
        content_providers={},
        completion_contract=contract,
        heartbeat_timeout_ms=30000,
        service=dispatch,
    )

    # a) dispatch_and_execute() completes without raising
    assert res.request.state is RequestState.SUCCEEDED_VERIFIED
    assert res.attempt.state is RequestState.SUCCEEDED_VERIFIED

    # b) The resulting ExecutionWorkflowResult reflects a real successful completion
    assert res.process_outcome is not None
    assert res.process_outcome.execution_outcome.started is True
    assert res.process_outcome.execution_outcome.exit_code == 0
    assert res.process_outcome.execution_outcome.cancelled is False

    assert res.completion_assessment is not None
    assert res.completion_assessment.state is CompletionAssessmentState.VERIFIED

    # c) The decoded output genuinely contains agy's real response text
    raw_bytes = res.process_outcome.canonical_stream
    parsed = json.loads(raw_bytes.decode("utf-8"))
    
    assert "response" in parsed
    assert len(parsed["response"].strip()) > 0
