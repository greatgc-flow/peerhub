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


def test_sweep_stale_staged_prompts_reclaims_only_files_past_the_age_bound(
    tmp_path: Path,
) -> None:
    old = stage_prompt(
        "abandoned by an uncertain dispatch", root=tmp_path,
        relative_dir="staging", request_id="old-req",
    )
    fresh = stage_prompt(
        "a dispatch that is merely slow, not abandoned", root=tmp_path,
        relative_dir="staging", request_id="fresh-req",
    )
    # Backdate only the "old" file's mtime past the age bound.
    old_time = time.time() - 7200
    import os
    os.utime(old.path, (old_time, old_time))

    removed = sweep_stale_staged_prompts(
        tmp_path, "staging", max_age_seconds=3600.0
    )

    assert removed == 1
    assert not old.path.exists()
    assert fresh.path.exists()


def test_sweep_stale_staged_prompts_on_a_nonexistent_staging_dir_is_a_noop(
    tmp_path: Path,
) -> None:
    removed = sweep_stale_staged_prompts(
        tmp_path, "never-created", max_age_seconds=3600.0
    )
    assert removed == 0
