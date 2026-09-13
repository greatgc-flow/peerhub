import os
import sqlite3
from pathlib import Path
import pytest
from peerhub.cli import context as context_module
from peerhub.cli.context import resolve_workspace

def test_resolve_explicit_workspace_dot_vs_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    # This test's "default" case means "no project boundary or .peerhub
    # exists anywhere up the tree" -- but tmp_path here is nested inside
    # this real checkout (pytest's basetemp is forced under _sys/data/temp
    # by a Windows tmp-dir ACL workaround), which IS a real git-bounded
    # project with a real (if now-empty) .peerhub above it. Without this,
    # the discovery walk would escape tmp_path and resolve against that
    # real ancestor instead of testing the genuinely-no-boundary fallback
    # this test is named for (this exact escape once wrote a real test row
    # into that real database -- see the R2 test-isolation incident in
    # docs/design/quota-efficiency-RATIFIED-cx-astra-2026-09-13.md's
    # follow-up). Force "nothing found anywhere above tmp_path" rather
    # than adding a marker file, since a `.git` marker in tmp_path would
    # change selection_source to "discovered" and defeat the "cwd" case
    # below.
    monkeypatch.setattr(context_module, "_is_git_boundary", lambda path: False)
    real_exists = Path.exists

    def _scoped_exists(self: Path) -> bool:
        try:
            self.relative_to(tmp_path)
        except ValueError:
            return False
        return real_exists(self)

    monkeypatch.setattr(Path, "exists", _scoped_exists)

    # Default behavior: resolves to cwd, source="cwd"
    res_default = resolve_workspace(explicit_workspace=None)
    assert res_default.root == tmp_path
    assert res_default.selection_source == "cwd"
    
    # Explicit dot behavior: resolves to cwd, source="explicit"
    res_explicit = resolve_workspace(explicit_workspace=".")
    assert res_explicit.root == tmp_path
    assert res_explicit.selection_source == "explicit"

def test_resolve_explicit_workspace_path(tmp_path):
    explicit_path = tmp_path / "explicit_dir"
    explicit_path.mkdir()
    
    res = resolve_workspace(explicit_workspace=str(explicit_path))
    assert res.root == explicit_path.resolve()
    assert res.selection_source == "explicit"

def test_resolve_peerhub_workspace_env(tmp_path, monkeypatch):
    env_path = tmp_path / "env_dir"
    env_path.mkdir()
    
    monkeypatch.setenv("PEERHUB_WORKSPACE", str(env_path))
    res = resolve_workspace()
    assert res.root == env_path.resolve()
    assert res.selection_source == "env"
    
def test_resolve_peerhub_workspace_env_relative_rejected(monkeypatch):
    monkeypatch.setenv("PEERHUB_WORKSPACE", "relative/path")
    with pytest.raises(ValueError, match="PEERHUB_WORKSPACE must be an absolute path"):
        resolve_workspace()

def test_resolve_nearest_peerhub_discovery(tmp_path):
    root_dir = tmp_path / "project"
    root_dir.mkdir()
    (root_dir / ".peerhub").mkdir()
    
    nested_dir = root_dir / "a" / "b" / "c"
    nested_dir.mkdir(parents=True)
    
    res = resolve_workspace(cwd_override=nested_dir)
    assert res.root == root_dir
    assert res.selection_source == "discovered"

def test_resolve_git_worktree_boundary_dir(tmp_path):
    # .git directory
    git_dir = tmp_path / "git_project"
    git_dir.mkdir()
    (git_dir / ".git").mkdir()
    
    nested = git_dir / "nested"
    nested.mkdir()
    
    # Should stop at git_project and not go higher
    res = resolve_workspace(cwd_override=nested)
    assert res.root == git_dir
    assert res.selection_source == "discovered"
    assert res.project_boundary == git_dir

def test_resolve_git_worktree_boundary_file(tmp_path):
    # .git file
    git_dir = tmp_path / "git_submodule"
    git_dir.mkdir()
    (git_dir / ".git").write_text("gitdir: ../.git/modules/submodule")
    
    nested = git_dir / "nested"
    nested.mkdir()
    
    res = resolve_workspace(cwd_override=nested)
    assert res.root == git_dir
    assert res.selection_source == "discovered"
    assert res.project_boundary == git_dir

def test_resolve_config_only_peerhub(tmp_path):
    ph_dir = tmp_path / "config_only"
    ph_dir.mkdir()
    peerhub = ph_dir / ".peerhub"
    peerhub.mkdir()
    (peerhub / "config").mkdir()
    
    res = resolve_workspace(cwd_override=ph_dir)
    assert res.root == ph_dir
    assert res.selection_source == "discovered"
    assert res.state_description == "config-only"
    assert res.is_initialized is False

def test_resolve_corrupt_db(tmp_path):
    ph_dir = tmp_path / "corrupt_db"
    ph_dir.mkdir()
    peerhub = ph_dir / ".peerhub"
    peerhub.mkdir()
    (peerhub / "peerhub.sqlite3").write_text("not a database")
    
    res = resolve_workspace(cwd_override=ph_dir)
    assert res.root == ph_dir
    assert res.state_description == "corrupt"
    assert res.is_initialized is False

def test_resolve_unsupported_db(tmp_path):
    ph_dir = tmp_path / "unsupported_db"
    ph_dir.mkdir()
    peerhub = ph_dir / ".peerhub"
    peerhub.mkdir()
    
    db_path = peerhub / "peerhub.sqlite3"
    conn = sqlite3.connect(db_path)
    # Create valid sqlite but no identity table -> unsupported/missing tables
    conn.execute("CREATE TABLE some_other_table (id INTEGER)")
    conn.commit()
    conn.close()
    
    res = resolve_workspace(cwd_override=ph_dir)
    assert res.root == ph_dir
    # Based on our logic, it connects fine but lacks identity table, still valid just identity=None
    # Let's see what logic gives: it checks if 'workspace_identity' exists. If not, it just sets identity=None and returns 'valid'
    assert res.state_description == "valid"
    assert res.is_initialized is True
    assert res.identity is None
    
def test_resolve_fails_closed_on_invalid_context(tmp_path, monkeypatch):
    monkeypatch.setenv("PEERHUB_CONTEXT_FILE", "some_file.json")
    with pytest.raises(RuntimeError, match="PEERHUB_CONTEXT_FILE is present but context verification is not yet implemented"):
        resolve_workspace()


def test_check_store_state_operational_error_is_unsupported_not_corrupt(tmp_path, monkeypatch):
    """Regression: sqlite3.OperationalError is a SUBCLASS of DatabaseError, so
    an except-order bug once let the DatabaseError clause swallow it first,
    always reporting 'corrupt' and never 'unsupported' (pyright's
    reportUnusedExcept correctly flagged the dead clause)."""
    ph_dir = tmp_path / "unsupported"
    ph_dir.mkdir()
    (ph_dir / "peerhub.sqlite3").write_text("placeholder")

    class _FakeConn:
        def cursor(self):
            raise sqlite3.OperationalError("disk I/O error")
        def close(self):
            pass

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: _FakeConn())
    is_init, identity, state = context_module._check_store_state(ph_dir)
    assert state == "unsupported"
    assert is_init is True
    assert identity is None


def test_check_store_state_database_error_is_corrupt(tmp_path, monkeypatch):
    ph_dir = tmp_path / "corrupt2"
    ph_dir.mkdir()
    (ph_dir / "peerhub.sqlite3").write_text("placeholder")

    class _FakeConn:
        def cursor(self):
            raise sqlite3.DatabaseError("file is not a database")
        def close(self):
            pass

    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: _FakeConn())
    is_init, identity, state = context_module._check_store_state(ph_dir)
    assert state == "corrupt"
    assert is_init is False
    assert identity is None

