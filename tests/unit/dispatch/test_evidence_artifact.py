import pytest
from peerhub.dispatch.contract import EvidenceArtifact

def test_evidence_artifact_valid():
    ea = EvidenceArtifact(
        artifact_id="evidence://ev_1234abcd",
        source_tool_name="tool_call_xyz",
        content_length=1024,
        sha256_hex="a" * 64,
        created_at=1000,
        expires_at=2000,
    )
    assert ea.artifact_id == "evidence://ev_1234abcd"
    assert ea.source_tool_name == "tool_call_xyz"
    assert ea.content_length == 1024
    assert ea.sha256_hex == "a" * 64
    assert ea.created_at == 1000
    assert ea.expires_at == 2000

def test_evidence_artifact_invalid_expires():
    with pytest.raises(ValueError, match="expires_at cannot precede created_at"):
        EvidenceArtifact(
            artifact_id="evidence://ev_1234abcd",
            source_tool_name="tool_call_xyz",
            content_length=1024,
            sha256_hex="a" * 64,
            created_at=2000,
            expires_at=1000,
        )

def test_evidence_artifact_invalid_sha():
    with pytest.raises(ValueError, match="sha256_hex must be a lowercase SHA-256 hex digest"):
        EvidenceArtifact(
            artifact_id="evidence://ev_1234abcd",
            source_tool_name="tool_call_xyz",
            content_length=1024,
            sha256_hex="invalid",
            created_at=1000,
            expires_at=2000,
        )

def test_evidence_artifact_invalid_length():
    with pytest.raises(ValueError, match="content_length must be a nonnegative integer"):
        EvidenceArtifact(
            artifact_id="evidence://ev_1234abcd",
            source_tool_name="tool_call_xyz",
            content_length=-1,
            sha256_hex="a" * 64,
            created_at=1000,
            expires_at=2000,
        )
