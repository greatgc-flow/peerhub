"""Current CLI selection and archive rollback preserve authoritative data."""
import hashlib
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from peerhub.core.models import Peer
from peerhub.core.store import CoreStore

REPO = Path(__file__).resolve().parents[3]


def peerhub(args, cwd, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("PEERHUB_CLI")}
    env.update(PYTHONPATH=str(REPO), **extra)
    return subprocess.run([sys.executable, "-m", "peerhub.cli", *args], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=120)


def test_default_and_explicit_core_are_identical_and_do_not_create_files(tmp_path):
    default = peerhub(["--help"], tmp_path)
    assert default.returncode == 0 and "ask" in default.stdout and "legacy-import" in default.stdout
    assert peerhub(["--help"], tmp_path, PEERHUB_CLI="core").stdout == default.stdout
    assert peerhub(["--help"], tmp_path, PEERHUB_CLI="").stdout == default.stdout
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("selector", ["m1", "M1", "v1", "legacy ", "1", "none", "core,legacy"])
def test_invalid_selector_fails_without_side_effects(tmp_path, selector):
    result = peerhub(["--help"], tmp_path, PEERHUB_CLI=selector)
    assert result.returncode == 2 and result.stdout == ""
    assert "PEERHUB_CLI" in result.stderr and "allowed values: core" in result.stderr
    assert "Traceback" not in result.stderr and list(tmp_path.iterdir()) == []


def test_archived_cli_is_not_an_in_process_rollback(tmp_path):
    result = peerhub(["--help"], tmp_path, PEERHUB_CLI="legacy")
    assert result.returncode == 2 and result.stdout == ""
    assert "legacy/v0-main-final" in result.stderr and "retired" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_report_mode_preserves_stdout(tmp_path):
    quiet = peerhub(["--help"], tmp_path)
    report = peerhub(["--help"], tmp_path, PEERHUB_CLI_REPORT="1")
    assert report.stdout == quiet.stdout
    assert report.stderr.strip() == "peerhub: cli selector=core (source: default)"
    report = peerhub(["--help"], tmp_path, PEERHUB_CLI_REPORT="1", PEERHUB_CLI="core")
    assert report.stderr.strip() == "peerhub: cli selector=core (source: env)"


def test_only_one_public_console_script_is_packaged():
    scripts = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    assert scripts == {"peerhub": "peerhub.cli:main"}


def test_retired_selection_cannot_modify_a_populated_store(tmp_path):
    db = tmp_path / "core.db"
    CoreStore(db).register_peer(Peer(peer_id="original"))
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    retired = peerhub(["--db", str(db), "peer", "get", "--peer", "original"], tmp_path, PEERHUB_CLI="legacy")
    assert retired.returncode == 2
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    current = peerhub(["--db", str(db), "peer", "get", "--peer", "original"], tmp_path)
    assert current.returncode == 0 and "original" in current.stdout


def test_cli_module_has_no_legacy_proxy_or_runtime_imports(tmp_path):
    code = (
        "import sys, peerhub.cli as cli;"
        "assert not hasattr(cli, 'legacy_main');"
        "assert not hasattr(cli, 'create_runtime');"
        "assert type(cli).__name__ == 'module';"
        "assert not any(m.startswith(('peerhub.application','peerhub.dispatch','peerhub.persistence',"
        "'peerhub.telemetry','peerhub.cli._legacy')) for m in sys.modules);"
        "print('ok')"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path,
                            env={**os.environ, "PYTHONPATH": str(REPO)}, capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout.strip() == "ok", result.stderr
