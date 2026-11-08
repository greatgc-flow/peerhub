"""Store discovery: nearest workspace, explicit choices, and provenance."""
from pathlib import Path
import subprocess

from peerhub.cli.app import DEFAULT_DB_PATH, _resolve_db_path
from peerhub.cli.store_select import select_store
from peerhub.core.models import Peer
from peerhub.core.store import CoreStore


def seed(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    CoreStore(path).register_peer(Peer(peer_id="existing"))
    return path


def test_nearest_workspace_store_wins_over_parent_workspace(tmp_path, monkeypatch):
    seed(tmp_path / DEFAULT_DB_PATH)
    project = tmp_path / "project"
    near = seed(project / DEFAULT_DB_PATH)
    nested = project / "src"
    nested.mkdir()
    monkeypatch.chdir(nested)
    monkeypatch.delenv("PEERHUB_DB", raising=False)
    assert Path(_resolve_db_path(DEFAULT_DB_PATH)) == near


def test_workspace_root_discovery_never_escapes_its_owner(tmp_path, monkeypatch):
    seed(tmp_path / DEFAULT_DB_PATH)
    provider = tmp_path / "provider"
    provider.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PEERHUB_DB", raising=False)
    assert Path(_resolve_db_path(DEFAULT_DB_PATH, workspace_root=provider)) == provider / DEFAULT_DB_PATH


def test_explicit_observation_path_ignores_primary_db_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("PEERHUB_DB", "primary.db")
    assert _resolve_db_path("observations.db", use_env=False, discover=False) == str(Path("observations.db").resolve())


def test_selection_reports_how_the_store_was_chosen(tmp_path):
    assert select_store("x.db", env={"PEERHUB_DB": "e.db"}).source == "explicit"
    assert select_store("x.db", env={}).source == "explicit"
    assert select_store(DEFAULT_DB_PATH, env={}, cwd=tmp_path).source == "default"
    seed(tmp_path / DEFAULT_DB_PATH)
    (tmp_path / "deep").mkdir()
    found = select_store(DEFAULT_DB_PATH, env={}, cwd=tmp_path / "deep")
    assert found.source == "discovered" and found.path.endswith("core.db")
    assert select_store(DEFAULT_DB_PATH, env={}, workspace_root=tmp_path / "fresh").source == "default"


import pytest


@pytest.fixture
def polluted_subfolder_workspace(tmp_path: Path):
    """Pollution fixture: workspace with an ambient ./peerhub/.peerhub/core.db subfolder."""
    sub_store = tmp_path / "peerhub" / DEFAULT_DB_PATH
    seed(sub_store)
    return tmp_path, sub_store


def test_implicit_peerhub_subfolder_is_not_discovered(polluted_subfolder_workspace, monkeypatch):
    """O-1: Remove implicit ./peerhub sub-folder lookup in store_select.py.

    Walking into ./peerhub breaks root-rename resilience and causes workspace store pollution.
    """
    ws, sub_store = polluted_subfolder_workspace
    monkeypatch.chdir(ws)
    monkeypatch.delenv("PEERHUB_DB", raising=False)
    # Without an explicit path or cwd .peerhub/core.db, it must NOT walk into ./peerhub
    selection = select_store(DEFAULT_DB_PATH, env={}, cwd=ws)
    assert selection.source == "default"
    assert selection.path != str(sub_store)

    # Resolving path via CLI helper returns default path, NOT the subfolder store
    resolved = _resolve_db_path(DEFAULT_DB_PATH)
    assert resolved == str((ws / DEFAULT_DB_PATH).resolve())
    assert Path(resolved) != sub_store


def test_explicit_env_and_workspace_selection_preserved(polluted_subfolder_workspace, monkeypatch):
    ws, sub_store = polluted_subfolder_workspace
    monkeypatch.chdir(ws)
    # Explicit --db wins
    assert select_store("explicit.db", env={}, cwd=ws).source == "explicit"
    # PEERHUB_DB environment wins
    assert select_store(DEFAULT_DB_PATH, env={"PEERHUB_DB": "env.db"}, cwd=ws).source == "env"
    # Workspace root selection is preserved
    assert select_store(DEFAULT_DB_PATH, env={}, workspace_root=ws / "fresh").source == "default"
    # CWD .peerhub/core.db is still discovered
    cwd_store = seed(ws / DEFAULT_DB_PATH)
    discovered = select_store(DEFAULT_DB_PATH, env={}, cwd=ws)
    assert discovered.source == "discovered"
    assert discovered.path == str(cwd_store)


import json
import sqlite3
from peerhub.cli.app import main


def test_legacy_store_present_emits_hint_line(polluted_subfolder_workspace, monkeypatch, capsys):
    ws, sub_store = polluted_subfolder_workspace
    monkeypatch.chdir(ws)
    monkeypatch.delenv("PEERHUB_DB", raising=False)

    code = main(["peer", "list"])
    assert code == 5
    out, err = capsys.readouterr()
    expected_hint = (
        f'Ignored legacy store: {sub_store.resolve()} (no longer auto-discovered). '
        f'Use --db "{sub_store.resolve()}" to keep using it; stores of other schema versions are refused as usual.'
    )
    assert expected_hint in err
    assert f"database not found: {(ws / DEFAULT_DB_PATH).resolve()}" in err

    # With JSON format, exit code 5 and JSON payload remain unchanged while stderr retains the hint
    code_json = main(["peer", "list", "--format", "json"])
    assert code_json == 5
    out_json, err_json = capsys.readouterr()
    payload = json.loads(out_json)
    assert payload["status"] == "UNAVAILABLE"
    assert "database not found" in payload["error"]
    assert expected_hint in err_json


def test_legacy_store_flat_filename_emits_hint_line(tmp_path, monkeypatch, capsys):
    flat_store = tmp_path / "peerhub" / "core.db"
    seed(flat_store)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PEERHUB_DB", raising=False)

    code = main(["peer", "list"])
    assert code == 5
    _, err = capsys.readouterr()
    expected_hint = (
        f'Ignored legacy store: {flat_store.resolve()} (no longer auto-discovered). '
        f'Use --db "{flat_store.resolve()}" to keep using it; stores of other schema versions are refused as usual.'
    )
    assert expected_hint in err


def test_legacy_store_absent_emits_no_hint_line(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PEERHUB_DB", raising=False)

    code = main(["peer", "list"])
    assert code == 5
    _, err = capsys.readouterr()
    assert "Ignored legacy store:" not in err
    assert f"database not found: {(tmp_path / DEFAULT_DB_PATH).resolve()}" in err


def test_source_package_dir_without_store_file_emits_no_hint_line(tmp_path, monkeypatch, capsys):
    # Source package dir with Python code but no store file
    pkg_dir = tmp_path / "peerhub"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("# package\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PEERHUB_DB", raising=False)

    code = main(["peer", "list"])
    assert code == 5
    _, err = capsys.readouterr()
    assert "Ignored legacy store:" not in err
    assert f"database not found: {(tmp_path / DEFAULT_DB_PATH).resolve()}" in err


def test_explicit_db_to_legacy_store_works_and_preserves_schema_gate(polluted_subfolder_workspace, monkeypatch, capsys):
    ws, sub_store = polluted_subfolder_workspace
    monkeypatch.chdir(ws)
    monkeypatch.delenv("PEERHUB_DB", raising=False)

    # Explicit --db to legacy store works
    code = main(["--db", str(sub_store), "peer", "list"])
    assert code == 0
    out, err = capsys.readouterr()
    assert "existing" in out
    assert "Ignored legacy store:" not in err

    # Other schema version is refused as usual (schema gate unchanged)
    with sqlite3.connect(sub_store) as conn:
        conn.execute("PRAGMA user_version = 999")
    code_schema_err = main(["--db", str(sub_store), "peer", "list"])
    assert code_schema_err == 6
    _, err_schema = capsys.readouterr()
    assert "SCHEMA VERSION ERROR" in err_schema


def test_explicit_missing_db_with_legacy_store_present_emits_no_hint_line(polluted_subfolder_workspace, monkeypatch, capsys):
    ws, _ = polluted_subfolder_workspace
    monkeypatch.chdir(ws)
    monkeypatch.delenv("PEERHUB_DB", raising=False)

    code = main(["--db", "nonexistent.db", "peer", "list"])
    assert code == 5
    _, err = capsys.readouterr()
    assert f"database not found: {ws / 'nonexistent.db'}" in err
    assert "Ignored legacy store:" not in err


def test_schema_11_explicit_default_path_with_legacy_store_emits_no_hint(polluted_subfolder_workspace, monkeypatch, capsys):
    ws, _ = polluted_subfolder_workspace
    monkeypatch.chdir(ws)
    monkeypatch.delenv("PEERHUB_DB", raising=False)

    code = main(["--db", str(DEFAULT_DB_PATH), "monitor", "--json", "--schema-version", "1.1"])
    assert code == 5
    out, err = capsys.readouterr()
    payload = json.loads(out)
    assert payload["status"] == "UNAVAILABLE"
    assert f"database not found: {(ws / DEFAULT_DB_PATH).resolve()}" in err
    assert "Ignored legacy store:" not in err


def test_schema_11_env_default_path_with_legacy_store_emits_no_hint(polluted_subfolder_workspace, monkeypatch, capsys):
    ws, _ = polluted_subfolder_workspace
    monkeypatch.chdir(ws)
    monkeypatch.setenv("PEERHUB_DB", str(DEFAULT_DB_PATH))

    code = main(["monitor", "--json", "--schema-version", "1.1"])
    assert code == 5
    out, err = capsys.readouterr()
    payload = json.loads(out)
    assert payload["status"] == "UNAVAILABLE"
    assert f"database not found: {(ws / DEFAULT_DB_PATH).resolve()}" in err
    assert "Ignored legacy store:" not in err


def test_schema_11_no_store_selected_with_legacy_store_emits_hint(polluted_subfolder_workspace, monkeypatch, capsys):
    ws, sub_store = polluted_subfolder_workspace
    monkeypatch.chdir(ws)
    monkeypatch.delenv("PEERHUB_DB", raising=False)

    code = main(["monitor", "--json", "--schema-version", "1.1"])
    assert code == 5
    out, err = capsys.readouterr()
    payload = json.loads(out)
    assert payload["status"] == "UNAVAILABLE"
    expected_hint = (
        f'Ignored legacy store: {sub_store.resolve()} (no longer auto-discovered). '
        f'Use --db "{sub_store.resolve()}" to keep using it; stores of other schema versions are refused as usual.'
    )
    assert expected_hint in err



def test_explicit_db_beats_env_including_literal_default(tmp_path, monkeypatch, capsys):
    from peerhub.cli.app import main
    monkeypatch.chdir(tmp_path)
    other = seed(tmp_path / "env.db")
    monkeypatch.setenv("PEERHUB_DB", str(other))
    for path in ("chosen.db", DEFAULT_DB_PATH):
        assert main(["--db", path, "peer", "register", "--peer", "chosen", "--json"]) == 0
        captured = capsys.readouterr()
        assert Path(path).is_file()
        assert captured.err == ""
        assert CoreStore(Path(path)).get_peer("chosen") is not None
    assert CoreStore(other).get_peer("chosen") is None


def git(*args):
    return subprocess.run(["git", *map(str, args)], check=True, capture_output=True)


@pytest.fixture
def linked_checkout(tmp_path):
    root = tmp_path / "owner" / "main space 한글"
    root.mkdir(parents=True)
    git("init", root)
    git("-C", root, "-c", "user.name=Test", "-c", "user.email=test@example.com",
        "commit", "--allow-empty", "-m", "initial")
    linked = tmp_path / "linked space 한글"
    git("-C", root, "worktree", "add", "--detach", linked)
    return root, linked


@pytest.mark.parametrize("ancestor", [False, True])
def test_linked_worktree_discovers_main_store(linked_checkout, ancestor):
    root, linked = linked_checkout
    expected = seed((root.parent if ancestor else root) / DEFAULT_DB_PATH)
    nested = linked / "src"
    nested.mkdir()
    for start in (linked, nested):
        selection = select_store(DEFAULT_DB_PATH, env={}, cwd=start)
        assert selection == type(selection)(str(expected), "discovered")
        workspace_selection = select_store(DEFAULT_DB_PATH, env={}, workspace_root=start)
        if ancestor:
            assert workspace_selection.source == "default"
        else:
            assert workspace_selection.path == str(expected)
    local = seed(linked / DEFAULT_DB_PATH)
    assert select_store(DEFAULT_DB_PATH, env={}, cwd=nested).path == str(local)


def test_nested_repo_does_not_follow_outer_worktree(linked_checkout):
    root, linked = linked_checkout
    seed(root / DEFAULT_DB_PATH)
    inner = linked / "inner"
    inner.mkdir()
    git("init", inner)
    assert select_store(DEFAULT_DB_PATH, env={}, cwd=inner).source == "default"


@pytest.mark.parametrize("failure", [FileNotFoundError(), subprocess.TimeoutExpired("git", 2)])
def test_git_failure_is_a_discovery_miss(tmp_path, monkeypatch, failure):
    def fail(*a, **kw):
        raise failure
    monkeypatch.setattr(subprocess, "run", fail)
    assert select_store(DEFAULT_DB_PATH, env={}, cwd=tmp_path).source == "default"


def test_malformed_git_metadata_is_a_discovery_miss(tmp_path):
    (tmp_path / ".git").write_text("gitdir: missing\n", encoding="utf-8")
    assert select_store(DEFAULT_DB_PATH, env={}, cwd=tmp_path).source == "default"


def test_separate_git_dir_does_not_guess_main_checkout(tmp_path):
    root = tmp_path / "owner" / "main"
    root.mkdir(parents=True)
    metadata = tmp_path / "separate metadata"
    git("init", "--separate-git-dir", metadata, root)
    git("-C", root, "-c", "user.name=Test", "-c", "user.email=test@example.com",
        "commit", "--allow-empty", "-m", "initial")
    linked = tmp_path / "linked"
    git("-C", root, "worktree", "add", "--detach", linked)
    seed(root / DEFAULT_DB_PATH)
    seed(metadata / DEFAULT_DB_PATH)  # Git reports this metadata dir as its main worktree.
    assert select_store(DEFAULT_DB_PATH, env={}, cwd=linked).source == "default"


def test_bare_repository_has_no_main_checkout_fallback(tmp_path):
    bare = tmp_path / "bare"
    git("init", "--bare", bare)
    seed(tmp_path / DEFAULT_DB_PATH)
    # Explicit workspace does not discover physical parents; bare Git has no main checkout.
    assert select_store(DEFAULT_DB_PATH, env={}, workspace_root=bare).source == "default"


def test_explicit_and_env_choices_skip_git(linked_checkout, monkeypatch):
    root, linked = linked_checkout
    seed(root / DEFAULT_DB_PATH)
    def unexpected(*a, **kw):
        pytest.fail("explicit selection queried Git")
    monkeypatch.setattr(subprocess, "run", unexpected)
    explicit = select_store(DEFAULT_DB_PATH, discover=False, env={"PEERHUB_DB": "other.db"}, cwd=linked)
    assert explicit.source == "explicit" and Path(explicit.path) == linked / DEFAULT_DB_PATH
    selected_env = select_store(DEFAULT_DB_PATH, env={"PEERHUB_DB": "other.db"}, cwd=linked)
    assert selected_env.source == "env" and Path(selected_env.path) == linked / "other.db"


@pytest.mark.parametrize("command", [
    ["peer", "register", "--peer", "new"],
    ["stream", "create", "--stream", "new", "--members", "new"],
    ["ask", "cx", "hello"],
    ["account", "list"],
    ["observation", "refresh", "--peers", "cx"],
    ["monitor"],
])
@pytest.mark.parametrize("json_output", [False, True])
def test_implicit_miss_refuses_before_dispatch(tmp_path, monkeypatch, capsys, command, json_output):
    import peerhub.cli.app as app
    def unexpected(*a, **kw):
        pytest.fail("missing store reached dispatch")
    for handler in ("_run_ask_cli", "_run_account_cli", "_run_batch_cli", "_run_light_command", "CoreStore"):
        monkeypatch.setattr(app, handler, unexpected)
    monkeypatch.chdir(tmp_path)
    assert main(command + (["--json"] if json_output else [])) == 5
    output = capsys.readouterr()
    assert "[store_missing]" in output.err and "next:" in output.err
    assert "--db" in output.err and "PEERHUB_DB" in output.err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("command", [["guide"], ["explain", "store_missing"],
                                     ["profile", "list"], ["monitor", "groups"],
                                     ["monitor", "defaults", "show"]])
def test_store_free_commands_work_without_store(tmp_path, monkeypatch, capsys, command):
    monkeypatch.chdir(tmp_path)
    assert main(command) == 0
    assert not (tmp_path / ".peerhub").exists()


@pytest.mark.parametrize("explicit", ["flag", "env"])
def test_explicit_selection_still_bootstraps_ask(tmp_path, monkeypatch, capsys, explicit):
    from peerhub.extensions import ask as module
    from tests.communication.fakes import FakeRuntimeTarget
    real_ask = module.ask
    monkeypatch.setattr(module, "ask", lambda *a, **kw: real_ask(*a, **kw, runtime=FakeRuntimeTarget()))
    monkeypatch.chdir(tmp_path)
    if explicit == "env":
        monkeypatch.setenv("PEERHUB_DB", DEFAULT_DB_PATH)
    prefix = ["--db", DEFAULT_DB_PATH] if explicit == "flag" else []
    assert main(prefix + ["ask", "cx", "hello", "--json"]) == 0
    assert (tmp_path / DEFAULT_DB_PATH).is_file()


@pytest.mark.parametrize("subdirectory", [False, True])
def test_ask_workspace_uses_linked_main_store(linked_checkout, tmp_path, monkeypatch, capsys, subdirectory):
    from peerhub.extensions import ask as module
    from tests.communication.fakes import FakeRuntimeTarget
    root, linked = linked_checkout
    if subdirectory:
        linked = linked / "src"
        linked.mkdir()
    expected = seed(root / DEFAULT_DB_PATH)
    real_ask = module.ask
    seen = []
    def fake(db, *a, **kw):
        seen.append(Path(db))
        return real_ask(db, *a, **kw, runtime=FakeRuntimeTarget())
    monkeypatch.setattr(module, "ask", fake)
    monkeypatch.chdir(tmp_path)
    assert main(["ask", "cx", "hello", "-w", str(linked), "--json"]) == 0
    assert seen == [expected]
    assert not (linked / ".peerhub").exists()


def test_git_environment_cannot_redirect_discovery(linked_checkout, tmp_path, monkeypatch):
    root, linked = linked_checkout
    expected = seed(root / DEFAULT_DB_PATH)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    git("init", unrelated)
    for key, value in {"GIT_DIR": str(unrelated / ".git"), "GIT_WORK_TREE": str(unrelated),
                       "GIT_INDEX_FILE": str(tmp_path / "bad-index")}.items():
        monkeypatch.setenv(key, value)
    original = subprocess.run
    calls = []
    def checked(*a, **kw):
        calls.append(kw.get("env", {}))
        return original(*a, **kw)
    monkeypatch.setattr(subprocess, "run", checked)
    assert select_store(DEFAULT_DB_PATH, env={}, cwd=linked).path == str(expected)
    assert len(calls) == 2
    assert all(not ({"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"} & set(env)) for env in calls)


@pytest.mark.parametrize("workspace", [False, True])
def test_main_root_already_searched_does_not_repeat_discovery(tmp_path, monkeypatch, workspace):
    import peerhub.cli.store_select as module
    monkeypatch.setattr(module, "_main_worktree", lambda base: tmp_path)
    original = Path.is_file
    visits = []
    def checked(path):
        if path == tmp_path / DEFAULT_DB_PATH:
            visits.append(path)
        return original(path)
    monkeypatch.setattr(Path, "is_file", checked)
    assert select_store(DEFAULT_DB_PATH, env={}, cwd=tmp_path,
                        workspace_root=tmp_path if workspace else None).source == "default"
    assert len(visits) == 1


@pytest.mark.parametrize("command", [["diag", "readiness"], ["diag", "quota"],
                                     ["diag", "health"], ["diag", "integrity"]])
def test_implicit_missing_diag_keeps_explicit_missing_report(tmp_path, monkeypatch, capsys, command):
    monkeypatch.chdir(tmp_path)
    implicit = main(command + ["--json"])
    implicit_output = capsys.readouterr()
    explicit = main(["--db", DEFAULT_DB_PATH, *command, "--json"])
    explicit_output = capsys.readouterr()
    assert implicit == explicit
    implicit_report = json.loads(implicit_output.out)
    explicit_report = json.loads(explicit_output.out)
    implicit_report.pop("read_at", None)
    explicit_report.pop("read_at", None)
    assert implicit_report == explicit_report
    assert not (tmp_path / ".peerhub").exists()


def test_missing_guard_accepts_namespace_without_json(tmp_path, monkeypatch, capsys):
    import peerhub.cli.app as app
    parser = app.build_parser()
    args = parser.parse_args(["peer", "register", "--peer", "local"])
    del args.json
    monkeypatch.setattr(parser, "parse_args", lambda argv: args)
    monkeypatch.setattr(app, "build_parser", lambda **kw: parser)
    monkeypatch.chdir(tmp_path)
    assert main(["peer", "register", "--peer", "local"]) == 5
    assert "[store_missing]" in capsys.readouterr().err
    assert not (tmp_path / ".peerhub").exists()


def test_collection_miss_is_guarded_before_corestore(tmp_path, monkeypatch, capsys):
    import argparse
    import peerhub.cli.app as app
    parser = app.build_parser()
    args = argparse.Namespace(db=DEFAULT_DB_PATH, subcommand="collection", action="create", json=False)
    monkeypatch.setattr(parser, "parse_args", lambda argv: args)
    monkeypatch.setattr(app, "build_parser", lambda **kw: parser)
    monkeypatch.chdir(tmp_path)
    assert main(["collection", "create"]) == 5
    assert "[store_missing]" in capsys.readouterr().err
    assert not (tmp_path / ".peerhub").exists()
