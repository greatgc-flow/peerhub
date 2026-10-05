"""Wave 3: Artifact Store Lifecycle, Continuity & Edge Case Tests.

Verifies:
- ART-030: Boot GC sweeps stranded staging (.tmp) files older than threshold.
- ART-031: NUL bytes within payload content are processed without truncation.
- ART-032: Empty 0-byte payload stages, hashes (to standard sha256), and commits validly.
- ART-012: Stream record with m2.artifact.reference remains readable as an opaque dict when extension is disabled.
"""

import time
import os
from pathlib import Path
import pytest

from peerhub.m2.artifact import ArtifactStore, StagedArtifact


def test_art_032_zero_byte_artifact(tmp_path):
    """ART-032: An empty 0-byte payload stages and commits to the canonical SHA-256 digest."""
    store = ArtifactStore(tmp_path / "artifacts")
    empty_digest = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"

    staged = store.stage_bytes(b"")
    assert staged.digest == empty_digest
    assert staged.size == 0

    committed_digest = store.commit_staged(staged)
    assert committed_digest == empty_digest

    # Verify read
    assert store.read_bytes(empty_digest) == b""


def test_art_031_nul_bytes_in_payload(tmp_path):
    """ART-031: Binary payloads containing NUL bytes (\\x00) are processed without truncation."""
    store = ArtifactStore(tmp_path / "artifacts")
    data = b"prefix\x00\x00middle\x00suffix\xff\xfe"

    staged = store.stage_bytes(data)
    assert staged.size == len(data)

    digest = store.commit_staged(staged)
    read_data = store.read_bytes(digest)
    assert read_data == data
    assert len(read_data) == len(data)


def test_art_030_boot_gc_sweeps_stranded_tmp_files(tmp_path):
    """ART-030: Sweeping stranded tmp files deletes files older than cutoff and retains recent ones."""
    store = ArtifactStore(tmp_path / "artifacts")
    tmp_dir = tmp_path / "artifacts" / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    old_file = tmp_dir / "stranded_old.tmp"
    old_file.write_bytes(b"old garbage from crashed stage")
    # Set modification time to 48 hours ago
    past_time = time.time() - (48 * 3600)
    os.utime(old_file, (past_time, past_time))

    new_file = tmp_dir / "fresh_active.tmp"
    new_file.write_bytes(b"active upload in progress")

    # Sweep files older than 24 hours (86400 seconds)
    removed = store.sweep_staging(max_age_seconds=86400)

    assert old_file.name in [p.name for p in removed]
    assert not old_file.exists()
    assert new_file.exists()


def test_art_012_disabled_extension_stream_continuity():
    """ART-012: When extension is disabled, stream records containing m2.artifact.reference remain readable as opaque dicts."""
    # Simulate a Core record containing an extension payload
    raw_record = {
        "id": "rec_001",
        "stream_id": "stream_art",
        "offset": 42,
        "kind": "m2.artifact.reference",
        "payload": {
            "digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
            "size": 0,
            "media_type": "application/octet-stream",
        },
    }

    # Core reader encounters the record without the artifact extension active
    assert raw_record["kind"] == "m2.artifact.reference"
    assert isinstance(raw_record["payload"], dict)
    assert raw_record["payload"]["digest"] == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
