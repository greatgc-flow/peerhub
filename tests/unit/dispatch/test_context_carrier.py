"""D-CTX context-file carrier (D5/D6, D0/Q2/D2 closed 2026-09-14)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from peerhub.dispatch.context_carrier import (
    ContextFileError,
    cleanup_context_file,
    create_context_file,
    read_context_file,
)
from peerhub.persistence.maintenance import WorkspaceMaintenanceError


def test_create_and_read_round_trip(tmp_path: Path) -> None:
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    temp_root = tmp_path / "tmp"

    path = create_context_file(
        workspace_root, credential_id="cred-1", temp_root=temp_root
    )
    assert path.is_file()

    credential_id, recorded_workspace_root = read_context_file(path)
    assert credential_id == "cred-1"
    assert recorded_workspace_root == str(workspace_root)


def test_create_context_file_uses_unpredictable_per_attempt_paths(tmp_path: Path) -> None:
    """Never a well-known singleton -- concurrent dispatches must not collide."""

    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    temp_root = tmp_path / "tmp"

    path_a = create_context_file(workspace_root, credential_id="cred-a", temp_root=temp_root)
    path_b = create_context_file(workspace_root, credential_id="cred-b", temp_root=temp_root)
    assert path_a != path_b
    assert path_a.parent == path_b.parent  # same contexts dir, different files


def test_create_context_file_lives_under_workspace_transient_namespace(tmp_path: Path) -> None:
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    temp_root = tmp_path / "tmp"

    path = create_context_file(workspace_root, credential_id="cred-1", temp_root=temp_root)
    assert temp_root in path.parents
    assert "dispatch-contexts" in path.parts


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL measurement")
def test_create_context_file_restricts_windows_acl_to_current_user(tmp_path: Path) -> None:
    """D6: measured, not assumed. Confirms the ACL narrowing this module
    exists for actually took effect on the real file."""

    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    temp_root = tmp_path / "tmp"

    path = create_context_file(workspace_root, credential_id="cred-1", temp_root=temp_root)

    result = subprocess.run(
        ["icacls", str(path)], capture_output=True, check=True
    )
    output = result.stdout.decode(errors="replace")
    # The broad grants a default-ACL file in this environment's own temp
    # directory was empirically measured to carry (2026-09-14 probe) must
    # be gone after restriction.
    assert "Authenticated Users" not in output
    assert "BUILTIN\\Users" not in output


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_create_context_file_restricts_posix_mode_to_owner(tmp_path: Path) -> None:
    import stat

    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    temp_root = tmp_path / "tmp"

    path = create_context_file(workspace_root, credential_id="cred-1", temp_root=temp_root)
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == stat.S_IRUSR | stat.S_IWUSR


def test_create_context_file_rejects_symlinked_contexts_directory(tmp_path: Path) -> None:
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    temp_root = tmp_path / "tmp"

    from peerhub.application.config_paths import resolve_workspace_temp

    contexts_dir = resolve_workspace_temp(workspace_root, temp_root=temp_root).path / "dispatch-contexts"
    contexts_dir.parent.mkdir(parents=True)
    real_dir = tmp_path / "real-contexts"
    real_dir.mkdir()
    try:
        contexts_dir.symlink_to(real_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this host")

    with pytest.raises(WorkspaceMaintenanceError):
        create_context_file(workspace_root, credential_id="cred-1", temp_root=temp_root)


def test_read_context_file_rejects_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(ContextFileError):
        read_context_file(path)


def test_read_context_file_rejects_missing_credential_id(tmp_path: Path) -> None:
    path = tmp_path / "incomplete.json"
    path.write_text('{"workspace_root": "/x"}', encoding="utf-8")
    with pytest.raises(ContextFileError):
        read_context_file(path)


def test_read_context_file_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ContextFileError):
        read_context_file(tmp_path / "does-not-exist.json")


def test_cleanup_context_file_is_idempotent(tmp_path: Path) -> None:
    workspace_root = tmp_path / "ws"
    workspace_root.mkdir()
    temp_root = tmp_path / "tmp"

    path = create_context_file(workspace_root, credential_id="cred-1", temp_root=temp_root)
    cleanup_context_file(path)
    assert not path.exists()
    cleanup_context_file(path)  # must not raise on a missing file
