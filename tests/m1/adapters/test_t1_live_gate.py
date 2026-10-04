"""T1/Wave 8 offline checks of the live gate: opt-in skip reason, default deselection, evidence helpers (no provider is called)."""
import json
import os
import subprocess
import sys

import pytest

from tests.m1.live import live_support as ls

pytestmark = [pytest.mark.integration, pytest.mark.security]


def run_pytest(*args, env_extra=None, drop=()):
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args], cwd=ls.ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)


def test_t1_live_default_run_deselects_live_tests():
    r = run_pytest("tests/m1/live", "-q", drop=(ls.OPT_IN_ENV,))
    assert "deselected" in r.stdout and "passed" not in r.stdout and "failed" not in r.stdout.replace("0 failed", "")


def test_t1_live_marker_without_opt_in_skips_with_machine_readable_reason():
    r = run_pytest("tests/m1/live", "-m", "live", "-rs", "-q", drop=(ls.OPT_IN_ENV,))
    assert r.returncode == 0, r.stdout
    assert r.stdout.count("LIVE-OPT-IN[env=PEERHUB_M1_LIVE;required=1;marker=live]") >= 1
    assert "6 skipped" in r.stdout and "passed" not in r.stdout and "failed" not in r.stdout
    # the opt-in value must be exactly "1" (anything else stays skipped)
    r0 = run_pytest("tests/m1/live", "-m", "live", "-q", env_extra={ls.OPT_IN_ENV: "true"})
    assert "6 skipped" in r0.stdout and "passed" not in r0.stdout


def test_t1_live_opt_in_reason_function():
    saved = os.environ.pop(ls.OPT_IN_ENV, None)
    try:
        assert ls.opt_in_reason().startswith("LIVE-OPT-IN[")
        os.environ[ls.OPT_IN_ENV] = "1"
        assert ls.opt_in_reason() is None  # positive control
    finally:
        os.environ.pop(ls.OPT_IN_ENV, None)
        if saved is not None:
            os.environ[ls.OPT_IN_ENV] = saved


def test_t1_live_profiles_are_lowest_tier_from_packaged_defaults():
    for kind in ls.PROVIDERS:
        p = ls.lowest_profile(kind)
        assert p["profile"] == f"{kind}.standard" and p["model"]
    assert ls.lowest_profile("cx")["effort"] == "low"
    assert len(ls.PROMPT.split()) <= 20


def test_t1_live_tree_snapshot_detects_mutation_and_ignores_evidence_dir(tmp_path):
    (tmp_path / "a.txt").write_text("1")
    (tmp_path / "ev").mkdir()
    snap = ls.tree_snapshot(tmp_path, ignore=(tmp_path / "ev",))
    (tmp_path / "ev" / "x.json").write_text("{}")
    assert ls.diff_snapshots(snap, ls.tree_snapshot(tmp_path, ignore=(tmp_path / "ev",))) == []  # designated artifact: allowed
    (tmp_path / "a.txt").write_text("22")
    (tmp_path / "b.txt").write_text("n")
    assert ls.diff_snapshots(snap, ls.tree_snapshot(tmp_path, ignore=(tmp_path / "ev",))) == ["a.txt", "b.txt"]


def test_t1_live_evidence_merge_is_atomic_and_keeps_other_providers(tmp_path):
    p = tmp_path / "ev" / "d.json"
    ls.merge_evidence("cc", "discovery", {"version": "1.2.3"}, p)
    ls.merge_evidence("ag", "canary", {"status": "delivered"}, p)
    ls.merge_evidence("cc", "canary", {"status": "uncertain"}, p)
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["providers"] == {"cc": {"discovery": {"version": "1.2.3"}, "canary": {"status": "uncertain"}}, "ag": {"canary": {"status": "delivered"}}}
    assert not list(p.parent.glob("*.tmp"))
