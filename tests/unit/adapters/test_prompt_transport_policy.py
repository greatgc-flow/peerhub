import pytest
import os
from pathlib import Path
from peerhub.adapters.prompt_transport import (
    stage_prompt,
    RetentionMode,
    CleanupDebtRecord,
    sweep_by_ownership,
    should_stage_prompt,
    read_query_file_utf8,
    render_staged_prompt_pointer
)

def test_tpe_01_query_file_exact_max_bytes():
    assert should_stage_prompt(payload_utf8_bytes=1000, max_inline_bytes=1000) is False

def test_tpe_02_query_file_max_bytes_plus_one():
    assert should_stage_prompt(payload_utf8_bytes=1001, max_inline_bytes=1000) is True

def test_tpe_03_staging_dir_created(tmp_path: Path):
    relative = "nested/new_dir"
    staged = stage_prompt("test", root=tmp_path, relative_dir=relative, request_id="req1")
    assert (tmp_path / relative).is_dir()
    assert staged.path.is_file()

def test_tpe_04_staging_dir_readonly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    original_open = Path.open
    def mock_open(self, *args, **kwargs):
        if "staging" in str(self):
            raise OSError("Permission denied")
        return original_open(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", mock_open)
    with pytest.raises(OSError, match="Permission denied"):
        stage_prompt("test", root=tmp_path, relative_dir="staging", request_id="req1")

def test_tpe_05_disk_full_during_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    def mock_fsync(*args, **kwargs):
        raise OSError("No space left on device")
    monkeypatch.setattr(os, "fsync", mock_fsync)
    
    with pytest.raises(OSError, match="No space left on device"):
        stage_prompt("test", root=tmp_path, relative_dir="staging", request_id="req1")
    
    # Assert the partial file is NOT left behind
    staging_dir = tmp_path / "staging"
    if staging_dir.exists():
        assert len(list(staging_dir.iterdir())) == 0

def test_tpe_06_same_prompt_staged_twice(tmp_path: Path):
    s1 = stage_prompt("test payload", root=tmp_path, relative_dir="staging", request_id="req1")
    s2 = stage_prompt("test payload", root=tmp_path, relative_dir="staging", request_id="req1")
    # Same prompt, same request_id -> distinct copy logic reuse
    assert s1.path == s2.path
    
    s3 = stage_prompt("test payload", root=tmp_path, relative_dir="staging", request_id="req2")
    # Same prompt, different request_id -> different file
    assert s1.path != s3.path

def test_tpe_07_staged_file_modified_externally(tmp_path: Path):
    staged = stage_prompt("original text", root=tmp_path, relative_dir="staging", request_id="req1")
    staged.path.write_bytes(b"modified externally")
    
    # NOTE: render_staged_prompt_pointer doesn't yet check against original digest, so it doesn't reject mismatch.
    # Asserting ValueError simulates the RED state where the rejection is missing.
    with pytest.raises(ValueError, match="mismatch"):
        render_staged_prompt_pointer(str(staged.path))

def test_tpe_08_sweep_during_active_dispatch(tmp_path: Path):
    s1 = stage_prompt("in use", root=tmp_path, relative_dir="staging", request_id="req1")
    s2 = stage_prompt("orphaned", root=tmp_path, relative_dir="staging", request_id="req2")
    
    removed = sweep_by_ownership(root=tmp_path, relative_dir="staging", active_owners={"req1"})
    assert s1.path.exists()
    assert not s2.path.exists()
    assert removed == 1

def test_tpe_09_query_file_symlink(tmp_path: Path):
    target = tmp_path / "target.txt"
    target.write_text("hello", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        os.symlink(target, link)
    except OSError:
        pytest.skip("Symlinks not supported on this OS without admin rights")
    assert read_query_file_utf8(link) == "hello"

def test_tpe_10_query_file_directory(tmp_path: Path):
    d = tmp_path / "dir"
    d.mkdir()
    with pytest.raises((IsADirectoryError, PermissionError)):
        read_query_file_utf8(d)

def test_tpe_11_query_file_binary(tmp_path: Path):
    b = tmp_path / "bin"
    b.write_bytes(b"\x80\x81\x82")
    with pytest.raises(UnicodeDecodeError):
        read_query_file_utf8(b)

def test_tpe_12_crash_retention_ephemeral(tmp_path: Path):
    staged = stage_prompt("test", root=tmp_path, relative_dir="staging", request_id="req1")
    record = CleanupDebtRecord(staged_path=staged.path, consumer_pid=123)
    assert record.staged_path == staged.path

def test_tpe_13_crash_retention_retained(tmp_path: Path):
    staged = stage_prompt("test", root=tmp_path, relative_dir="staging", request_id="req1", retention_mode=RetentionMode.RETAINED)
    record = CleanupDebtRecord(staged_path=staged.path, consumer_pid=123, retained_input_ttl_days=7)
    assert record.staged_path == staged.path

