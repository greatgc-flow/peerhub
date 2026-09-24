import pytest
from pathlib import Path
from peerhub.adapters.prompt_transport import (
    stage_prompt,
    RetentionMode,
    CleanupDebtRecord,
    sweep_by_ownership
)

def test_tpe_01_query_file_exact_max_bytes():
    raise NotImplementedError("TDD RED state")

def test_tpe_02_query_file_max_bytes_plus_one():
    raise NotImplementedError("TDD RED state")

def test_tpe_03_staging_dir_created():
    raise NotImplementedError("TDD RED state")

def test_tpe_04_staging_dir_readonly():
    raise NotImplementedError("TDD RED state")

def test_tpe_05_disk_full_during_staging():
    raise NotImplementedError("TDD RED state")

def test_tpe_06_same_prompt_staged_twice():
    raise NotImplementedError("TDD RED state")

def test_tpe_07_staged_file_modified_externally():
    raise NotImplementedError("TDD RED state")

def test_tpe_08_sweep_during_active_dispatch():
    sweep_by_ownership(Path("/tmp"), "relative")

def test_tpe_09_query_file_symlink():
    raise NotImplementedError("TDD RED state")

def test_tpe_10_query_file_directory():
    raise NotImplementedError("TDD RED state")

def test_tpe_11_query_file_binary():
    raise NotImplementedError("TDD RED state")

def test_tpe_12_crash_retention_ephemeral():
    CleanupDebtRecord(staged_path=Path("/tmp/foo"), consumer_pid=123)

def test_tpe_13_crash_retention_retained():
    raise NotImplementedError("TDD RED state")
