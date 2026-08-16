import pathlib
import pytest
from peerhub.adapters.contract import (
    AdapterRequest,
    CompletionContractView,
    SessionAction,
    SessionHint,
    TransportLimits,
    EvidencePayload,
)
from peerhub.adapters.codex_adapter import RealCodexAdapter, _CODEX_PROFILE
from peerhub.dispatch.materializer import ArtifactMaterializer, MaterializationItemRequest, MaterializationSource, MaterializationStatus

class FakeCompletionContractView:
    @property
    def contract_id(self) -> str:
        return "fake-contract"

def test_codex_evidence_offloading_fresh(tmp_path: pathlib.Path):
    adapter = RealCodexAdapter()
    
    payload_small = EvidencePayload(source_tool_name="tool_a", content_bytes=b"small payload")
    payload_large = EvidencePayload(source_tool_name="tool_b", content_bytes=b"large payload " * 100000)
    
    request = AdapterRequest(
        request_id="req-125",
        prompt_content="original prompt",
        prompt_reference=None,
        workspace_scope=".",
        profile_id="cx.standard",
        requested_session_action=SessionAction.NONE,
        completion_contract=FakeCompletionContractView(),
        evidence_payloads=(payload_small, payload_large),
    )
    
    limits = TransportLimits(
        process_timeout_ms=60000,
        silence_timeout_ms=60000,
        max_output_bytes=1000000,
    )
    
    plan = adapter.plan_invocation(
        request=request,
        profile=_CODEX_PROFILE,
        session=None,
        limits=limits,
    )
    
    # argv for fresh: codex.cmd exec --json <prompt>
    prompt = plan.argv[3]
    
    # (a) Under threshold gets inlined normally
    assert "small payload" in prompt
    
    # (b) Over threshold gets offloaded
    assert ("large payload " * 100000) not in prompt
    assert "large output was" in prompt
    assert "offloaded to evidence://ev_" in prompt
    
    # (c) The offloaded content is actually retrievable from disk
    assert len(plan.artifacts) == 1
    artifact_spec = plan.artifacts[0]
    
    from peerhub.dispatch.contract import ArtifactMetadata, ArtifactState
    import dataclasses

    class MockUOW:
        def __init__(self):
            self.metadata = ArtifactMetadata(
                attempt_id="attempt-1",
                artifact_id=artifact_spec.artifact_id,
                placeholder=artifact_spec.placeholder,
                workspace_scope_id=".",
                staging_ref="ref",
                expected_sha256_hex=artifact_spec.sha256_hex,
                expected_length=artifact_spec.expected_length,
                access_mode=artifact_spec.access_mode,
                declared_lifecycle=artifact_spec.lifecycle,
                state=ArtifactState.DECLARED,
                declared_at=100,
                revision=1,
            )
        def get_artifact_metadata(self, attempt_id, artifact_id):
            return self.metadata
        def mark_artifact_staged(self, attempt_id, artifact_id, staging_path_relative, expected_revision, staged_at):
            self.metadata = dataclasses.replace(self.metadata, revision=self.metadata.revision + 1, state=ArtifactState.STAGED)
            return True
        def mark_artifact_verified(self, attempt_id, artifact_id, verified_digest, verified_length, target_path_relative, expected_revision, verified_at):
            self.metadata = dataclasses.replace(self.metadata, revision=self.metadata.revision + 1, state=ArtifactState.VERIFIED, verified_sha256_hex=verified_digest, verified_length=verified_length)
            return True
        def commit(self): pass
        def rollback(self): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass

    materializer = ArtifactMaterializer(
        unit_of_work_factory=lambda: MockUOW(),
        workspace_root=tmp_path,
    )

    attempt_id = "attempt-1"
    target_path = pathlib.PurePosixPath(f".artifacts/{artifact_spec.artifact_id.replace('evidence://', '')}")
    item_req = MaterializationItemRequest(
        artifact_id=artifact_spec.artifact_id,
        source=MaterializationSource.BYTES_INLINE,
        target_path=target_path,
        expected_digest=f"sha256:{artifact_spec.sha256_hex}",
        expected_length=artifact_spec.expected_length,
        attempt_id=attempt_id,
    )
    
    result = materializer.materialize(
        manifest=item_req,
        content_provider=lambda: artifact_spec.content_bytes,
    )
    
    assert result.status == MaterializationStatus.SUCCESS
    
    # Verify file is retrievable
    abs_target = tmp_path / target_path
    assert abs_target.exists()
    assert abs_target.read_bytes() == artifact_spec.content_bytes

def test_codex_evidence_offloading_resume(tmp_path: pathlib.Path):
    adapter = RealCodexAdapter()
    
    payload_small = EvidencePayload(source_tool_name="tool_a", content_bytes=b"small payload")
    payload_large = EvidencePayload(source_tool_name="tool_b", content_bytes=b"large payload " * 100000)
    
    request = AdapterRequest(
        request_id="req-125",
        prompt_content="original prompt",
        prompt_reference=None,
        workspace_scope=".",
        profile_id="cx.standard",
        requested_session_action=SessionAction.RESUME,
        completion_contract=FakeCompletionContractView(),
        evidence_payloads=(payload_small, payload_large),
    )
    
    session = SessionHint(
        external_session_id="ext-sesh-456",
        adapter_fingerprint=None,
        session_generation=None,
    )
    
    limits = TransportLimits(
        process_timeout_ms=60000,
        silence_timeout_ms=60000,
        max_output_bytes=1000000,
    )
    
    plan = adapter.plan_invocation(
        request=request,
        profile=_CODEX_PROFILE,
        session=session,
        limits=limits,
    )
    
    # argv for resume: codex.cmd exec resume --json <session_id> <prompt>
    prompt = plan.argv[5]
    
    # (a) Under threshold gets inlined normally
    assert "small payload" in prompt
    
    # (b) Over threshold gets offloaded
    assert ("large payload " * 100000) not in prompt
    assert "large output was" in prompt
    assert "offloaded to evidence://ev_" in prompt
