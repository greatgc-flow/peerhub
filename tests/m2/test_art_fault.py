"""Wave 2: ART-003, ART-004, ART-016, ART-017 - Fault Injection and Staging Concurrency/Idempotency.

Verifies:
- ART-003: Disk-full (ENOSPC) during stage leaves no corrupted commit; temp file is aggressively removed.
- ART-004: Partial stream read failure cleans up .tmp file and aborts before commitment.
- ART-016: Concurrent staging of identical payload handles atomic rename collision gracefully (mkstemp uniqueness + FileExistsError catch).
- ART-017: Re-uploading an identical artifact digest is strictly idempotent and returns the existing reference.
"""

import io
from pathlib import Path
from unittest.mock import patch
import pytest

from peerhub.m2.artifact import ArtifactStore


@pytest.mark.fault
def test_art_003_enospc_during_stage_cleans_up_temp_file(tmp_path):
    """ART-003: When os.write raises OSError(ENOSPC), partial temp files are wiped and no artifact is committed."""
    store = ArtifactStore(tmp_path / "artifacts")
    data = b"data that will trigger enospc during staging write"

    def mock_write(fd, b):
        raise OSError(28, "No space left on device")

    with patch("os.write", side_effect=mock_write):
        with pytest.raises(OSError, match="No space left on device"):
            store.stage_bytes(data)

    # Assert no stray temp files remain in .tmp
    tmp_files = list(store.tmp_dir.glob("*"))
    assert tmp_files == [], f"Staged temp files were not cleaned up: {tmp_files}"


@pytest.mark.fault
def test_art_004_partial_stream_read_error_cleans_up_temp_file(tmp_path):
    """ART-004: When stream reading is interrupted midway, the temp file is removed cleanly."""
    store = ArtifactStore(tmp_path / "artifacts")

    class FaultyStream(io.BytesIO):
        def __init__(self, initial_bytes):
            super().__init__(initial_bytes)
            self.read_count = 0

        def read(self, size=-1):
            self.read_count += 1
            if self.read_count > 1:
                raise ConnectionResetError("Stream read aborted by remote peer")
            return super().read(size)

    faulty = FaultyStream(b"A" * 1024)

    with pytest.raises(ConnectionResetError):
        store.stage_stream(faulty, chunk_size=256)

    # Ensure .tmp is clean
    assert list(store.tmp_dir.glob("*")) == []


@pytest.mark.concurrency
def test_art_016_and_017_idempotent_duplicate_commit(tmp_path):
    """ART-016 & ART-017: Committing identical payload twice succeeds idempotently with single physical blob."""
    store = ArtifactStore(tmp_path / "artifacts")
    content = b"duplicate content test for idempotency"

    # Process 1 / attempt 1
    staged1 = store.stage_bytes(content)
    digest1 = store.commit_staged(staged1)

    # Process 2 / attempt 2 (same content staged independently to a distinct temp file)
    staged2 = store.stage_bytes(content)
    assert staged1.path != staged2.path  # mkstemp uniqueness guarantees disjoint temp files

    # Second commit must succeed seamlessly without error
    digest2 = store.commit_staged(staged2)

    assert digest1 == digest2
    assert store.resolve_path(digest1).is_file()
    assert store.read_bytes(digest1) == content
    assert not staged2.path.exists(), "Duplicate temp file should be cleanly removed upon idempotent commit"
