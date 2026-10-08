"""The single public CLI: one console script, no selector, no side effects on --help."""
import hashlib
import os
import subprocess
import sys
import tomllib
from pathlib import Path

from peerhub.core.models import Peer
from peerhub.core.store import CoreStore

REPO = Path(__file__).resolve().parents[3]


def peerhub(args, cwd, **extra):
    env = {**os.environ, "PYTHONPATH": str(REPO), **extra}
    return subprocess.run([sys.executable, "-m", "peerhub.cli", *args], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=120)


def test_help_lists_the_commands_and_creates_no_files(tmp_path):
    result = peerhub(["--help"], tmp_path)
    assert result.returncode == 0 and "ask" in result.stdout
    assert "legacy-import" not in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_only_one_public_console_script_is_packaged():
    scripts = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    assert scripts == {"peerhub": "peerhub.cli:main"}


def test_reading_a_populated_store_does_not_modify_it(tmp_path):
    db = tmp_path / "core.db"
    CoreStore(db).register_peer(Peer(peer_id="original"))
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    current = peerhub(["--db", str(db), "peer", "get", "--peer", "original"], tmp_path)
    assert current.returncode == 0 and "original" in current.stdout
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


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
