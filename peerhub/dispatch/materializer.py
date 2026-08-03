"""Stateful file-I/O layer for artifact materialization (Slice 5-A).

Implements the ``ArtifactMaterializer`` contract ratified in
``docs/design/SLICE5-KICKOFF-R1.md``
("ArtifactMaterializer contract RATIFIED (2026-08-03, ag+cx unanimous)").

Ownership boundary:
    - Materializer owns DECLARED → STAGED → VERIFIED only.
    - Physical deletion (CLEANED) belongs to a separate async GC pass.

Safety invariants:
    1. ``manifest_digest`` is derived inside ``materialize()`` from durable
       immutable facts only -- never accepted from a caller.
    2. Staging isolation: write to ``<target>.tmp.<uuid>``, atomic rename
       only after verification.
    3. On handled errors, delete ONLY the materializer's own ``.tmp.<uuid>``
       file -- never pre-existing or unproven files.
    4. Never persist absolute paths, source paths, or raw bytes.
"""

from __future__ import annotations

import enum
import hashlib
import json
import logging
import os
import pathlib
import time
import uuid
from dataclasses import dataclass
from collections.abc import Mapping
from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from peerhub.persistence.sqlite import SqliteUnitOfWork

from peerhub.dispatch.contract import (
    ArtifactMetadata,
    ArtifactState,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class MaterializationSource(enum.Enum):
    """How the materializer obtains bytes."""

    BYTES_INLINE = "bytes_inline"
    # Future: BLOB_REF, STREAM, etc. -- only BYTES_INLINE for Slice 5.


class MaterializationStatus(enum.Enum):
    """Outcome status of a single ``materialize()`` call."""

    SUCCESS = "success"
    CONFLICT_WINNER = "conflict_winner"
    HARD_FAILURE = "hard_failure"
    RETRYABLE_FAILURE = "retryable_failure"


@dataclass(frozen=True)
class MaterializationItemRequest:
    """Immutable description of *what* to materialize and *where*.

    ``target_path`` is workspace-relative (``PurePosixPath``).
    The materializer resolves it against ``workspace_root`` internally.

    Renamed from ``MaterializationManifest`` to avoid collision with the
    aggregate ``MaterializationManifest`` in ``artifacts.py`` — this is a
    per-artifact request shape, not the full-invocation aggregate.
    """

    artifact_id: str  # opaque, caller-assigned; "" for zero-artifact
    source: MaterializationSource
    target_path: pathlib.PurePosixPath  # workspace-relative, never absolute
    expected_digest: str  # "sha256:<hex>"
    expected_length: int  # bytes

    # Persistence identity fields carried through from the artifact row.
    attempt_id: str = ""
    placeholder: str = ""
    workspace_scope_id: str = ""
    staging_ref: str = ""
    access_mode: str = "READ_WRITE"
    declared_lifecycle: str = "EPHEMERAL"


# Back-compat alias — existing code importing the old name continues to work.
MaterializationManifest = MaterializationItemRequest


@dataclass(frozen=True)
class MaterializationResult:
    """Outcome of a ``materialize()`` call."""

    artifact_id: str
    target_path: pathlib.PurePosixPath
    status: MaterializationStatus
    manifest_digest: str  # digest of the manifest itself (derived inside materialize)
    verified_digest: str | None  # digest of the file on disk after verification
    verified_length: int | None
    attempt_id: str  # uuid4 for this attempt
    error: str | None  # human-readable, only on failure


# ---------------------------------------------------------------------------
# Digest helpers
# ---------------------------------------------------------------------------


def compute_manifest_digest(manifest: MaterializationItemRequest) -> str:
    """Derive a deterministic SHA-256 digest from manifest immutable facts.

    Canonical JSON serialisation of the identity fields, then SHA-256.
    Never accepted from the caller -- always computed inside ``materialize()``.
    """
    identity = {
        "artifact_id": manifest.artifact_id,
        "source": manifest.source.value,
        "target_path": str(manifest.target_path),
        "expected_digest": manifest.expected_digest,
        "expected_length": manifest.expected_length,
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_file_digest(file_path: pathlib.Path) -> tuple[str, int]:
    """Reopen a file and compute SHA-256 digest and byte length.

    Returns:
        (``"sha256:<hex>"``, byte_length)
    """
    h = hashlib.sha256()
    length = 0
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
            length += len(chunk)
    return f"sha256:{h.hexdigest()}", length


def _now_epoch_ms() -> int:
    """Current time as milliseconds-since-epoch integer (matches repo timestamps)."""
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# ArtifactMaterializer
# ---------------------------------------------------------------------------


class ArtifactMaterializer:
    """Stateful file-I/O layer for artifact materialization.

    Owns the DECLARED → STAGED → VERIFIED lifecycle.
    Does NOT own physical deletion (CLEANED) -- that belongs to the GC pass.

    Parameters:
        unit_of_work_factory: callable returning a ``SqliteUnitOfWork`` context
            manager (typically ``store.unit_of_work``).
        workspace_root: absolute path to the workspace root directory.
        clock: optional callable returning integer timestamps for repo methods.
            Defaults to ``_now_epoch_ms``.
    """

    def __init__(
        self,
        unit_of_work_factory: Callable[[], "SqliteUnitOfWork"],
        workspace_root: pathlib.Path,
        *,
        clock: Callable[[], int] | None = None,
    ) -> None:
        if not workspace_root.is_absolute():
            raise ValueError("workspace_root must be an absolute path")
        self._uow_factory = unit_of_work_factory
        self._workspace_root = workspace_root
        self._clock = clock or _now_epoch_ms

    def materialize(
        self,
        manifest: MaterializationItemRequest,
        content_provider: Callable[[], bytes],
    ) -> MaterializationResult:
        """Materialize a single artifact to disk following the ratified protocol.

        For a zero-artifact manifest (``artifact_id == ""``), returns a no-op
        success immediately with no repository interaction.

        For a real artifact:
        1. Crash-recovery check (DECLARED + existing file on disk).
        2. Staging isolation (write to ``.tmp.<uuid>``, then atomic rename).
        3. State machine transitions via narrow typed repository methods.
        4. CAS-conflict handling on concurrent materialization.
        """
        attempt_id = str(uuid.uuid4())

        # ------------------------------------------------------------------
        # §1.9 Zero-artifact manifest
        # ------------------------------------------------------------------
        if manifest.artifact_id == "":
            return MaterializationResult(
                artifact_id="",
                target_path=manifest.target_path,
                status=MaterializationStatus.SUCCESS,
                manifest_digest="",
                verified_digest=None,
                verified_length=None,
                attempt_id=attempt_id,
                error=None,
            )

        # ------------------------------------------------------------------
        # Compute manifest_digest from immutable facts (§1.2 invariant 1)
        # ------------------------------------------------------------------
        manifest_digest = compute_manifest_digest(manifest)

        # ------------------------------------------------------------------
        # Resolve paths (§1.2 invariant 2: target_path is workspace-relative)
        # ------------------------------------------------------------------
        abs_target = self._workspace_root / manifest.target_path
        staging_suffix = f".tmp.{attempt_id}"
        staging_path = abs_target.parent / (abs_target.name + staging_suffix)

        # Workspace-relative paths for persistence (never persist absolutes)
        target_path_relative = str(manifest.target_path)
        staging_path_relative = str(
            pathlib.PurePosixPath(staging_path.relative_to(self._workspace_root))
        )

        # ------------------------------------------------------------------
        # §1.6 Crash-recovery: DECLARED + existing file on disk
        # ------------------------------------------------------------------
        if abs_target.exists():
            recovery_result = self._attempt_crash_recovery(
                manifest=manifest,
                abs_target=abs_target,
                attempt_id=attempt_id,
                manifest_digest=manifest_digest,
                target_path_relative=target_path_relative,
                staging_path_relative=staging_path_relative,
            )
            if recovery_result is not None:
                return recovery_result

        # ------------------------------------------------------------------
        # §1.3 Staging isolation protocol
        # ------------------------------------------------------------------

        # Step 3-4: Write content to staging file
        try:
            content = content_provider()
        except Exception as exc:
            # Failure-mode table: Source missing → HARD_FAILURE
            return MaterializationResult(
                artifact_id=manifest.artifact_id,
                target_path=manifest.target_path,
                status=MaterializationStatus.HARD_FAILURE,
                manifest_digest=manifest_digest,
                verified_digest=None,
                verified_length=None,
                attempt_id=attempt_id,
                error=f"content_provider raised: {exc}",
            )

        try:
            abs_target.parent.mkdir(parents=True, exist_ok=True)
            staging_path.parent.mkdir(parents=True, exist_ok=True)
        except PermissionError as exc:
            # Failure-mode table: Permission denied (staging dir) → HARD_FAILURE
            return MaterializationResult(
                artifact_id=manifest.artifact_id,
                target_path=manifest.target_path,
                status=MaterializationStatus.HARD_FAILURE,
                manifest_digest=manifest_digest,
                verified_digest=None,
                verified_length=None,
                attempt_id=attempt_id,
                error=f"permission denied creating directories: {exc}",
            )

        try:
            with open(staging_path, "wb") as f:
                f.write(content)
        except OSError as exc:
            # Failure-mode table: ENOSPC/quota → RETRYABLE; PermissionError → HARD
            self._safe_unlink(staging_path)
            if isinstance(exc, PermissionError):
                status = MaterializationStatus.HARD_FAILURE
            else:
                status = MaterializationStatus.RETRYABLE_FAILURE
            return MaterializationResult(
                artifact_id=manifest.artifact_id,
                target_path=manifest.target_path,
                status=status,
                manifest_digest=manifest_digest,
                verified_digest=None,
                verified_length=None,
                attempt_id=attempt_id,
                error=f"write failed: {exc}",
            )

        # ------------------------------------------------------------------
        # Step 6: DECLARED → STAGED (CAS-guarded)
        # ------------------------------------------------------------------
        staged_ok = False
        try:
            with self._uow_factory() as uow:
                current_meta = uow.get_artifact_metadata(
                    manifest.attempt_id, manifest.artifact_id
                )
                if current_meta is None:
                    uow.rollback()
                    self._safe_unlink(staging_path)
                    return MaterializationResult(
                        artifact_id=manifest.artifact_id,
                        target_path=manifest.target_path,
                        status=MaterializationStatus.HARD_FAILURE,
                        manifest_digest=manifest_digest,
                        verified_digest=None,
                        verified_length=None,
                        attempt_id=attempt_id,
                        error="artifact metadata not found",
                    )

                staged_ok = uow.mark_artifact_staged(
                    attempt_id=manifest.attempt_id,
                    artifact_id=manifest.artifact_id,
                    staging_path_relative=staging_path_relative,
                    expected_revision=current_meta.revision,
                    staged_at=self._clock(),
                )
                if not staged_ok:
                    # CAS loss — handle per §1.5
                    uow.rollback()
                    return self._handle_cas_loss(
                        manifest=manifest,
                        abs_target=abs_target,
                        staging_path=staging_path,
                        attempt_id=attempt_id,
                        manifest_digest=manifest_digest,
                    )
                uow.commit()
        except Exception:
            # CAS loss or other DB error
            self._safe_unlink(staging_path)
            return self._handle_cas_loss(
                manifest=manifest,
                abs_target=abs_target,
                staging_path=staging_path,
                attempt_id=attempt_id,
                manifest_digest=manifest_digest,
            )

        # ------------------------------------------------------------------
        # Steps 7-8: Verification (reopen + hash round-trip)
        # ------------------------------------------------------------------
        verified_digest, verified_length = compute_file_digest(staging_path)

        if (
            verified_digest != manifest.expected_digest
            or verified_length != manifest.expected_length
        ):
            # Failure-mode table: Digest/length mismatch → HARD_FAILURE
            self._safe_unlink(staging_path)
            return MaterializationResult(
                artifact_id=manifest.artifact_id,
                target_path=manifest.target_path,
                status=MaterializationStatus.HARD_FAILURE,
                manifest_digest=manifest_digest,
                verified_digest=verified_digest,
                verified_length=verified_length,
                attempt_id=attempt_id,
                error=(
                    f"digest mismatch: expected {manifest.expected_digest}/{manifest.expected_length}, "
                    f"got {verified_digest}/{verified_length}"
                ),
            )

        # Step 8: Atomic rename staging → final target
        try:
            os.replace(str(staging_path), str(abs_target))
        except PermissionError as exc:
            # Failure-mode table: Permission denied (rename) → HARD_FAILURE
            self._safe_unlink(staging_path)
            return MaterializationResult(
                artifact_id=manifest.artifact_id,
                target_path=manifest.target_path,
                status=MaterializationStatus.HARD_FAILURE,
                manifest_digest=manifest_digest,
                verified_digest=verified_digest,
                verified_length=verified_length,
                attempt_id=attempt_id,
                error=f"atomic rename failed: {exc}",
            )

        # ------------------------------------------------------------------
        # Step 9: STAGED → VERIFIED (CAS-guarded)
        # ------------------------------------------------------------------
        try:
            with self._uow_factory() as uow:
                current_meta = uow.get_artifact_metadata(
                    manifest.attempt_id, manifest.artifact_id
                )
                if current_meta is None:
                    uow.rollback()
                    return MaterializationResult(
                        artifact_id=manifest.artifact_id,
                        target_path=manifest.target_path,
                        status=MaterializationStatus.RETRYABLE_FAILURE,
                        manifest_digest=manifest_digest,
                        verified_digest=verified_digest,
                        verified_length=verified_length,
                        attempt_id=attempt_id,
                        error="artifact metadata not found for verification",
                    )

                verified_ok = uow.mark_artifact_verified(
                    attempt_id=manifest.attempt_id,
                    artifact_id=manifest.artifact_id,
                    verified_digest=verified_digest,
                    verified_length=verified_length,
                    target_path_relative=target_path_relative,
                    expected_revision=current_meta.revision,
                    verified_at=self._clock(),
                )
                if not verified_ok:
                    # CAS loss on verification → RETRYABLE_FAILURE per §1.7
                    uow.rollback()
                    return MaterializationResult(
                        artifact_id=manifest.artifact_id,
                        target_path=manifest.target_path,
                        status=MaterializationStatus.RETRYABLE_FAILURE,
                        manifest_digest=manifest_digest,
                        verified_digest=verified_digest,
                        verified_length=verified_length,
                        attempt_id=attempt_id,
                        error="CAS conflict during verification transition",
                    )
                uow.commit()
        except Exception as exc:
            return MaterializationResult(
                artifact_id=manifest.artifact_id,
                target_path=manifest.target_path,
                status=MaterializationStatus.RETRYABLE_FAILURE,
                manifest_digest=manifest_digest,
                verified_digest=verified_digest,
                verified_length=verified_length,
                attempt_id=attempt_id,
                error=f"verification transition failed: {exc}",
            )

        # ------------------------------------------------------------------
        # Step 10: Success
        # ------------------------------------------------------------------
        return MaterializationResult(
            artifact_id=manifest.artifact_id,
            target_path=manifest.target_path,
            status=MaterializationStatus.SUCCESS,
            manifest_digest=manifest_digest,
            verified_digest=verified_digest,
            verified_length=verified_length,
            attempt_id=attempt_id,
            error=None,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _attempt_crash_recovery(
        self,
        *,
        manifest: MaterializationItemRequest,
        abs_target: pathlib.Path,
        attempt_id: str,
        manifest_digest: str,
        target_path_relative: str,
        staging_path_relative: str,
    ) -> MaterializationResult | None:
        """§1.6: DECLARED + existing file → try to recover.

        Returns a ``MaterializationResult`` if recovery succeeds or the
        artifact is in a non-recoverable state; returns ``None`` if the
        existing file was corrupt and was unlinked (caller should re-stage).
        """
        with self._uow_factory() as uow:
            meta = uow.get_artifact_metadata(
                manifest.attempt_id, manifest.artifact_id
            )
            if meta is None:
                # No metadata row — can't recover, proceed with staging
                uow.rollback()
                return None

            if meta.state == ArtifactState.DECLARED:
                # DECLARED + file exists → verify the existing file
                file_digest, file_length = compute_file_digest(abs_target)

                if (
                    file_digest == manifest.expected_digest
                    and file_length == manifest.expected_length
                ):
                    # File verifies — skip re-staging, go straight to VERIFIED
                    staged_ok = uow.mark_artifact_staged(
                        attempt_id=manifest.attempt_id,
                        artifact_id=manifest.artifact_id,
                        staging_path_relative=staging_path_relative,
                        expected_revision=meta.revision,
                        staged_at=self._clock(),
                    )
                    if not staged_ok:
                        uow.rollback()
                        return None

                    # Re-read to get updated revision
                    meta_staged = uow.get_artifact_metadata(
                        manifest.attempt_id, manifest.artifact_id
                    )
                    if meta_staged is None:
                        uow.rollback()
                        return None

                    verified_ok = uow.mark_artifact_verified(
                        attempt_id=manifest.attempt_id,
                        artifact_id=manifest.artifact_id,
                        verified_digest=file_digest,
                        verified_length=file_length,
                        target_path_relative=target_path_relative,
                        expected_revision=meta_staged.revision,
                        verified_at=self._clock(),
                    )
                    if not verified_ok:
                        uow.rollback()
                        return None

                    uow.commit()
                    return MaterializationResult(
                        artifact_id=manifest.artifact_id,
                        target_path=manifest.target_path,
                        status=MaterializationStatus.SUCCESS,
                        manifest_digest=manifest_digest,
                        verified_digest=file_digest,
                        verified_length=file_length,
                        attempt_id=attempt_id,
                        error=None,
                    )
                else:
                    # File corrupt — unlink and re-stage
                    uow.rollback()
                    self._safe_unlink(abs_target)
                    return None  # Caller will proceed with normal staging

            elif meta.state == ArtifactState.VERIFIED:
                # Already VERIFIED — verify on disk and return winner
                uow.rollback()
                return self._return_winner_if_valid(
                    manifest=manifest,
                    abs_target=abs_target,
                    attempt_id=attempt_id,
                    manifest_digest=manifest_digest,
                    meta=meta,
                )

            else:
                # STAGED, RESERVED, etc. — not our recovery territory
                uow.rollback()
                return MaterializationResult(
                    artifact_id=manifest.artifact_id,
                    target_path=manifest.target_path,
                    status=MaterializationStatus.RETRYABLE_FAILURE,
                    manifest_digest=manifest_digest,
                    verified_digest=None,
                    verified_length=None,
                    attempt_id=attempt_id,
                    error=f"artifact in non-recoverable state: {meta.state.value}",
                )

    def _handle_cas_loss(
        self,
        *,
        manifest: MaterializationItemRequest,
        abs_target: pathlib.Path,
        staging_path: pathlib.Path,
        attempt_id: str,
        manifest_digest: str,
    ) -> MaterializationResult:
        """§1.5: On CAS loss, re-read and check the winner."""
        # Always clean up our own staging file
        self._safe_unlink(staging_path)

        with self._uow_factory() as uow:
            meta = uow.get_artifact_metadata(
                manifest.attempt_id, manifest.artifact_id
            )
            uow.rollback()

        if meta is None:
            return MaterializationResult(
                artifact_id=manifest.artifact_id,
                target_path=manifest.target_path,
                status=MaterializationStatus.RETRYABLE_FAILURE,
                manifest_digest=manifest_digest,
                verified_digest=None,
                verified_length=None,
                attempt_id=attempt_id,
                error="CAS conflict, artifact metadata not found after re-read",
            )

        return self._return_winner_if_valid(
            manifest=manifest,
            abs_target=abs_target,
            attempt_id=attempt_id,
            manifest_digest=manifest_digest,
            meta=meta,
        )

    def _return_winner_if_valid(
        self,
        *,
        manifest: MaterializationItemRequest,
        abs_target: pathlib.Path,
        attempt_id: str,
        manifest_digest: str,
        meta: ArtifactMetadata,
    ) -> MaterializationResult:
        """Check if the winner's result is valid and return CONFLICT_WINNER or RETRYABLE."""
        if meta.state != ArtifactState.VERIFIED:
            return MaterializationResult(
                artifact_id=manifest.artifact_id,
                target_path=manifest.target_path,
                status=MaterializationStatus.RETRYABLE_FAILURE,
                manifest_digest=manifest_digest,
                verified_digest=None,
                verified_length=None,
                attempt_id=attempt_id,
                error=(
                    f"CAS conflict, winner state/digest mismatch: "
                    f"state={meta.state.value}"
                ),
            )

        # Winner is VERIFIED — verify the file still exists and matches on disk
        if not abs_target.exists():
            return MaterializationResult(
                artifact_id=manifest.artifact_id,
                target_path=manifest.target_path,
                status=MaterializationStatus.RETRYABLE_FAILURE,
                manifest_digest=manifest_digest,
                verified_digest=None,
                verified_length=None,
                attempt_id=attempt_id,
                error="CAS conflict, winner's file missing from disk",
            )

        try:
            disk_digest, disk_length = compute_file_digest(abs_target)
        except OSError as exc:
            return MaterializationResult(
                artifact_id=manifest.artifact_id,
                target_path=manifest.target_path,
                status=MaterializationStatus.RETRYABLE_FAILURE,
                manifest_digest=manifest_digest,
                verified_digest=None,
                verified_length=None,
                attempt_id=attempt_id,
                error=f"CAS conflict, cannot verify winner's file: {exc}",
            )

        if (
            disk_digest == manifest.expected_digest
            and disk_length == manifest.expected_length
        ):
            # Winner is valid — idempotent success
            return MaterializationResult(
                artifact_id=manifest.artifact_id,
                target_path=manifest.target_path,
                status=MaterializationStatus.CONFLICT_WINNER,
                manifest_digest=manifest_digest,
                verified_digest=disk_digest,
                verified_length=disk_length,
                attempt_id=attempt_id,
                error=None,
            )
        else:
            return MaterializationResult(
                artifact_id=manifest.artifact_id,
                target_path=manifest.target_path,
                status=MaterializationStatus.RETRYABLE_FAILURE,
                manifest_digest=manifest_digest,
                verified_digest=disk_digest,
                verified_length=disk_length,
                attempt_id=attempt_id,
                error=(
                    f"CAS conflict, winner's file identity mismatch: "
                    f"expected {manifest.expected_digest}/{manifest.expected_length}, "
                    f"got {disk_digest}/{disk_length}"
                ),
            )

    @staticmethod
    def _safe_unlink(path: pathlib.Path) -> None:
        """Unlink a file, ignoring FileNotFoundError.

        §1.8 rollback scope: only delete files we own (.tmp.<uuid>).
        """
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("Failed to unlink staging file: %s", path, exc_info=True)

    def materialize_manifest(
        self,
        manifest: "_ArtifactsManifest",
        content_providers: Mapping[str, Callable[[], bytes]],
    ) -> tuple[MaterializationResult, ...]:
        """Materialize all items from an aggregate ``MaterializationManifest``.

        Parameters
        ----------
        manifest:
            The aggregate manifest from ``artifacts.py`` containing all
            ``MaterializationItem`` entries for a single attempt.
        content_providers:
            Mapping from ``artifact_id`` to a ``Callable[[], bytes]``
            that returns the artifact content.  Each key must match
            an item's ``artifact_id`` in ``manifest.items``.

        Returns
        -------
        tuple[MaterializationResult, ...]
            One result per ``manifest.items`` entry, in the same order.
        """
        results: list[MaterializationResult] = []
        for item in manifest.items:
            item_request = MaterializationItemRequest(
                artifact_id=item.artifact_id,
                source=MaterializationSource.BYTES_INLINE,
                target_path=pathlib.PurePosixPath(
                    item.staging_path.relative_to(self._workspace_root)
                ),
                expected_digest=(
                    f"sha256:{item.sha256_hex}"
                    if not item.sha256_hex.startswith("sha256:")
                    else item.sha256_hex
                ),
                expected_length=item.expected_length,
                attempt_id=manifest.attempt_id,
                placeholder=item.placeholder,
                workspace_scope_id=manifest.workspace.scope_id,
                staging_ref=str(item.staging_path),
                access_mode=item.access_mode,
                declared_lifecycle=item.lifecycle,
            )
            provider = content_providers.get(item.artifact_id)
            if provider is None:
                results.append(
                    MaterializationResult(
                        artifact_id=item.artifact_id,
                        target_path=item_request.target_path,
                        status=MaterializationStatus.HARD_FAILURE,
                        manifest_digest="",
                        verified_digest=None,
                        verified_length=None,
                        attempt_id=manifest.attempt_id,
                        error=f"no content provider for artifact_id={item.artifact_id!r}",
                    )
                )
                continue
            results.append(self.materialize(item_request, provider))
        return tuple(results)


# Late import to avoid circular dependency — only used by
# materialize_manifest's type annotation (string-quoted above).
from peerhub.dispatch.artifacts import MaterializationManifest as _ArtifactsManifest  # noqa: E402
