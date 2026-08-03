"""Unit tests for peerhub.dispatch.materializer (Slice 5-A).

Covers the ``ArtifactMaterializer`` contract ratified in
``docs/design/SLICE5-KICKOFF-R1.md``.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from peerhub.dispatch.contract import (
    ArtifactMetadata,
    ArtifactState,
)
from peerhub.dispatch.materializer import (
    ArtifactMaterializer,
    MaterializationItemRequest,
    MaterializationManifest,
    MaterializationResult,
    MaterializationSource,
    MaterializationStatus,
    compute_file_digest,
    compute_manifest_digest,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONTENT = b"hello world -- test artifact content"
_CONTENT_DIGEST = "sha256:" + hashlib.sha256(_CONTENT).hexdigest()
_CONTENT_LENGTH = len(_CONTENT)


def _make_manifest(
    artifact_id: str = "art-01",
    target_rel: str = "staging/out/test.dat",
    content: bytes = _CONTENT,
    attempt_id: str = "attempt-01",
) -> MaterializationManifest:
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    return MaterializationManifest(
        artifact_id=artifact_id,
        source=MaterializationSource.BYTES_INLINE,
        target_path=pathlib.PurePosixPath(target_rel),
        expected_digest=digest,
        expected_length=len(content),
        attempt_id=attempt_id,
        placeholder="__ART_01__",
        workspace_scope_id="ws-01",
        staging_ref="rel/staging/art-01.dat",
        access_mode="READ_WRITE",
        declared_lifecycle="EPHEMERAL",
    )


def _make_artifact_metadata(
    attempt_id: str = "attempt-01",
    artifact_id: str = "art-01",
    state: ArtifactState = ArtifactState.DECLARED,
    revision: int = 1,
    **overrides: Any,
) -> ArtifactMetadata:
    defaults = dict(
        attempt_id=attempt_id,
        artifact_id=artifact_id,
        placeholder="__ART_01__",
        workspace_scope_id="ws-01",
        staging_ref="rel/staging/art-01.dat",
        access_mode="READ_WRITE",
        declared_lifecycle="EPHEMERAL",
        state=state,
        declared_at=100,
        revision=revision,
    )
    defaults.update(overrides)
    return ArtifactMetadata(**defaults)


class FakeUnitOfWork:
    """In-memory fake of SqliteUnitOfWork for unit tests.

    Tracks artifact metadata rows by (attempt_id, artifact_id) and
    implements the narrow typed methods the materializer calls.
    """

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], ArtifactMetadata] = {}
        self._committed = False
        self._rolled_back = False

    def seed(self, meta: ArtifactMetadata) -> None:
        """Pre-seed a row for testing."""
        key = (meta.attempt_id, meta.artifact_id)
        self._rows[key] = meta

    def __enter__(self) -> "FakeUnitOfWork":
        self._committed = False
        self._rolled_back = False
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def commit(self) -> None:
        self._committed = True

    def rollback(self) -> None:
        self._rolled_back = True

    def get_artifact_metadata(
        self, attempt_id: str, artifact_id: str
    ) -> ArtifactMetadata | None:
        return self._rows.get((attempt_id, artifact_id))

    def mark_artifact_staged(
        self,
        *,
        attempt_id: str,
        artifact_id: str,
        staging_path_relative: str,
        expected_revision: int,
        staged_at: int,
    ) -> bool:
        key = (attempt_id, artifact_id)
        meta = self._rows.get(key)
        if meta is None:
            return False
        if meta.state != ArtifactState.DECLARED:
            return False
        if meta.revision != expected_revision:
            return False
        # Transition
        import dataclasses
        self._rows[key] = dataclasses.replace(
            meta,
            state=ArtifactState.STAGED,
            staging_ref=staging_path_relative,
            staged_at=staged_at,
            revision=meta.revision + 1,
        )
        return True

    def mark_artifact_verified(
        self,
        *,
        attempt_id: str,
        artifact_id: str,
        verified_digest: str,
        verified_length: int,
        target_path_relative: str,
        expected_revision: int,
        verified_at: int,
    ) -> bool:
        key = (attempt_id, artifact_id)
        meta = self._rows.get(key)
        if meta is None:
            return False
        if meta.state != ArtifactState.STAGED:
            return False
        if meta.revision != expected_revision:
            return False
        import dataclasses
        self._rows[key] = dataclasses.replace(
            meta,
            state=ArtifactState.VERIFIED,
            verified_sha256_hex=verified_digest,
            verified_length=verified_length,
            staging_ref=target_path_relative,
            verified_at=verified_at,
            revision=meta.revision + 1,
        )
        return True


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace root."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    return ws


@pytest.fixture
def fake_uow() -> FakeUnitOfWork:
    return FakeUnitOfWork()


def _make_materializer(
    workspace: Path,
    uow: FakeUnitOfWork,
    clock_value: int = 200,
) -> ArtifactMaterializer:
    """Build an ArtifactMaterializer backed by a FakeUnitOfWork."""
    return ArtifactMaterializer(
        unit_of_work_factory=lambda: uow,
        workspace_root=workspace,
        clock=lambda: clock_value,
    )


# =========================================================================
# Tests
# =========================================================================


class TestZeroArtifactManifest:
    """§1.9: Zero-artifact manifest → no-op success, no manifest row."""

    def test_zero_artifact_returns_success(self, workspace: Path) -> None:
        fake = FakeUnitOfWork()
        m = _make_materializer(workspace, fake)

        manifest = MaterializationManifest(
            artifact_id="",
            source=MaterializationSource.BYTES_INLINE,
            target_path=pathlib.PurePosixPath("unused"),
            expected_digest="sha256:unused",
            expected_length=0,
        )
        result = m.materialize(manifest, lambda: b"")

        assert result.status == MaterializationStatus.SUCCESS
        assert result.artifact_id == ""
        assert result.manifest_digest == ""
        assert result.verified_digest is None
        assert result.verified_length is None
        assert result.error is None
        # No rows should have been created
        assert len(fake._rows) == 0


class TestSuccessfulMaterialization:
    """Happy path: file written, verified, DECLARED→STAGED→VERIFIED."""

    def test_materialize_success(
        self, workspace: Path, fake_uow: FakeUnitOfWork
    ) -> None:
        manifest = _make_manifest()
        fake_uow.seed(_make_artifact_metadata())
        m = _make_materializer(workspace, fake_uow)

        result = m.materialize(manifest, lambda: _CONTENT)

        assert result.status == MaterializationStatus.SUCCESS
        assert result.verified_digest == _CONTENT_DIGEST
        assert result.verified_length == _CONTENT_LENGTH
        assert result.error is None
        assert result.manifest_digest != ""

        # Verify the file was written to the target path
        abs_target = workspace / manifest.target_path
        assert abs_target.exists()
        assert abs_target.read_bytes() == _CONTENT

        # Verify state transitions in the fake UoW
        meta = fake_uow.get_artifact_metadata("attempt-01", "art-01")
        assert meta is not None
        assert meta.state == ArtifactState.VERIFIED
        assert meta.verified_sha256_hex == _CONTENT_DIGEST
        assert meta.verified_length == _CONTENT_LENGTH
        assert meta.revision == 3  # 1 → 2 (staged) → 3 (verified)

    def test_manifest_digest_computed_deterministically(self) -> None:
        """manifest_digest is derived from immutable facts, never from caller."""
        m1 = _make_manifest(artifact_id="a1")
        m2 = _make_manifest(artifact_id="a1")

        d1 = compute_manifest_digest(m1)
        d2 = compute_manifest_digest(m2)
        assert d1 == d2
        assert d1.startswith("sha256:")

    def test_manifest_digest_differs_for_different_manifests(self) -> None:
        m1 = _make_manifest(artifact_id="a1")
        m2 = _make_manifest(artifact_id="a2")
        assert compute_manifest_digest(m1) != compute_manifest_digest(m2)


class TestDigestMismatch:
    """Digest mismatch after write → hard failure, no VERIFIED, tmp cleaned up."""

    def test_digest_mismatch_returns_hard_failure(
        self, workspace: Path, fake_uow: FakeUnitOfWork
    ) -> None:
        # Create a manifest expecting specific content, but provider returns different bytes
        manifest = _make_manifest(content=b"expected content")
        fake_uow.seed(_make_artifact_metadata())
        m = _make_materializer(workspace, fake_uow)

        # Provider returns different bytes than what the manifest expects
        wrong_content = b"different content entirely"
        result = m.materialize(manifest, lambda: wrong_content)

        assert result.status == MaterializationStatus.HARD_FAILURE
        assert "digest mismatch" in (result.error or "")

        # Verify no .tmp staging file remains
        staging_dir = workspace / "staging" / "out"
        if staging_dir.exists():
            tmp_files = [f for f in staging_dir.iterdir() if ".tmp." in f.name]
            assert len(tmp_files) == 0, f"Staging tmp files should be cleaned up: {tmp_files}"

        # Verify state did NOT advance to VERIFIED
        meta = fake_uow.get_artifact_metadata("attempt-01", "art-01")
        assert meta is not None
        assert meta.state != ArtifactState.VERIFIED


class TestCrashRecoveryDeclaredValidFile:
    """§1.6: DECLARED + existing valid file → skip re-write, go straight to VERIFIED."""

    def test_recovery_with_valid_existing_file(
        self, workspace: Path, fake_uow: FakeUnitOfWork
    ) -> None:
        manifest = _make_manifest()
        fake_uow.seed(_make_artifact_metadata(state=ArtifactState.DECLARED))

        # Pre-create the target file with valid content
        abs_target = workspace / manifest.target_path
        abs_target.parent.mkdir(parents=True, exist_ok=True)
        abs_target.write_bytes(_CONTENT)

        m = _make_materializer(workspace, fake_uow)
        result = m.materialize(manifest, lambda: _CONTENT)

        assert result.status == MaterializationStatus.SUCCESS
        assert result.verified_digest == _CONTENT_DIGEST
        assert result.verified_length == _CONTENT_LENGTH
        assert result.error is None

        # Verify state went to VERIFIED
        meta = fake_uow.get_artifact_metadata("attempt-01", "art-01")
        assert meta is not None
        assert meta.state == ArtifactState.VERIFIED


class TestCrashRecoveryDeclaredCorruptFile:
    """§1.6: DECLARED + existing corrupt file → unlink and re-stage."""

    def test_recovery_with_corrupt_existing_file(
        self, workspace: Path, fake_uow: FakeUnitOfWork
    ) -> None:
        manifest = _make_manifest()
        fake_uow.seed(_make_artifact_metadata(state=ArtifactState.DECLARED))

        # Pre-create the target file with CORRUPT content
        abs_target = workspace / manifest.target_path
        abs_target.parent.mkdir(parents=True, exist_ok=True)
        abs_target.write_bytes(b"corrupt data that does not match digest")

        m = _make_materializer(workspace, fake_uow)
        result = m.materialize(manifest, lambda: _CONTENT)

        # Should have unlinked the corrupt file and re-staged successfully
        assert result.status == MaterializationStatus.SUCCESS
        assert result.verified_digest == _CONTENT_DIGEST
        assert result.verified_length == _CONTENT_LENGTH
        assert result.error is None

        # Verify the file now has the correct content
        assert abs_target.read_bytes() == _CONTENT

        # Verify state went to VERIFIED
        meta = fake_uow.get_artifact_metadata("attempt-01", "art-01")
        assert meta is not None
        assert meta.state == ArtifactState.VERIFIED


class TestConcurrentMaterialization:
    """§1.5: CAS loss → re-read, return winner if valid."""

    def test_cas_loss_with_verified_winner(
        self, workspace: Path
    ) -> None:
        """Simulate: another materializer won the race and is now VERIFIED."""
        fake_uow = FakeUnitOfWork()

        manifest = _make_manifest()

        # Seed the artifact as already VERIFIED (winner finished first)
        fake_uow.seed(
            _make_artifact_metadata(
                state=ArtifactState.VERIFIED,
                revision=3,
                verified_sha256_hex=_CONTENT_DIGEST,
                verified_length=_CONTENT_LENGTH,
            )
        )

        # Also put the valid file on disk (winner wrote it)
        abs_target = workspace / manifest.target_path
        abs_target.parent.mkdir(parents=True, exist_ok=True)
        abs_target.write_bytes(_CONTENT)

        m = _make_materializer(workspace, fake_uow)

        # mark_artifact_staged will return False (CAS loss: state is VERIFIED, not DECLARED)
        result = m.materialize(manifest, lambda: _CONTENT)

        # The materializer should detect the winner and return CONFLICT_WINNER
        assert result.status == MaterializationStatus.CONFLICT_WINNER
        assert result.verified_digest == _CONTENT_DIGEST
        assert result.verified_length == _CONTENT_LENGTH
        assert result.error is None

    def test_cas_loss_winner_state_mismatch(
        self, workspace: Path
    ) -> None:
        """CAS loss where the winner is in STAGED (not VERIFIED) → RETRYABLE."""
        fake_uow = FakeUnitOfWork()

        manifest = _make_manifest()

        # Seed as STAGED — the winner hasn't finished verification yet
        fake_uow.seed(
            _make_artifact_metadata(
                state=ArtifactState.STAGED,
                revision=2,
            )
        )

        m = _make_materializer(workspace, fake_uow)
        result = m.materialize(manifest, lambda: _CONTENT)

        assert result.status == MaterializationStatus.RETRYABLE_FAILURE
        assert result.error is not None
        assert "state" in (result.error or "").lower() or "CAS" in (result.error or "")


class TestContentProviderFailure:
    """Source missing / content_provider raises → HARD_FAILURE."""

    def test_content_provider_exception(
        self, workspace: Path, fake_uow: FakeUnitOfWork
    ) -> None:
        manifest = _make_manifest()
        fake_uow.seed(_make_artifact_metadata())
        m = _make_materializer(workspace, fake_uow)

        def failing_provider() -> bytes:
            raise FileNotFoundError("source file missing")

        result = m.materialize(manifest, failing_provider)

        assert result.status == MaterializationStatus.HARD_FAILURE
        assert "content_provider raised" in (result.error or "")


class TestComputeFileDigest:
    """Verify compute_file_digest produces correct SHA-256."""

    def test_digest_of_known_content(self, tmp_path: Path) -> None:
        f = tmp_path / "test.dat"
        f.write_bytes(_CONTENT)

        digest, length = compute_file_digest(f)

        assert digest == _CONTENT_DIGEST
        assert length == _CONTENT_LENGTH

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.dat"
        f.write_bytes(b"")

        digest, length = compute_file_digest(f)

        expected = "sha256:" + hashlib.sha256(b"").hexdigest()
        assert digest == expected
        assert length == 0


class TestWorkspaceRootValidation:
    """workspace_root must be absolute."""

    def test_relative_workspace_root_raises(self) -> None:
        with pytest.raises(ValueError, match="absolute"):
            ArtifactMaterializer(
                unit_of_work_factory=lambda: MagicMock(),
                workspace_root=Path("relative/path"),
            )


class TestStagingFileCleanup:
    """Verify that staging .tmp files are cleaned up on failure."""

    def test_no_tmp_files_after_hard_failure(
        self, workspace: Path, fake_uow: FakeUnitOfWork
    ) -> None:
        """After a digest-mismatch hard failure, no .tmp files should remain."""
        manifest = _make_manifest(content=b"expected data")
        fake_uow.seed(_make_artifact_metadata())
        m = _make_materializer(workspace, fake_uow)

        result = m.materialize(manifest, lambda: b"wrong data")

        assert result.status == MaterializationStatus.HARD_FAILURE

        # Scan workspace for any .tmp files
        for p in workspace.rglob("*.tmp.*"):
            pytest.fail(f"Staging tmp file not cleaned up: {p}")


class TestMaterializationItemRequestRename:
    """Bug 2 regression: materializer.py's per-artifact type is now
    MaterializationItemRequest, distinct from artifacts.py's aggregate
    MaterializationManifest."""

    def test_item_request_is_distinct_from_artifacts_manifest(self) -> None:
        """The two types must be distinguishable at the type level."""
        from peerhub.dispatch.artifacts import (
            MaterializationManifest as ArtifactsManifest,
        )

        # MaterializationItemRequest is the per-artifact type
        assert MaterializationItemRequest is not ArtifactsManifest
        # Back-compat alias still resolves
        assert MaterializationManifest is MaterializationItemRequest

    def test_back_compat_alias_works(self) -> None:
        """The old name MaterializationManifest still works via alias."""
        m = MaterializationManifest(
            artifact_id="art-test",
            source=MaterializationSource.BYTES_INLINE,
            target_path=pathlib.PurePosixPath("out/test.dat"),
            expected_digest="sha256:abc",
            expected_length=10,
        )
        assert isinstance(m, MaterializationItemRequest)

    def test_materialize_accepts_item_request(self, workspace: Path) -> None:
        """materialize() accepts MaterializationItemRequest (the new name)."""
        fake = FakeUnitOfWork()
        fake.seed(_make_artifact_metadata())
        m = _make_materializer(workspace, fake)

        item_req = MaterializationItemRequest(
            artifact_id="art-01",
            source=MaterializationSource.BYTES_INLINE,
            target_path=pathlib.PurePosixPath("staging/out/test.dat"),
            expected_digest=_CONTENT_DIGEST,
            expected_length=_CONTENT_LENGTH,
            attempt_id="attempt-01",
            placeholder="__ART_01__",
            workspace_scope_id="ws-01",
            staging_ref="rel/staging/art-01.dat",
            access_mode="READ_WRITE",
            declared_lifecycle="EPHEMERAL",
        )

        result = m.materialize(item_req, lambda: _CONTENT)
        assert result.status == MaterializationStatus.SUCCESS
