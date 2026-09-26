"""Unit tests for peerhub/adapters/prompt_transport.py.

Covers item 3 (dotdir consolidation, ratified 2026-09-09): staging to an
arbitrary root (workspace OR temp), the validator's continued rejection of
absolute/traversal relative_dir regardless of root, and the two cleanup
mechanisms (immediate removal for a definite outcome, bounded janitor
sweep for genuinely-uncertain ones).
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest

from peerhub.adapters.prompt_transport import (
    remove_staged_prompt,
    resolve_staging_dir,
    stage_prompt,
    sweep_stale_staged_prompts,
)


def test_stage_prompt_under_workspace_root(tmp_path: Path) -> None:
    staged = stage_prompt(
        "hello world", root=tmp_path, relative_dir=".peerhub/prompt-staging",
        request_id="req-1",
    )
    assert staged.path.is_file()
    assert staged.path.is_relative_to(tmp_path)
    assert staged.path.read_bytes().decode("utf-8") == "hello world"
    assert staged.sha256_hex == hashlib.sha256(b"hello world").hexdigest()


def test_stage_prompt_under_an_arbitrary_temp_root(tmp_path: Path) -> None:
    """The whole point of item 3: staging works identically under a root
    that is NOT the workspace (simulating config_paths.resolve_workspace_temp())."""
    temp_root = tmp_path / "not-a-workspace" / "os-temp-stand-in"
    staged = stage_prompt(
        "temp-rooted payload", root=temp_root, relative_dir="prompt-staging",
        request_id="req-2",
    )
    assert staged.path.is_file()
    assert staged.path.is_relative_to(temp_root)
    assert not staged.path.is_relative_to(tmp_path / "workspace")


def test_resolve_staging_dir_still_rejects_absolute_relative_dir(tmp_path: Path) -> None:
    """The validator must never be relaxed, regardless of which root is
    in play (item 3's explicit constraint). Uses a drive-qualified path so
    this is unambiguously absolute on Windows too -- a bare "/etc/passwd"
    is NOT Path.is_absolute() on Windows without a drive letter."""
    absolute = str(Path(tmp_path.anchor) / "etc" / "passwd")
    with pytest.raises(ValueError, match="must be relative"):
        resolve_staging_dir(tmp_path, absolute)


def test_resolve_staging_dir_still_rejects_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="traversal"):
        resolve_staging_dir(tmp_path, "../../escape")


def test_remove_staged_prompt_deletes_the_file(tmp_path: Path) -> None:
    staged = stage_prompt(
        "to be removed", root=tmp_path, relative_dir="staging", request_id="req-3",
    )
    assert staged.path.is_file()
    remove_staged_prompt(str(staged.path))
    assert not staged.path.exists()


def test_remove_staged_prompt_is_idempotent_on_a_missing_file(tmp_path: Path) -> None:
    """Cleanup must never raise just because it's called twice, or the
    file was already reclaimed by the janitor sweep."""
    missing = tmp_path / "never-existed.prompt.txt"
    remove_staged_prompt(str(missing))  # must not raise


def _dead_pid() -> int:
    import subprocess
    import sys
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _backdate(path: Path, seconds: float = 7200) -> None:
    import os
    t = time.time() - seconds
    os.utime(path, (t, t))


def _set_owner(staged_path: Path, pid: int) -> None:
    import json
    staged_path.with_name(staged_path.name + ".owner").write_text(
        json.dumps({"pid": pid, "request_id": "x"}), encoding="utf-8"
    )


def test_sweep_reclaims_only_old_scratch_whose_owner_is_confirmed_dead(tmp_path: Path) -> None:
    dead_old = stage_prompt("abandoned", root=tmp_path, relative_dir="staging", request_id="dead-old")
    live_old = stage_prompt("old but live", root=tmp_path, relative_dir="staging", request_id="live-old")
    unknown_old = stage_prompt("unknown consumer", root=tmp_path, relative_dir="staging", request_id="unk-old")
    fresh_dead = stage_prompt("young", root=tmp_path, relative_dir="staging", request_id="fresh")
    _set_owner(dead_old.path, _dead_pid())
    # live_old keeps the owner recorded by stage_prompt (this test process: alive)
    unknown_old.path.with_name(unknown_old.path.name + ".owner").unlink()
    _set_owner(fresh_dead.path, _dead_pid())
    for staged in (dead_old, live_old, unknown_old):
        _backdate(staged.path)

    removed = sweep_stale_staged_prompts(tmp_path, "staging", max_age_seconds=3600.0)

    assert removed == 1
    assert not dead_old.path.exists()
    assert not dead_old.path.with_name(dead_old.path.name + ".owner").exists()
    assert live_old.path.exists()      # old-but-live: age never authorizes deletion (TP-E-08)
    assert unknown_old.path.exists()   # unknown consumer survives
    assert fresh_dead.path.exists()    # age selects candidates: too young


def test_remove_staged_prompt_also_removes_the_owner_record(tmp_path: Path) -> None:
    staged = stage_prompt("x", root=tmp_path, relative_dir="staging", request_id="r")
    owner = staged.path.with_name(staged.path.name + ".owner")
    assert owner.is_file()
    remove_staged_prompt(str(staged.path))
    assert not staged.path.exists() and not owner.exists()


def test_process_is_alive_distinguishes_live_and_dead_processes() -> None:
    import os
    from peerhub.adapters.prompt_transport import process_is_alive
    assert process_is_alive(os.getpid()) is True
    assert process_is_alive(_dead_pid()) is False
    assert process_is_alive(0) is False


def test_sweep_stale_staged_prompts_on_a_nonexistent_staging_dir_is_a_noop(
    tmp_path: Path,
) -> None:
    removed = sweep_stale_staged_prompts(
        tmp_path, "never-created", max_age_seconds=3600.0
    )
    assert removed == 0
