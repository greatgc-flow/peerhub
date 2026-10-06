"""Wave 6 E2E-009: Core stays useful with ALL first-party extensions disabled (real CLI, one fresh process per command = restarts)."""
import json
import subprocess
import sys

import pytest

from tests.communication.spec import ROOT

pytestmark = [pytest.mark.integration]
CT = "2026-10-01T00:00:00Z"


def cli(db, *args, block=True, timeout=120):
    mod = ["-m", "tests.communication.harness.core_only"] if block else ["-m", "peerhub.cli.app"]
    return subprocess.run([sys.executable, *mod, "--db", str(db), *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout)


def ok(r):
    assert r.returncode == 0, r.stderr[-800:]
    assert 'LOADED_EXTENSIONS:[]' in r.stderr, r.stderr[-400:]  # nothing from peerhub.extensions was imported
    return json.loads(r.stdout)


@pytest.mark.catalog_id("E2E-009")
def test_e2e_009_core_remains_useful_with_all_extensions_disabled(tmp_path):
    db = tmp_path / "core.db"
    probe = subprocess.run([sys.executable, "-c", "import sys; sys.meta_path.insert(0, __import__('tests.communication.harness.core_only', fromlist=['x']).ExtensionsDisabled());"
                            "import peerhub.extensions.bridge"], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert probe.returncode != 0 and "extension disabled" in probe.stderr  # the blocker really blocks (control for the rest)
    for p in ("a", "b"):
        assert ok(cli(db, "peer", "register", "--id", p))["peer_id"] == p
    assert ok(cli(db, "stream", "create", "--id", "s", "--members", "a", "b"))["members"] == ["a", "b"]
    recs = [ok(cli(db, "record", "append", "--stream", "s", "--author", "a", "--kind", "message", "--body", json.dumps(f"m{i}"),
                   "--idemp-key", f"k{i}", "--created-at", CT)) for i in range(3)]
    assert [r["position"] for r in recs] == [1, 2, 3]
    again = ok(cli(db, "record", "append", "--stream", "s", "--author", "a", "--kind", "message", "--body", '"m1"', "--idemp-key", "k1", "--created-at", CT))
    assert again["record_id"] == recs[1]["record_id"]  # idempotent retry in a fresh process
    read = ok(cli(db, "record", "read", "--stream", "s", "--after", "1"))
    assert [(r["position"], r["body"]) for r in read] == [(2, "m1"), (3, "m2")]
    off = ok(cli(db, "offset", "get", "--peer", "b", "--stream", "s"))
    assert (off["read_through_position"], off["revision"]) == (0, 1)
    off = ok(cli(db, "offset", "advance", "--peer", "b", "--stream", "s", "--position", "2", "--revision", "1"))
    assert (off["read_through_position"], off["revision"]) == (2, 2)
    stale = cli(db, "offset", "advance", "--peer", "b", "--stream", "s", "--position", "3", "--revision", "1")
    assert stale.returncode == 3 and "CAS ERROR" in stale.stderr  # explicit CAS conflict, state unchanged
    off2 = ok(cli(db, "offset", "get", "--peer", "b", "--stream", "s"))  # restart: durable state survived
    assert (off2["read_through_position"], off2["revision"]) == (2, 2)
    # the optional Diag command reports explicitly that the extension is unavailable (no traceback, no side effects)
    d = cli(db, "diag", "health", "--stream", "s")
    assert d.returncode == 5 and "diagnostics extension" in d.stderr and "Traceback" not in d.stderr
    # positive control: with extensions enabled the same command works against the same store
    full = cli(db, "diag", "health", "--stream", "s", block=False)
    assert full.returncode == 0 and json.loads(full.stdout)["status"] == "OK"
