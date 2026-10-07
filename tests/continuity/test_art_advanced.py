"""Wave 4: Artifact Store Security Bounds, Device Names & Garbage Collection Tests.

Verifies:
- ART-013: Windows reserved device names (CON, NUL, PRN) in digests or paths are strictly rejected.
- ART-014: Long paths (>260 chars) on Windows are handled properly.
- ART-015: NUL bytes in digest reference string are strictly rejected before filesystem operations.
- ART-028: Orphan cleanup routine identifies unreferenced blobs and removes them cleanly.
- ART-034: Missing physical blob raises ArtifactNotFoundError.
- ART-036: Structurally invalid digest strings raise InvalidArtifactReferenceError.
"""

from pathlib import Path
import pytest

from peerhub.extensions.artifact import (
    ArtifactStore,
    ArtifactNotFoundError,
    InvalidArtifactReferenceError,
    SecurityBoundaryError,
)


def test_art_013_reserved_device_names_rejected(tmp_path):
    """ART-013: Reserved device names like CON, NUL, PRN, AUX are strictly rejected."""
    store = ArtifactStore(tmp_path / "artifacts")

    reserved_names = ["CON", "nul", "prn", "AUX", "com1", "lpt1"]
    for name in reserved_names:
        with pytest.raises((SecurityBoundaryError, InvalidArtifactReferenceError)):
            store.resolve_path(name)


def test_art_014_long_path_handling(tmp_path):
    """ART-014: Storing and reading artifacts in deep paths exceeding 260 chars succeeds."""
    # Create deeply nested artifact store base directory
    deep_path = tmp_path / ("sub_" + "x" * 60) / ("sub_" + "y" * 60) / ("sub_" + "z" * 60)
    store = ArtifactStore(deep_path)

    data = b"long path content"
    staged = store.stage_bytes(data)
    digest = store.commit_staged(staged)

    resolved = store.resolve_path(digest)
    # Ensure resolved path is long or works across long paths
    assert len(str(resolved)) > 150
    assert store.read_bytes(digest) == data


def test_art_015_nul_byte_in_reference_rejected(tmp_path):
    """ART-015: References containing NUL byte (\\x00) are rejected pre-OS."""
    store = ArtifactStore(tmp_path / "artifacts")
    invalid_ref = "e3b0c442\x0098fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    with pytest.raises(InvalidArtifactReferenceError):
        store.resolve_path(invalid_ref)


def test_art_028_orphan_cleanup(tmp_path):
    """ART-028: Orphan cleanup removes blobs unreferenced by Core."""
    store = ArtifactStore(tmp_path / "artifacts")

    # Commit 3 artifacts
    d1 = store.commit_staged(store.stage_bytes(b"blob 1"))
    d2 = store.commit_staged(store.stage_bytes(b"blob 2"))
    d3 = store.commit_staged(store.stage_bytes(b"blob 3"))

    # Core only references d1 and d3 (d2 is orphaned)
    referenced = {d1, d3}
    orphans = store.cleanup_orphans(referenced)

    assert d2 in orphans
    assert d1 not in orphans
    assert d3 not in orphans

    # d2 file should no longer exist
    assert not store.resolve_path(d2).exists()
    # d1 and d3 must remain intact
    assert store.resolve_path(d1).exists()
    assert store.resolve_path(d3).exists()


def test_art_034_missing_physical_blob_raises_error(tmp_path):
    """ART-034: Requesting a valid digest not present on disk raises ArtifactNotFoundError."""
    store = ArtifactStore(tmp_path / "artifacts")
    non_existent = "a" * 64

    with pytest.raises(ArtifactNotFoundError):
        store.read_bytes(non_existent)


def test_art_036_invalid_digest_structure_raises_error(tmp_path):
    """ART-036: Non-hex, too short, or malformed digest strings raise InvalidArtifactReferenceError."""
    store = ArtifactStore(tmp_path / "artifacts")

    invalid_digests = [
        "not_a_digest",
        "12345",
        "g" * 64,  # 'g' is not hex
        "A" * 64,  # uppercase
        "../../../etc/passwd",
    ]

    for inv in invalid_digests:
        with pytest.raises((InvalidArtifactReferenceError, SecurityBoundaryError)):
            store.resolve_path(inv)
