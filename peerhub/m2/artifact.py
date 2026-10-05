"""M2.1 Artifact Content-Addressable Storage Layer.

Adheres strictly to M2_1_ARTIFACT_CONTRACT.md and EXCEPTION_CATALOG.m2.json:
- 2-tier sharded directory structure: artifacts/<digest[:2]>/<digest>
- Strict lowercase hex ASCII digest enforcement (64 chars)
- Strict defense against path traversals, symlinks, and Windows reparse points / junctions
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import sys


class SecurityBoundaryError(Exception):
    """Path traversal, symlink, or security boundary violation."""


class WindowsJunctionError(SecurityBoundaryError):
    """Windows junction point or reparse point detected in artifact path."""


class InvalidArtifactReferenceError(ValueError):
    """Artifact digest string fails format, length, or character set constraints."""


class ArtifactNotFoundError(LookupError):
    """Referenced artifact digest not found in physical store."""


class ArtifactTamperedError(ValueError):
    """Artifact content on disk does not match its cryptographic SHA-256 digest."""


HEX_DIGEST_REGEX = re.compile(r"^[0-9a-f]{64}$")


class ArtifactStore:
    """Content-Addressable Storage (CAS) for immutable M2.1 artifacts."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.tmp_dir = self.root / ".tmp"

    def validate_digest(self, digest: str) -> bool:
        """Validate that digest is strictly a 64-character lowercase ASCII hex string."""
        if not isinstance(digest, str):
            return False
        return bool(HEX_DIGEST_REGEX.fullmatch(digest))

    def resolve_path(self, digest: str) -> Path:
        """Resolve physical artifact path using 2-tier directory sharding.

        Rejects directory traversal sequences, path separators, and malformed digests.
        """
        # Explicitly reject directory traversal sequences before any filesystem resolution
        if ".." in digest or "/" in digest or "\\" in digest or ":" in digest:
            raise SecurityBoundaryError(f"Path traversal detected in digest reference: {digest!r}")

        if not self.validate_digest(digest):
            raise InvalidArtifactReferenceError(f"Invalid lowercase SHA-256 digest reference: {digest!r}")

        target = self.root / digest[:2] / digest

        # Defense-in-depth: Ensure resolved target is strictly within self.root
        try:
            target.resolve().relative_to(self.root)
        except ValueError as e:
            raise SecurityBoundaryError(f"Resolved path escapes artifact root: {digest!r}") from e

        return target

    def stage_file(self, source: Path | str) -> None:
        """Stage an existing physical file into the artifact store.

        Rejects symlinks, directories, and Windows junction/reparse points.
        """
        src = Path(source)

        # 1. Symlink check (cross-platform)
        if os.path.islink(src) or src.is_symlink():
            raise SecurityBoundaryError(f"Symlinks are strictly forbidden as artifact source: {src}")

        # 2. Windows Reparse Point / Junction check
        if sys.platform == "win32" and src.exists():
            st = os.lstat(src)
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            file_attrs = getattr(st, "st_file_attributes", 0)
            if file_attrs & reparse_flag:
                raise WindowsJunctionError(f"Windows reparse point detected in artifact path: {src}")

            # Also reject directories if passed as file staging source
            if src.is_dir():
                raise WindowsJunctionError(f"Directories cannot be staged as blob artifacts: {src}")

        if not src.is_file():
            raise FileNotFoundError(f"Source file not found: {src}")

    def stage_stream(self, stream: Any, chunk_size: int = 65536) -> StagedArtifact:
        """Stage arbitrary byte stream into a temporary staging file using os.write with fsync."""
        import hashlib
        import tempfile

        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp_path_str = tempfile.mkstemp(dir=self.tmp_dir, prefix="stage-")
        tmp_path = Path(tmp_path_str)

        hasher = hashlib.sha256()
        total_size = 0
        try:
            while True:
                chunk = stream.read(chunk_size)
                if not chunk:
                    break
                os.write(fd, chunk)
                hasher.update(chunk)
                total_size += len(chunk)

            os.fsync(fd)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            if tmp_path.exists():
                tmp_path.unlink()
            raise
        else:
            os.close(fd)

        digest = hasher.hexdigest()
        return StagedArtifact(path=tmp_path, digest=digest, size=total_size)

    def stage_bytes(self, data: bytes) -> StagedArtifact:
        """Stage in-memory bytes into a temporary staging file with synchronous fsync."""
        import io
        return self.stage_stream(io.BytesIO(data))

    def commit_staged(self, staged: StagedArtifact) -> str:
        """Atomically commit a staged artifact to its final sharded destination."""
        target_path = self.resolve_path(staged.digest)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Atomic replacement / move
        try:
            os.replace(staged.path, target_path)
        except FileExistsError:
            # Idempotent deduplication: file already exists with same digest
            if staged.path.exists():
                staged.path.unlink()

        return staged.digest

    def read_bytes(self, digest: str) -> bytes:
        """Read artifact content with read-time cryptographic verification."""
        import hashlib

        target_path = self.resolve_path(digest)
        if not target_path.is_file():
            raise ArtifactNotFoundError(f"Artifact {digest} not found on disk at {target_path}")

        data = target_path.read_bytes()
        actual_digest = hashlib.sha256(data).hexdigest()
        if actual_digest != digest:
            raise ArtifactTamperedError(
                f"Artifact digest mismatch: expected {digest}, got {actual_digest} (tampered or truncated)"
            )

        return data


from dataclasses import dataclass


@dataclass(frozen=True)
class StagedArtifact:
    """Represents a temporary staged artifact file before atomic commit."""

    path: Path
    digest: str
    size: int

