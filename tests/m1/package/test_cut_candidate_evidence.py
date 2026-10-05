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
RELEASE_POLICY = REPO / "docs/m1_impl/release-policy.json"
TTL = json.loads(RELEASE_POLICY.read_text(encoding="utf-8"))["policy_ttl_seconds"]
VERSION = ev.source_version(REPO)


def make_dist(path, payload=b"wheel-bytes"):
    path.mkdir(exist_ok=True)
    (path / f"peerhub-{VERSION}-py3-none-any.whl").write_bytes(payload)
    (path / f"peerhub-{VERSION}.tar.gz").write_bytes(payload + b"-sdist")
    return path


def policy_with(tmp_path, name, **over):
    p = json.loads(POLICY.read_text(encoding="utf-8"))
    p.update(over)
    f = tmp_path / name
    f.write_text(json.dumps(p, indent=2), encoding="utf-8")
    return f


def release_policy(tmp_path, name, ttl="keep", **over):
    p = json.loads(RELEASE_POLICY.read_text(encoding="utf-8"))
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
                                 release_policy=kw.pop("release", RELEASE_POLICY), now=NOW, **kw)


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
    ttl = TTL
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
        j = env.evidence(policy=policy_with(tmp_path, "other-policy.json", on_timeout="ESCALATE"))
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


def test_cut_015_threshold_is_release_policy_driven_same_evidence_flips(env, tmp_path):
    j = env.evidence(age=7200)  # one piece of evidence, two release policies
    short = release_policy(tmp_path, "short.json", ttl=3600)
    long = release_policy(tmp_path, "long.json", ttl=86400)
    assert env.manifest([j], release=short)["release_ready"] is False
    assert "is stale" in holds(env.manifest([j], release=short))
    assert env.manifest([j], release=long)["release_ready"] is True


@pytest.mark.parametrize("ttl", [None, 0, -5, "3600", True])
def test_cut_016_missing_or_invalid_release_policy_threshold_is_refused(env, tmp_path, ttl):
    bad = release_policy(tmp_path, "bad.json", ttl=ttl)
    with pytest.raises(ValueError, match="policy_ttl_seconds"):
        env.manifest([env.evidence(age=1)], release=bad)
    with pytest.raises(ValueError, match="--release-policy"):
        env.manifest([env.evidence(age=1)], release=None)


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
    rp = ["--release-policy", str(RELEASE_POLICY)]
    run = lambda *a: subprocess.run([sys.executable, "-m", "tools.m1_release_evidence", *a], cwd=REPO, capture_output=True, text=True)
    assert run("--stamp-junit", str(j), "--commit", SHA, "--dist", str(env.dist)).returncode == 0
    ok = run("--junit", str(j), "--dist", str(env.dist), "--out", str(tmp_path / "o1"), "--commit", SHA, "--candidate", "--policy-file", str(POLICY), *rp)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert json.loads((tmp_path / "o1/evidence.json").read_text(encoding="utf-8"))["candidate"]["source_revision"] == SHA
    other = run("--junit", str(j), "--dist", str(env.dist), "--out", str(tmp_path / "o2"), "--commit", "d" * 40, "--candidate", *rp)
    assert other.returncode == 1 and "different candidate" in other.stdout
    assert run("--ttl-seconds", "5").returncode == 2  # no time threshold on the command line
    no_rp = run("--junit", str(j), "--dist", str(env.dist), "--out", str(tmp_path / "o3"), "--commit", SHA, "--candidate")
    assert no_rp.returncode == 2 and "--release-policy" in no_rp.stderr  # no threshold default in code
    missing = run("--stamp-junit", str(tmp_path / "nope.xml"), "--commit", SHA)
    assert missing.returncode == 0 and "was not produced" in missing.stderr  # a gate that wrote no junit must not crash the stamp step


def test_cut_020_no_hard_coded_time_threshold_in_the_tool():
    src = (REPO / "tools/m1_release_evidence.py").read_text(encoding="utf-8")
    ttl = TTL
    assert str(ttl) not in src and "--ttl" not in src and not re.search(r"timedelta\(|\b(3600|86400|604800)\b", src)


GATE_JOBS = (("gate-g0-fast", ["g0"]), ("gate-g1-core", ["g1"]), ("gate-g2-m1", ["g2"]), ("live-validation", ["g3", "g3-slow", "g3-e2e"]),
             ("build", ["g4"]))


def workflow_problems(wf):
    """Structural contract of publish.yml for candidate evidence; returns human-readable problems (empty = ok)."""
    bad = []
    ev_run = next((st["run"] for st in wf["jobs"]["release-evidence"]["steps"] if "m1_release_evidence" in st.get("run", "")), "")
    for need in ("--candidate", f"--policy-file {ev.POLICY_REL}", "--release-policy docs/m1_impl/release-policy.json"):
        if need not in ev_run:
            bad.append(f"evidence step lacks {need}")
    if "--ttl" in ev_run:
        bad.append("evidence step passes a ttl")
    for job, names in GATE_JOBS:
        steps = wf["jobs"][job]["steps"]
        idx_stamp = [i for i, st in enumerate(steps) if "--stamp-junit" in st.get("run", "")]
        if len(idx_stamp) != 1:
            bad.append(f"{job}: needs exactly one stamp step")
            continue
        st = steps[idx_stamp[0]]
        if st.get("if") != "always()":
            bad.append(f"{job}: stamp step must run if: always()")
        for n in names:
            if f"--stamp-junit junit/{n}.xml" not in st["run"]:
                bad.append(f"{job}: junit/{n}.xml is not stamped")
            if not any(f"--junitxml=junit/{n}.xml" in x.get("run", "") for x in steps[:idx_stamp[0]]):
                bad.append(f"{job}: no test step before the stamp writes junit/{n}.xml")
            if f"junit/{n}.xml" not in ev_run:
                bad.append(f"evidence step does not read junit/{n}.xml")
        up = [i for i, x in enumerate(steps) if "upload-artifact" in x.get("uses", "") and "junit" in str(x.get("with", {}).get("name", ""))]
        if not up or min(up) < idx_stamp[0]:
            bad.append(f"{job}: junit must be uploaded after the stamp step")
        if job == "build":
            build = [i for i, x in enumerate(steps) if "python -m build" in x.get("run", "")]
            if not build or idx_stamp[0] < build[0] or "--dist dist" not in st["run"]:
                bad.append("build: G4 must be stamped after the build with --dist dist")
    return bad


def test_cut_021_publish_workflow_binds_and_checks_the_candidate():
    wf = yaml.safe_load((REPO / ".github/workflows/publish.yml").read_text(encoding="utf-8"))
    assert workflow_problems(wf) == []  # positive control


def _mutate(kind):
    import copy
    wf = copy.deepcopy(yaml.safe_load((REPO / ".github/workflows/publish.yml").read_text(encoding="utf-8")))
    ev_step = next(st for st in wf["jobs"]["release-evidence"]["steps"] if "m1_release_evidence" in st.get("run", ""))
    live = wf["jobs"]["live-validation"]["steps"]
    build = wf["jobs"]["build"]["steps"]
    if kind == "no-release-policy":
        ev_step["run"] = ev_step["run"].replace("--release-policy docs/m1_impl/release-policy.json", "")
    elif kind == "g3-junit-not-written":
        for st in live:
            st["run"] = st.get("run", "").replace("--junitxml=junit/g3.xml", "")
    elif kind == "stamp-not-always":
        next(st for st in live if "--stamp-junit" in st.get("run", "")).pop("if")
    elif kind == "g4-stamp-before-build":
        i = next(i for i, st in enumerate(build) if "--stamp-junit" in st.get("run", ""))
        build.insert(0, build.pop(i))
    elif kind == "g4-upload-before-stamp":
        i = next(i for i, st in enumerate(build) if "upload-artifact" in st.get("uses", "") and "junit" in str(st["with"]["name"]))
        j = next(i for i, st in enumerate(build) if "--stamp-junit" in st.get("run", ""))
        build.insert(j, build.pop(i))
    elif kind == "g4-no-dist":
        next(st for st in build if "--stamp-junit" in st.get("run", ""))["run"] = "python -m tools.m1_release_evidence --stamp-junit junit/g4.xml --commit X"
    elif kind == "evidence-skips-g3-slow":
        ev_step["run"] = ev_step["run"].replace("--junit junit/g3-slow.xml", "")
    return wf


@pytest.mark.parametrize("kind", ["no-release-policy", "g3-junit-not-written", "stamp-not-always", "g4-stamp-before-build",
                                  "g4-upload-before-stamp", "g4-no-dist", "evidence-skips-g3-slow"])
def test_cut_022_mutated_workflows_are_caught(kind):
    assert workflow_problems(_mutate(kind)), kind


def edit_props(path, fn):
    import xml.etree.ElementTree as ET
    t = ET.parse(path)
    fn(t.getroot().find(".//properties"))
    t.write(path, encoding="utf-8", xml_declaration=True)


def test_cut_023_stripped_package_property_does_not_let_g4_evidence_pass_for_other_wheels(env, tmp_path):
    j = env.evidence(age=5)
    other = make_dist(tmp_path / "otherdist", b"other-wheels")
    kw = dict(repo_root=REPO, junit_paths=[j], dist_dir=other, commit=SHA, candidate=True, release_policy=RELEASE_POLICY, now=NOW)
    assert ev.build_manifest(**kw)["release_ready"] is False  # control: the untouched file is bound to env.dist only
    edit_props(j, lambda h: [h.remove(e) for e in list(h) if e.get("name") == "candidate.package_sha256"])
    m = ev.build_manifest(**kw)
    assert m["release_ready"] is False and m["evidence_bindings"][0]["status"] == "mismatched" and "id" in holds(m)
    assert env.manifest([j])["release_ready"] is False  # not even for its own dist: the id covers the packages


def test_cut_024_forged_id_or_edited_property_is_rejected(env):
    j = env.evidence(age=5)
    assert env.manifest([j])["release_ready"] is True
    edit_props(j, lambda h: [e.set("value", "0" * 64) for e in h if e.get("name") == "candidate.id"])
    m = env.manifest([j])
    assert m["release_ready"] is False and m["evidence_bindings"][0]["status"] == "mismatched" and "id" in holds(m)
    j2 = env.evidence(age=5)
    edit_props(j2, lambda h: [e.set("value", "f" * 64) for e in h if e.get("name") == "candidate.package_sha256"])  # id kept, digest edited
    assert env.manifest([j2])["release_ready"] is False


def test_cut_025_wheel_swapped_after_stamping_and_g4_reused_across_candidates(env):
    j = env.evidence(age=5)
    assert env.manifest([j])["release_ready"] is True
    wheel = next(env.dist.glob("*.whl"))
    wheel.write_bytes(b"swapped-after-stamping")
    m = env.manifest([j])
    assert m["release_ready"] is False and "package_sha256" in holds(m)
    wheel.write_bytes(b"wheel-bytes")
    assert env.manifest([j], commit="e" * 40)["release_ready"] is False  # same file, other source revision


def test_cut_026_source_scoped_evidence_alone_cannot_satisfy_the_package_gate(env):
    src = env.evidence(age=5, dist=None)  # stamped without package digests (like G0-G3)
    props = ev._bound_props(src)
    assert "package_sha256" not in props and re.fullmatch(r"[0-9a-f]{64}", props["id"])
    m = env.manifest([src])
    assert m["evidence_bindings"][0]["status"] == "accepted" and m["evidence_bindings"][0]["scope"] == "source"
    assert m["release_ready"] is False and "package digests of the dist being released" in holds(m)
    assert any("blocking test" in b and "(gate G4)" in b for b in m["blockers"])  # G4 tests need package-scoped files
    both = [env.evidence(age=5, dist=None), env.evidence(age=5)]
    assert env.manifest(both)["release_ready"] is True  # positive control: a package-scoped file is also present


def test_cut_027_missing_junit_file_is_a_hold_not_a_crash(env, tmp_path):
    m = env.manifest([env.evidence(age=5), tmp_path / "g3.xml"])
    assert [b["status"] for b in m["evidence_bindings"]] == ["accepted", "missing"] and "g3.xml is missing" in holds(m)
    assert m["release_ready"] is False
