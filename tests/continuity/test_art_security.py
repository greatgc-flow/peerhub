"""Wave 0: ART-005..ART-008 - Artifact Path Traversal, Symlinks, Reparse Points, and Canonical Digest Security.

Verifies:
- ART-005: Path traversal sequences ('..') in artifact digest reference are strictly rejected before filesystem access.
- ART-006: Symlinks pointing outside the workspace boundary are strictly rejected (SecurityBoundaryError).
- ART-007: Windows junction points and reparse points are rejected safely.
- ART-008: Strict lowercase hex ASCII digest enforcement prevents Unicode normalization (NFC/NFD) collisions.
"""

from pathlib import Path
import stat
import sys
import pytest

from peerhub.extensions.artifact import (  # type: ignore[import-not-found]
    ArtifactStore,
    SecurityBoundaryError,
    WindowsJunctionError,
    InvalidArtifactReferenceError,
)


@pytest.mark.security
def test_art_005_digest_path_traversal_strictly_rejected(tmp_path):
    """ART-005: Malicious artifact references with '..' or path separators raise InvalidArtifactReferenceError."""
    store = ArtifactStore(tmp_path / "artifacts")

    malicious_refs = [
        "../escaped_file",
        "..\\escaped_file",
        "/etc/passwd",
        "C:\\Windows\\system32",
        "da/39/a3ee5e6b4b0d3255bfef95601890afd80709",
        "da39a3ee5e6b4b0d3255bfef95601890afd80709..",
        "short_digest",
    ]

    for bad_ref in malicious_refs:
        with pytest.raises((InvalidArtifactReferenceError, SecurityBoundaryError)):
            store.resolve_path(bad_ref)


@pytest.mark.security
def test_art_006_symlink_payload_rejected_as_security_violation(tmp_path):
    """ART-006: Staging an artifact from an OS symlink raises SecurityBoundaryError."""
    artifacts_root = tmp_path / "artifacts"
    store = ArtifactStore(artifacts_root)

    secret_target = tmp_path / "secret.txt"
    secret_target.write_text("sensitive data", encoding="utf-8")

    symlink_path = tmp_path / "symlink_source"
    try:
        symlink_path.symlink_to(secret_target)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation requires elevated permissions on this Windows host")

    with pytest.raises(SecurityBoundaryError):
        store.stage_file(symlink_path)


@pytest.mark.security
def test_art_007_windows_reparse_point_rejected(tmp_path, monkeypatch):
    """ART-007: Windows junction/reparse points are detected and aborted before staging (simulated on every platform)."""
    import os
    import types

    from peerhub.extensions import artifact as artifact_module

    store = ArtifactStore(tmp_path / "artifacts")
    target = tmp_path / "junction_dir"
    target.mkdir()
    real_lstat = os.lstat

    class _Reparse:  # a real stat result that additionally carries the Windows reparse-point attribute
        def __init__(self, st):
            self._st = st
            self.st_file_attributes = stat.FILE_ATTRIBUTE_REPARSE_POINT if hasattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT") else 0x400

        def __getattr__(self, name):
            return getattr(self._st, name)

    monkeypatch.setattr(artifact_module, "sys", types.SimpleNamespace(platform="win32"))
    monkeypatch.setattr(artifact_module.os, "lstat", lambda p, *a, **k: _Reparse(real_lstat(p, *a, **k)))
    with pytest.raises((WindowsJunctionError, SecurityBoundaryError)):
        store.stage_file(target)


@pytest.mark.security
def test_art_008_canonical_lowercase_ascii_hex_enforced():
    """ART-008: Strict lowercase hex ASCII digest enforcement rejects uppercase, non-hex, or unicode variants."""
    store = ArtifactStore(Path("/fake/artifacts"))

    # Valid lowercase hex SHA-256 (64 chars)
    valid_digest = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert store.validate_digest(valid_digest) is True

    # Uppercase hex must be rejected
    assert store.validate_digest(valid_digest.upper()) is False

    # Mixed case must be rejected
    assert store.validate_digest(valid_digest[:32] + valid_digest[32:].upper()) is False

    # Unicode lookalikes / normalization variants (e.g. Cyrillic 'а' vs Latin 'a')
    cyrillic_lookalike = valid_digest.replace("a", "\u0430")
    assert store.validate_digest(cyrillic_lookalike) is False
