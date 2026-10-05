"""Cutover gate item 7: rollback = the `peerhub` entrypoint selector (PEERHUB_CLI), never a data migration.
Real processes (sys.executable -m peerhub.cli); the independent oracles are literal help-tree markers and file/state hashes."""
import hashlib
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from tests.m1.harness.core import compute_state_digest

pytestmark = [pytest.mark.integration]

REPO = Path(__file__).resolve().parents[3]
LEGACY_MARK = "Common workflows:"   # only the legacy help carries this epilog
M1_MARK = "legacy-import"           # only the M1 tree has this subcommand


def peerhub(args, cwd, **env):
    e = {k: v for k, v in os.environ.items() if not k.startswith("PEERHUB_CLI")}
    e["PYTHONPATH"] = str(REPO)  # run THIS checkout, not whatever is installed
    e.update(env)
    return subprocess.run([sys.executable, "-m", "peerhub.cli", *args], cwd=str(cwd), env=e, capture_output=True, text=True, timeout=120)


def m1(db, *args):
    return subprocess.run([sys.executable, "-m", "peerhub.m1_cli", "--db", str(db), *args], cwd=str(db.parent), capture_output=True, text=True,
                          timeout=120, env={**os.environ, "PYTHONPATH": str(REPO)})


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def tree(root):
    return sorted(p.relative_to(root).as_posix() for p in Path(root).rglob("*"))


def test_cut_001_safe_default_is_legacy_when_unset(tmp_path):
    cp = peerhub(["--help"], tmp_path)
    assert cp.returncode == 0 and LEGACY_MARK in cp.stdout and M1_MARK not in cp.stdout
    assert tree(tmp_path) == []  # selecting and printing help creates nothing


def test_cut_002_empty_value_is_the_default_and_explicit_legacy_matches(tmp_path):
    base = peerhub(["--help"], tmp_path)
    assert peerhub(["--help"], tmp_path, PEERHUB_CLI="").stdout == base.stdout
    explicit = peerhub(["--help"], tmp_path, PEERHUB_CLI="legacy")
    assert explicit.returncode == 0 and explicit.stdout == base.stdout and LEGACY_MARK in explicit.stdout


def test_cut_003_explicit_m1_forwards_argv_to_the_m1_cli(tmp_path):
    cp = peerhub(["--help"], tmp_path, PEERHUB_CLI="m1")
    assert cp.returncode == 0 and M1_MARK in cp.stdout and LEGACY_MARK not in cp.stdout
    direct = subprocess.run([sys.executable, "-m", "peerhub.m1_cli", "--help"], cwd=str(tmp_path), capture_output=True, text=True,
                            env={**os.environ, "PYTHONPATH": str(REPO)})
    assert M1_MARK in direct.stdout
    bad = peerhub(["no-such-subcommand"], tmp_path, PEERHUB_CLI="m1")  # argv really reaches the M1 parser (exit 2 from argparse)
    assert bad.returncode == 2 and "usage:" in bad.stderr and "Traceback" not in bad.stderr


@pytest.mark.parametrize("value", ["M1", "v1", "legacy ", "1", "none", "m1,legacy"])
def test_cut_004_invalid_value_fails_fast_exit_2_naming_allowed_values(tmp_path, value):
    cp = peerhub(["--help"], tmp_path, PEERHUB_CLI=value)
    assert cp.returncode == 2 and cp.stdout == ""
    lines = cp.stderr.strip().splitlines()
    assert len(lines) == 1 and "legacy" in lines[0] and "m1" in lines[0] and "PEERHUB_CLI" in lines[0]
    assert "Traceback" not in cp.stderr and tree(tmp_path) == []


def test_cut_005_report_mode_names_selector_and_source_without_changing_stdout(tmp_path):
    quiet = peerhub(["--version"], tmp_path)
    cp = peerhub(["--version"], tmp_path, PEERHUB_CLI_REPORT="1")
    assert cp.stdout == quiet.stdout and quiet.stderr == ""
    assert cp.stderr.strip() == "peerhub: cli selector=legacy (source: default)"
    cp = peerhub(["--help"], tmp_path, PEERHUB_CLI_REPORT="1", PEERHUB_CLI="m1")
    assert cp.stderr.strip() == "peerhub: cli selector=m1 (source: env)"


def test_cut_006_shipped_default_is_legacy_and_scripts_are_unchanged():
    from peerhub.cli.selector import ALLOWED_SELECTORS, DEFAULT_CLI_SELECTOR
    assert DEFAULT_CLI_SELECTOR == "legacy" and ALLOWED_SELECTORS == ("legacy", "m1")  # the flip is an owner decision, not this change
    scripts = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["scripts"]
    assert scripts["peerhub"] == "peerhub.cli:main" and scripts["peerhub-m1"] == "peerhub.m1_cli:main"


def test_cut_007_rollback_flip_both_ways_leaves_the_populated_m1_store_untouched(tmp_path):
    db = tmp_path / "m1store" / "core.db"
    db.parent.mkdir()
    for args in (["peer", "register", "--id", "peer:a", "--name", "A"], ["peer", "register", "--id", "peer:b"],
                 ["stream", "create", "--id", "s1", "--title", "T", "--members", "peer:a", "peer:b"],
                 ["record", "append", "--stream", "s1", "--author", "peer:a", "--kind", "note", "--body", '{"x": 1}', "--idemp-key", "k1",
                  "--created-at", "2026-01-01T00:00:00Z"]):
        cp = m1(db, *args)
        assert cp.returncode == 0, (args, cp.stderr)
    h0, d0, files0 = sha(db), compute_state_digest(db), tree(db.parent)
    readonly = [["stream", "show", "--id", "s1"], ["record", "read", "--stream", "s1"], ["peer", "get", "--id", "peer:a"]]
    work = tmp_path / "cwd"
    work.mkdir()
    outs = {}
    for selector in ("m1", "legacy", "m1", "legacy", "m1"):  # flip both ways, repeatedly
        for args in readonly:
            cp = peerhub(["--db", str(db), *args], work, PEERHUB_CLI=selector)
            if selector == "m1":
                assert cp.returncode == 0 and "Traceback" not in cp.stderr, cp.stderr
                outs.setdefault(tuple(args), []).append(cp.stdout)
            else:  # the legacy tree does not own the M1 store: it rejects the M1-only --db option cleanly and writes nothing
                assert cp.returncode == 2 and LEGACY_MARK not in cp.stdout and "Traceback" not in cp.stderr
        assert (sha(db), compute_state_digest(db), tree(db.parent)) == (h0, d0, files0), f"M1 store changed after PEERHUB_CLI={selector}"
    assert all(len(set(v)) == 1 and v[0].strip() for v in outs.values())  # same answers before and after the flips
    assert "peer:a" in outs[("peer", "get", "--id", "peer:a")][0]
    assert tree(work) == []  # the flip itself created no file anywhere in the working directory
