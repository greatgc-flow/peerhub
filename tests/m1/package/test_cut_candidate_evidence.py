"""Cutover gate item 6: candidate-matched verification. Evidence must be a current PASS bound to the exact candidate
(source revision, package digests, selector default, policy, gate definitions); freshness comes ONLY from the policy file.
Synthetic JUnit + a fake dist directory keep this fast; oracles are literal expectations, not the tool's own functions."""
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
import yaml

from tests.m1.harness.pkg_env import REPO
from tests.m1.package.test_rel_007_evidence_bundle import all_ids, junit
from tools import m1_release_evidence as ev

pytestmark = [pytest.mark.package, pytest.mark.evidence]

SHA = "a" * 40
NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
POLICY = REPO / ev.POLICY_REL
VERSION = ev.source_version(REPO)


def make_dist(path, payload=b"wheel-bytes"):
    path.mkdir(exist_ok=True)
    (path / f"peerhub-{VERSION}-py3-none-any.whl").write_bytes(payload)
    (path / f"peerhub-{VERSION}.tar.gz").write_bytes(payload + b"-sdist")
    return path


def policy_with(tmp_path, name, ttl="keep", **over):
    p = json.loads(POLICY.read_text(encoding="utf-8"))
    if ttl is None:
        p.pop("policy_ttl_seconds", None)
    elif ttl != "keep":
        p["policy_ttl_seconds"] = ttl
    p.update(over)
    f = tmp_path / name
    f.write_text(json.dumps(p, indent=2), encoding="utf-8")
    return f


def alt_repo(tmp_path, selector="m1"):
    """A minimal repo root whose shipped selector default differs (stamps evidence for a different candidate)."""
    root = tmp_path / "altrepo"
    for rel in (ev.POLICY_REL, ev.GATES_REL):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / rel, root / rel)
    (root / ev.SELECTOR_SOURCE).parent.mkdir(parents=True, exist_ok=True)
    (root / ev.SELECTOR_SOURCE).write_text(f'DEFAULT_CLI_SELECTOR = "{selector}"\n', encoding="utf-8")
    return root


class Env:
    def __init__(self, tmp_path):
        self.tmp = tmp_path
        self.dist = make_dist(tmp_path / "dist")
        self.policy = POLICY
        self.n = 0

    def evidence(self, outcomes=None, *, age=0, commit=SHA, dist="same", repo=REPO, stamp=True, policy=None):
        self.n += 1
        j = junit(self.tmp / f"j{self.n}.xml", outcomes or {i: "passed" for i in all_ids(live=True)})
        if stamp:
            ev.stamp_junit(j, commit=commit, repo_root=repo, dist_dir=self.dist if dist == "same" else dist,
                           policy_path=policy or self.policy, at=NOW - timedelta(seconds=age))
        return j

    def manifest(self, junits, **kw):
        kw.setdefault("commit", SHA)
        return ev.build_manifest(repo_root=REPO, junit_paths=junits, dist_dir=self.dist, candidate=True, policy_path=kw.pop("policy", self.policy),
                                 now=NOW, **kw)


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def holds(m):
    return "\n".join(m["hold_reasons"])


def test_cut_010_positive_control_current_bound_all_green_is_release_ready(env):
    m = env.manifest([env.evidence(age=60)])
    assert m["blockers"] == [] and m["hold_reasons"] == [] and m["release_ready"] is True
    assert re.fullmatch(r"[0-9a-f]{64}", m["candidate"]["candidate_id"]) and m["candidate"]["selector_default"] == "legacy"
    assert m["candidate"]["source_revision"] == SHA and len(m["candidate"]["package_sha256"]) == 2
    assert [b["status"] for b in m["evidence_bindings"]] == ["accepted"]


def test_cut_011_stale_evidence_holds_with_an_explicit_reason(env):
    ttl = json.loads(POLICY.read_text(encoding="utf-8"))["policy_ttl_seconds"]
    m = env.manifest([env.evidence(age=ttl + 1)])
    assert m["release_ready"] is False and "is stale" in holds(m) and "j1.xml" in holds(m) and str(ttl) in holds(m)
    assert m["evidence_bindings"][0]["status"] == "stale"
    assert env.manifest([env.evidence(age=ttl - 5)])["release_ready"] is True  # just inside the window


@pytest.mark.parametrize("what,field", [("sha", "source_revision"), ("wheel", "package_sha256"), ("selector", "selector_default"),
                                         ("policy", "policy_sha256")])
def test_cut_012_evidence_for_another_candidate_is_rejected(env, tmp_path, what, field):
    if what == "sha":
        j = env.evidence(commit="b" * 40)
    elif what == "wheel":
        j = env.evidence(dist=make_dist(tmp_path / "otherdist", b"another-wheel"))
    elif what == "selector":
        j = env.evidence(repo=alt_repo(tmp_path))
    else:
        j = env.evidence(policy=policy_with(tmp_path, "other-policy.json", ttl=604800, on_timeout="ESCALATE"))
    m = env.manifest([j])
    assert m["release_ready"] is False and "different candidate" in holds(m) and field in holds(m), m["blockers"][:3]
    assert m["evidence_bindings"][0]["status"] == "mismatched"
    assert any("blocking test" in b for b in m["blockers"])  # a rejected file contributes no PASS at all


def test_cut_013_missing_blocking_gate_evidence_holds_naming_the_gate(env):
    gate_of = {e["test_id"]: e["primary_gate"] for e in json.loads(
        (REPO / "docs/m1_spec/06_GUIDES/TEST_SET/TEST_RELEASE_GATE_MAP.json").read_text(encoding="utf-8"))["entries"]}
    outcomes = {i: "passed" for i in all_ids(live=True) if gate_of[i] != "G1"}
    m = env.manifest([env.evidence(outcomes, age=1)])
    assert m["release_ready"] is False and "HOLD: blocking gate G1 has no current PASS evidence" in holds(m)
    assert "gate G0 " not in holds(m)


@pytest.mark.parametrize("kind", ["skipped", "failed", "unbound"])
def test_cut_014_skipped_failed_and_unbound_evidence_is_never_a_pass(env, kind):
    ids = all_ids(live=True)
    if kind == "unbound":
        m = env.manifest([env.evidence(stamp=False)])
        assert "is unbound" in holds(m) and m["evidence_bindings"][0]["status"] == "unbound"
    else:
        outcomes = {i: "passed" for i in ids}
        outcomes[ids[0]] = kind
        m = env.manifest([env.evidence(outcomes, age=1)])
        assert any(f"blocking test {ids[0]}" in b for b in m["blockers"])
    assert m["release_ready"] is False


def test_cut_015_threshold_is_policy_driven_same_evidence_flips(env, tmp_path):
    age = 7200
    j = env.evidence(age=age, policy=env.policy)
    short = policy_with(tmp_path, "short.json", ttl=3600)
    long = policy_with(tmp_path, "long.json", ttl=86400)
    # the policy file hash is part of the candidate: stamp for each policy so only the threshold differs
    js = env.evidence(age=age, policy=short)
    jl = env.evidence(age=age, policy=long)
    assert env.manifest([js], policy=short)["release_ready"] is False
    assert "is stale" in holds(env.manifest([js], policy=short))
    assert env.manifest([jl], policy=long)["release_ready"] is True
    assert env.manifest([j], policy=long)["evidence_bindings"][0]["status"] == "mismatched"  # evidence minted under another policy


def test_cut_016_policy_without_a_threshold_cannot_establish_freshness(env, tmp_path):
    none = policy_with(tmp_path, "nottl.json", ttl=None)
    m = env.manifest([env.evidence(age=1, policy=none)], policy=none)
    assert m["release_ready"] is False and "policy_ttl_seconds" in holds(m)
    bad = policy_with(tmp_path, "zero.json", ttl=0)
    assert env.manifest([env.evidence(age=1, policy=bad)], policy=bad)["release_ready"] is False


def test_cut_017_candidate_id_is_deterministic_and_sensitive_to_every_field(env):
    base = dict(commit=SHA, package_sha256=["1" * 64, "2" * 64], selector="legacy", policy_path=POLICY, gates_path=REPO / ev.GATES_REL)
    f = ev.candidate_fields(**base)
    cid = ev.candidate_id(f)
    assert cid == ev.candidate_id(ev.candidate_fields(**{**base, "package_sha256": ["2" * 64, "1" * 64]}))  # order-independent, deterministic
    for k, v in (("commit", "c" * 40), ("package_sha256", ["1" * 64, "3" * 64]), ("selector", "m1")):
        assert ev.candidate_id(ev.candidate_fields(**{**base, k: v})) != cid, k
    assert ev.candidate_id({**f, "policy_sha256": "0" * 64}) != cid
    assert ev.candidate_id({**f, "gate_definition_sha256": "0" * 64}) != cid
    assert ev.candidate_id({**f, "policy_schema_version": 2}) != cid


def test_cut_018_manifest_without_candidate_mode_is_unchanged(env):
    m = ev.build_manifest(repo_root=REPO, junit_paths=[junit(env.tmp / "p.xml", {i: "passed" for i in all_ids(live=True)})], dist_dir=env.dist,
                          commit=SHA)
    assert "candidate" not in m and "hold_reasons" not in m and m["release_ready"] is True


def test_cut_019_cli_flow_stamp_then_candidate_with_policy_file(env, tmp_path):
    j = junit(tmp_path / "g.xml", {i: "passed" for i in all_ids(live=True)})
    run = lambda *a: subprocess.run([sys.executable, "-m", "tools.m1_release_evidence", *a], cwd=REPO, capture_output=True, text=True)
    assert run("--stamp-junit", str(j), "--commit", SHA, "--dist", str(env.dist)).returncode == 0
    ok = run("--junit", str(j), "--dist", str(env.dist), "--out", str(tmp_path / "o1"), "--commit", SHA, "--candidate", "--policy-file", str(POLICY))
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert json.loads((tmp_path / "o1/evidence.json").read_text(encoding="utf-8"))["candidate"]["source_revision"] == SHA
    other = run("--junit", str(j), "--dist", str(env.dist), "--out", str(tmp_path / "o2"), "--commit", "d" * 40, "--candidate")
    assert other.returncode == 1 and "different candidate" in other.stdout
    assert run("--ttl-seconds", "5").returncode == 2  # no time threshold on the command line


def test_cut_020_no_hard_coded_time_threshold_in_the_tool():
    src = (REPO / "tools/m1_release_evidence.py").read_text(encoding="utf-8")
    ttl = json.loads(POLICY.read_text(encoding="utf-8"))["policy_ttl_seconds"]
    assert str(ttl) not in src and "--ttl" not in src and not re.search(r"timedelta\(|\b(3600|86400|604800)\b", src)


def test_cut_021_publish_workflow_binds_and_checks_the_candidate():
    wf = yaml.safe_load((REPO / ".github/workflows/publish.yml").read_text(encoding="utf-8"))
    run = next(st["run"] for st in wf["jobs"]["release-evidence"]["steps"] if "m1_release_evidence" in st.get("run", ""))
    assert "--candidate" in run and f"--policy-file {ev.POLICY_REL}" in run and "--ttl" not in run
    for job, g in (("gate-g0-fast", "g0"), ("gate-g1-core", "g1"), ("gate-g2-m1", "g2"), ("live-validation", "g3"), ("build", "g4")):
        stamps = [st for st in wf["jobs"][job]["steps"] if "--stamp-junit" in st.get("run", "")]
        assert len(stamps) == 1 and f"junit/{g}.xml" in stamps[0]["run"] and stamps[0].get("if") == "always()", job
    assert "--dist dist" in next(st["run"] for st in wf["jobs"]["build"]["steps"] if "--stamp-junit" in st.get("run", ""))
