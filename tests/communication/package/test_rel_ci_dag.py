"""Wave 7: REL-005 (deterministic CI needs no provider quota) and REL-006 (publish is causally gated by the live job)."""
import copy
import json
import os
import re
import sys
import textwrap
import uuid

import pytest
import yaml

from tests.communication.harness import pkg_env
from tests.communication.harness.pkg_env import REPO

pytestmark = [pytest.mark.package, pytest.mark.release, pytest.mark.ci, pytest.mark.timeout(900)]

PROVIDER_CLIS = ("claude", "claude.cmd", "codex", "codex.cmd", "agy", "agy.exe")

# Keep the nested offline run focused on architecture boundaries, fake-peer
# delivery and recovery, and Bridge failures that must preserve durable state.
# The broad sweep also runs restart scenarios that need multiprocessing IPC.
_REL005_CASES = (
    "tests/communication/architecture/test_arch_core_boundary.py::test_arch_001_core_import_graph_forbids_extension_and_cli_dependencies",
    "tests/communication/architecture/test_arch_core_boundary.py::test_arch_002_core_boots_with_extension_tables_absent",
    "tests/communication/architecture/test_arch_extension_graph.py::test_extension_import_graph_matches_the_documented_allowlist",
    "tests/communication/bridge/test_cert_certainty.py::test_cert_003_reconcile_retry_survives_restart_and_never_double_authorizes",
    "tests/communication/e2e/test_cli_ask_bridge.py::test_uncertain_retry_never_spawns_again",
    "tests/communication/e2e/test_e2e_core_only.py::test_e2e_009_core_remains_useful_with_all_extensions_disabled",
    "tests/communication/e2e/test_e2e_fake_runtime.py::test_e2e_001_two_peers_one_adapter_one_stream",
    "tests/communication/e2e/test_e2e_fake_runtime.py::test_e2e_006_lost_session_creates_new_generation_and_reconstructs_from_durable_records",
    "tests/communication/e2e/test_e2e_fake_runtime.py::test_e2e_007_shared_pool_visible_in_diag_without_contaminating_core_peers",
    "tests/communication/fault/test_flt_bridge.py::test_flt_007_claim_expiring_mid_execution_fences_stale_owner_finalize_and_offset",
)


def _wf(name):
    workflow = yaml.safe_load((REPO / ".github/workflows" / name).read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    return workflow


def _needs(job):
    n = job.get("needs", [])
    return [n] if isinstance(n, str) else list(n)


# ----------------------------------------------------------------------------- workflow DAG simulator (GitHub semantics subset)
def runs(wf, job, results, event="release", _seen=()):
    """Does `job` run? Fail closed on unknown needs, cycles and expressions this simulator does not understand."""
    if job in _seen or job not in wf["jobs"]:
        return False
    j = wf["jobs"][job]
    cond = str(j.get("if", "")).strip()
    bypass = bool(re.search(r"\b(always|failure|cancelled)\(\)", cond))
    if not bypass:
        for n in _needs(j):
            if effective(wf, n, results, event, (*_seen, job)) != "success":
                return False
    if cond and not bypass:
        if cond == "github.event_name == 'release'":
            return event == "release"
        raise AssertionError(f"simulator does not understand condition {cond!r} on job {job}")
    return True


def effective(wf, job, results, event, _seen=()):
    if not runs(wf, job, results, event, _seen):
        return "skipped"
    out = results.get(job, "success")
    if out == "failure" and wf["jobs"][job].get("continue-on-error"):
        return "success"  # GitHub: a failed continue-on-error job does not fail its dependents
    return out


def closure(wf, job):
    out, stack = set(), list(_needs(wf["jobs"][job]))
    while stack:
        n = stack.pop()
        if n not in out:
            out.add(n)
            stack.extend(_needs(wf["jobs"][n]) if n in wf["jobs"] else [])
    return out


# ----------------------------------------------------------------------------- REL-006
def test_rel_006_publish_job_cannot_run_when_the_live_provider_gate_fails():
    wf = _wf("publish.yml")
    assert "live-validation" in closure(wf, "publish") and "build" in closure(wf, "publish")
    ok = {"live-validation": "success", "build": "success"}
    assert runs(wf, "publish", ok) is True  # positive control: all green publishes on a release
    assert runs(wf, "publish", ok, event="workflow_dispatch") is False  # dispatch is a dry run
    for bad in ("failure", "cancelled", "skipped"):
        assert runs(wf, "publish", {**ok, "live-validation": bad}) is False, bad
    assert runs(wf, "publish", {**ok, "build": "failure"}) is False
    assert wf["jobs"]["live-validation"].get("continue-on-error") is not True  # a failed live gate must not count as success


def test_rel_006_live_gate_covers_every_advertised_provider_and_a_missing_gate_blocks_publish():
    wf = _wf("publish.yml")
    from tools import live_gate
    assert sorted(live_gate.PROVIDER_CLIS) == ["agy", "claude", "codex"]  # the local gate refuses to run unless every advertised provider CLI exists
    text = yaml.safe_dump(wf["jobs"]["live-validation"])
    assert "self-hosted" not in text and all(f"{n}.xml" in text for n in ("g3", "g3-slow", "g3-e2e"))  # remote job only fetches local evidence
    # missing live job: publish still names it in needs -> unresolved dependency -> fail closed (does not run)
    gone = copy.deepcopy(wf)
    del gone["jobs"]["live-validation"]
    assert runs(gone, "publish", {"build": "success"}) is False
    # the simulator really detects broken wiring (mutation fixtures; each must be reported as publishing despite a failed live gate)
    live_fail = {"live-validation": "failure", "build": "success"}
    m1 = copy.deepcopy(wf)
    m1["jobs"]["live-validation"]["continue-on-error"] = True
    m2 = copy.deepcopy(wf)
    m2["jobs"]["publish"]["needs"] = "build"
    m3 = copy.deepcopy(wf)
    m3["jobs"]["publish"]["if"] = "always() && github.event_name == 'release'"
    for name, broken in (("continue-on-error", m1), ("needs-dropped", m2), ("always()", m3)):
        assert runs(broken, "publish", live_fail) is True, name


# ----------------------------------------------------------------------------- REL-005
def _no_provider_env(tmp_path):
    """Env with no provider credentials, no provider CLIs on PATH, and every outbound socket blocked."""
    site = tmp_path / "site"
    site.mkdir()
    (site / "sitecustomize.py").write_text(textwrap.dedent("""
        import socket
        _LOOP = ("127.0.0.1", "::1", "localhost", "0.0.0.0", "")
        def _wrap(orig):
            def f(addr, *a, **k):
                host = addr[0] if isinstance(addr, tuple) else addr
                if host in _LOOP:
                    return orig(addr, *a, **k)
                raise OSError("network blocked by REL-005")
            return f
        socket.socket.connect = lambda self, addr, _o=socket.socket.connect: _wrap(_o.__get__(self))(addr)
        socket.socket.connect_ex = lambda self, addr, _o=socket.socket.connect_ex: _wrap(_o.__get__(self))(addr)
        socket.create_connection = _wrap(socket.create_connection)
        _gai = socket.getaddrinfo
        def _getaddrinfo(host, *a, **k):
            if host in _LOOP or host is None:
                return _gai(host, *a, **k)
            raise OSError("network blocked by REL-005")
        socket.getaddrinfo = _getaddrinfo
    """), encoding="utf-8")
    secret = re.compile(r"(KEY|TOKEN|SECRET|ANTHROPIC|OPENAI|GOOGLE|GEMINI|CLAUDE|CODEX|AGY|PASSWORD|CREDENTIAL)", re.I)
    env = {k: v for k, v in pkg_env.clean_env().items() if not secret.search(k)}
    for key in ("PYTEST_ADDOPTS", "PEERHUB_LIVE", "PEERHUB_SOAK", "PEERHUB_SOAK_SCALE", "PEERHUB_SOAK_EVIDENCE_DIR"):
        env.pop(key, None)
    sysroot = os.environ.get("SystemRoot", "")
    env["PATH"] = os.pathsep.join(
        [os.path.dirname(sys.executable)] + ([os.path.join(sysroot, "System32"), sysroot] if os.name == "nt" else ["/usr/bin", "/bin"]))
    env["PYTHONPATH"] = os.pathsep.join((str(site), str(REPO / "tests")))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _offline_pytest_nodeids(tmp_path, targets: tuple[str, ...]) -> set[str]:
    from _rel005_oracle import validate_pair

    env = _no_provider_env(tmp_path)
    probe = pkg_env.run([sys.executable, "-c", textwrap.dedent("""
        import shutil, socket, sys
        found = [n for n in %r if shutil.which(n)]
        try:
            socket.create_connection(("example.com", 80), timeout=2)
            net = "open"
        except OSError as e:
            net = "blocked" if "REL-005" in str(e) else "other:" + str(e)
        print(found, net)
    """) % (PROVIDER_CLIS,)], cwd=REPO, env=env)
    assert probe.stdout.strip() == "[] blocked", probe.stdout + probe.stderr  # the sandbox itself is real
    marker_expression = "not slow and not e2e and not live and not canary and not soak"
    common = [sys.executable, "-m", "pytest", "-q", "-ra", "-p", "no:cacheprovider", "-p", "no:xdist",
              "-p", "_rel005_oracle", "-c", str(REPO / "pyproject.toml"), "--rootdir", str(REPO),
              "--basetemp", str(tmp_path / "nested-pytest"),
              "-o", "addopts=", "-m", marker_expression, *targets]
    report_dir = tmp_path / "rel005-oracle"
    collect_file, execution_file = report_dir / "collection.json", report_dir / "execution.json"
    collect_nonce, execution_nonce = uuid.uuid4().hex, uuid.uuid4().hex
    collect_cmd = [*common, "--collect-only", "--rel005-report", str(collect_file),
                   "--rel005-nonce", collect_nonce]
    collect = pkg_env.run(collect_cmd, cwd=REPO, env=env, check=True)
    assert collect_file.is_file(), collect.stdout[-1000:] + collect.stderr[-1000:]
    execution_cmd = [*common, "--rel005-report", str(execution_file), "--rel005-nonce", execution_nonce]
    execution = pkg_env.run(execution_cmd, cwd=REPO, env=env, timeout=900, check=True)
    assert execution_file.is_file(), execution.stdout[-1500:] + execution.stderr[-1500:]
    collection = json.loads(collect_file.read_text(encoding="utf-8"))
    validate_pair(collection,
                  json.loads(execution_file.read_text(encoding="utf-8")),
                  expected_nonce=(collect_nonce, execution_nonce),
                  platform="windows" if os.name == "nt" else "posix",
                  collect_returncode=collect.returncode, execution_returncode=execution.returncode)
    return set(collection["item_nodeids"])


def test_rel_005_deterministic_suite_passes_without_provider_clis_credentials_or_network(tmp_path):
    assert _offline_pytest_nodeids(tmp_path, _REL005_CASES) == set(_REL005_CASES)


@pytest.mark.slow
def test_rel_005_broad_offline_sweep(tmp_path):
    targets = ("tests/communication/architecture", "tests/communication/e2e",
               "tests/communication/fault/test_flt_bridge.py")
    assert len(_offline_pytest_nodeids(tmp_path, targets)) >= 30


def test_rel_005_live_and_soak_tests_are_deselected_by_the_default_command(tmp_path, monkeypatch):
    t = tmp_path / "t"
    t.mkdir()
    (t / "test_live_gate.py").write_text("import pytest\n\n@pytest.mark.live\ndef test_would_need_a_provider():\n    assert False, 'live test ran'\n\n"
                                         "@pytest.mark.soak\ndef test_would_soak():\n    assert False, 'soak test ran'\n", encoding="utf-8")
    (t / "test_plain.py").write_text("def test_plain():\n    assert True\n", encoding="utf-8")
    # Exercise the repository defaults even if the invoking shell overrides them.
    monkeypatch.setenv("PYTEST_ADDOPTS", "-q -m live")
    env = pkg_env.clean_env({"PYTHONPATH": str(REPO / "tests")})
    env.pop("PYTEST_ADDOPTS", None)
    base = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "no:xdist",
            "-p", "_rel005_oracle", "-c", str(REPO / "pyproject.toml"), "--rootdir", str(t), str(t)]

    def run(label: str, *options: str):
        report_file = tmp_path / f"{label}.json"
        result = pkg_env.run([*base, "--rel005-report", str(report_file), "--rel005-nonce", label, *options],
                             cwd=t, env=env)
        assert report_file.is_file(), result.stdout + result.stderr
        return result, json.loads(report_file.read_text(encoding="utf-8"))

    plain_nodeid = "test_plain.py::test_plain"
    live_nodeid = "test_live_gate.py::test_would_need_a_provider"
    default, report = run("default")
    assert default.returncode == 0, default.stdout + default.stderr
    assert report["item_nodeids"] == [plain_nodeid]
    assert sorted(report["deselected_nodeids"]) == sorted([live_nodeid, "test_live_gate.py::test_would_soak"])
    assert [(row["nodeid"], row["when"], row["outcome"]) for row in report["test_reports"]] == [
        (plain_nodeid, phase, "passed") for phase in ("setup", "call", "teardown")
    ]

    live, live_report = run("live", "-m", "live")
    assert live_report["item_nodeids"] == [live_nodeid]
    assert live.returncode == 1 and "live test ran" in live.stdout, live.stdout + live.stderr


# ----------------------------------------------------------------------------- REL-006 (textual YAML mutations + gate DAG linkage)
PUBLISH_TEXT = (REPO / ".github/workflows/publish.yml").read_text(encoding="utf-8")
GATE_JOB = {"G0": "candidate-ci", "G1": "candidate-ci", "G2": "candidate-ci", "G3": "live-validation", "G4": "build",
            "G5": "release-evidence", "G7": "candidate-ci"}  # release-gates.json blocking gate -> publish.yml job that provides it
PUBLISH_NEEDS = "needs: [verify, candidate-ci, live-validation, build, matrix, release-evidence]"


def _mutate(text, how):
    if how == "continue_on_error":
        out = text.replace("  live-validation:\n", "  live-validation:\n    continue-on-error: true\n", 1)
    elif how == "needs_dropped":
        out = text.replace(PUBLISH_NEEDS, "needs: build", 1)
    elif how == "live_not_needed_list":
        out = text.replace(PUBLISH_NEEDS, "needs: [build]", 1)
    elif how == "always":
        marker = "\n  publish:"
        head, tail = text.split(marker, 1)  # the publish JOB's own condition (the build job has a step-level `if` too)
        out = head + marker + tail.replace("if: github.event_name == 'release'", "if: always() && github.event_name == 'release'", 1)
    elif how == "live_removed":
        a = text.index("  live-validation:")
        b = text.index("  build:")
        out = text[:a] + text[b:]
    else:
        raise AssertionError(how)
    assert out != text, how  # the mutation really changed the YAML text
    return out


@pytest.mark.parametrize("how", ["continue_on_error", "needs_dropped", "live_not_needed_list", "always"])
def test_rel_006_textually_mutated_real_workflow_yaml_is_caught(how):
    wf = yaml.safe_load(_mutate(PUBLISH_TEXT, how))
    live_fail = {"live-validation": "failure", "build": "success"}
    assert runs(yaml.safe_load(PUBLISH_TEXT), "publish", live_fail) is False  # control: the real file blocks
    assert runs(wf, "publish", live_fail) is True  # the mutated file would publish despite a failed live gate
    assert "live-validation" not in closure(wf, "publish") or how in ("continue_on_error", "always")


def test_rel_006_removed_live_job_leaves_publish_unrunnable_and_gate_linkage_fails():
    wf = yaml.safe_load(_mutate(PUBLISH_TEXT, "live_removed"))
    assert runs(wf, "publish", {"build": "success"}) is False  # dangling needs -> fail closed
    assert GATE_JOB["G3"] not in wf["jobs"]


def _gates():
    return {g["id"]: g for g in json.loads((REPO / "docs/m1_spec/08_LIFECYCLE/release-gates.json").read_text(encoding="utf-8"))["gates"]}


def _bypass(job):
    return bool(re.search(r"\b(always|failure|cancelled)\(\)", str(job.get("if", "")))) or job.get("continue-on-error") not in (None, False)


def _blocking_gate_ids():
    return sorted(g for g, v in _gates().items() if v["blocking"] is True)


def test_rel_006_every_blocking_gate_in_release_gates_json_maps_to_a_job_in_publish_needs_closure():
    gates = _gates()
    blocking = _blocking_gate_ids()
    assert set(blocking) == set(GATE_JOB), f"release-gates.json blocking gates {blocking} vs mapped {sorted(GATE_JOB)}"  # a new gate needs a job
    assert "G6" not in GATE_JOB and gates["G6"]["blocking"] is False  # soak never gates publish
    wf = yaml.safe_load(PUBLISH_TEXT)
    clo = closure(wf, "publish")
    for g in blocking:
        job = GATE_JOB[g]
        assert job in wf["jobs"], f"{g}: job {job} missing from publish.yml"
        assert job in clo, f"publish does not wait for {g} ({job})"
        assert job in _needs(wf["jobs"]["publish"]), f"{g} ({job}) not a direct need of publish"
        assert gates[g]["on_fail"] != "OBSERVE"
    for job in clo:  # no bypass anywhere in the closure or on publish itself
        assert not _bypass(wf["jobs"][job]), f"{job}: continue-on-error/always() bypass"
    assert not _bypass(wf["jobs"]["publish"])
    assert closure(wf, "publish") == {"verify", "candidate-ci", "live-validation", "build", "matrix", "release-evidence"}
    assert _needs(wf["jobs"]["verify"]) == []
    assert set(_needs(wf["jobs"]["live-validation"])) == {"verify", "candidate-ci"}
    assert set(_needs(wf["jobs"]["build"])) == {"verify", "candidate-ci"}
    assert {"matrix", "verify"} <= set(_needs(wf["jobs"]["publish"]))
    assert {"candidate-ci", "live-validation", "build", "matrix", "verify"} <= closure(wf, "release-evidence")
    # The candidate matrix provides G0/G1/G2 and the G7 invariants before release evidence is assembled.
    for g in ("G3", "G4", "G5"):
        for dep in gates[g]["depends_on"]:
            assert GATE_JOB[dep] in closure(wf, GATE_JOB[g]), f"{g} must wait for {dep}"
    ok = {GATE_JOB[g]: "success" for g in blocking}
    assert runs(wf, "publish", ok) is True  # positive control
    for g in blocking:
        for bad in ("failure", "cancelled", "skipped"):
            assert runs(wf, "publish", {**ok, GATE_JOB[g]: bad}) is False, (g, bad)
    for job in ("verify", "matrix"):
        for bad in ("failure", "cancelled", "skipped"):
            assert runs(wf, "publish", {**ok, job: bad}) is False, (job, bad)


@pytest.mark.parametrize("gate", sorted(GATE_JOB))
def test_rel_006_mutated_yaml_per_gate_is_caught(gate):
    job = GATE_JOB[gate]
    base = yaml.safe_load(PUBLISH_TEXT)
    ok = {GATE_JOB[g]: "success" for g in GATE_JOB}
    bad = {**ok, job: "failure"}
    assert runs(base, "publish", bad) is False  # control: the real file blocks
    coe = copy.deepcopy(base)
    coe["jobs"][job]["continue-on-error"] = True
    assert runs(coe, "publish", bad) is True and _bypass(coe["jobs"][job])  # bypass publishes despite the failed gate
    dropped = copy.deepcopy(base)  # gate removed from every needs list: no longer in the closure
    for j in dropped["jobs"].values():
        if "needs" in j:
            j["needs"] = [n for n in _needs(j) if n != job]
    assert job not in closure(dropped, "publish") and runs(dropped, "publish", bad) is True
    gone = copy.deepcopy(base)  # job removed: dangling needs fails closed and the mapping check would fail
    del gone["jobs"][job]
    assert runs(gone, "publish", ok) is False and job not in gone["jobs"]
    alw = copy.deepcopy(base)  # always() on publish itself
    alw["jobs"]["publish"]["if"] = "always() && github.event_name == 'release'"
    assert runs(alw, "publish", bad) is True and _bypass(alw["jobs"]["publish"])


def test_rel_006_live_job_selects_the_six_m1_live_tests_with_opt_in():
    wf = yaml.safe_load(PUBLISH_TEXT)
    live = wf["jobs"]["live-validation"]
    assert live["runs-on"] == "ubuntu-latest"  # provider logins exist only locally: the job fetches evidence, it never calls providers
    from tools import live_gate
    assert [(m, n) for m, _, n in live_gate.STAGES] == [("live", "g3.xml"), ("slow", "g3-slow.xml"), ("e2e", "g3-e2e.xml")]
    cmd = " ".join(f"-m {m}" for m, _, _ in live_gate.STAGES)
    import json
    ids = sorted(t["id"] for t in json.loads((REPO / "docs/m1_spec/06_GUIDES/TEST_SET/test-catalog.json").read_text(encoding="utf-8"))["tests"]
                 if t.get("live_provider"))
    assert ids == ["LIVE-004", "LIVE-005", "LIVE-006", "LIVE-007", "LIVE-008", "LIVE-AG-001", "LIVE-CC-001", "LIVE-CX-001"]
    col = pkg_env.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", "-m", "live", "tests/communication/live"], cwd=REPO)
    assert len(re.findall(r"::test_live_", col.stdout)) == 6, col.stdout  # `-m live` (not slow/e2e) really selects all six
    assert cmd.startswith("-m live")  # the first local stage selects the six; slow/e2e would deselect all six
    for marker, catalog_key in (("slow", "test_live_007_"), ("e2e", "test_live_008_")):
        stage = pkg_env.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
                            "-m", marker, "tests/communication/live"], cwd=REPO)
        assert stage.returncode == 0 and len(re.findall("::" + catalog_key, stage.stdout)) == 3, stage.stdout



def test_rel_006_publish_evidence_job_runs_the_release_evidence_tool_over_every_gate_junit():
    wf = yaml.safe_load(PUBLISH_TEXT)
    run = next(st["run"] for st in wf["jobs"]["release-evidence"]["steps"] if "release_evidence" in st.get("run", ""))
    for report in ("g3", "g3-slow", "g3-e2e", "g4"):
        assert f"--junit junit/{report}.xml" in run
    assert '--ci-junit-dir ci-junit --ci-run-url "$CI_RUN_URL"' in run
    assert "--g4-matrix-dir junit" in run
    ci = yaml.safe_load((REPO / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    matrix = ci["jobs"]["full-matrix"]
    assert not _bypass(matrix)  # CI runner is blocking
    suite = next(step for step in matrix["steps"] if "pytest -q -n auto" in step.get("run", ""))
    assert "g2-rc-matrix-" in suite["run"]


def test_rel_006_verify_reuses_source_catalog_evidence_and_scans_traceability_without_providers():
    wf = _wf("publish.yml")
    assert set(wf["jobs"]) == {"verify", "candidate-ci", "live-validation", "build", "matrix", "release-evidence", "publish"}
    verify = wf["jobs"]["verify"]
    assert _needs(verify) == []
    scans = [step for step in verify["steps"] if "run" in step]
    assert [step["run"] for step in scans] == ["python -m tools.traceability"]
    assert not scans[0].get("if") and not scans[0].get("continue-on-error")
    candidate = wf["jobs"]["candidate-ci"]["steps"]
    select = next(step for step in candidate if step.get("id") == "candidate-ci")
    download = next(step for step in candidate if step.get("with", {}).get("pattern") == "junit-g2-rc-matrix-*")
    check = next(step for step in candidate if "--verify-ci-junit" in step.get("run", ""))
    assert 'tools.release_ci --repo "$GITHUB_REPOSITORY" --commit "$candidate_sha"' in select["run"]
    assert download["with"]["run-id"] == "${{ steps.candidate-ci.outputs.run-id }}"
    assert '--verify-ci-junit ci-junit --verify-source-catalog --commit "$GITHUB_SHA"' in check["run"]
    assert candidate.index(select) < candidate.index(download) < candidate.index(check)
    assert all(not step.get("if") and not step.get("continue-on-error") for step in (select, download, check))
    package = next(step["run"] for step in wf["jobs"]["build"]["steps"] if "--junitxml=junit/g4.xml" in step.get("run", ""))
    assert "pytest -q -n auto" in package
    for job in wf["jobs"].values():
        for step in job["steps"]:
            run = step.get("run", "")
            assert not re.search(r"(?:^|[;&\n])\s*(?:claude|codex|agy)(?:\s|$)", run)
            assert "-m tools.live_gate" not in run
            assert "tests/communication/live" not in run


def test_ci_is_dispatchable_on_topic_branches_and_pins_external_actions():
    wf = _wf("ci.yml")
    triggers = wf.get("on", wf.get(True))
    assert "workflow_dispatch" in triggers
    assert triggers["pull_request"]["branches"] == ["main"]
    assert wf["permissions"] == {"contents": "read"}
    publish = _wf("publish.yml")
    publish_triggers = publish.get("on", publish.get(True))
    assert publish_triggers["release"]["types"] == ["published"]
    publisher = publish["jobs"]["publish"]
    assert publisher["if"] == "github.event_name == 'release'"
    assert publisher["environment"] == "pypi"
    assert publisher["permissions"] == {"id-token": "write"}
    assert "workflow_dispatch" in publish_triggers
    assert runs(publish, "release-evidence", {}, event="workflow_dispatch")
    assert not runs(publish, "publish", {}, event="workflow_dispatch")
    assert "id-token" not in publish["permissions"]
    assert all("id-token" not in job.get("permissions", {})
               for name, job in publish["jobs"].items() if name != "publish")
    for workflow_name in ("ci.yml", "soak.yml", "publish.yml"):
        workflow = _wf(workflow_name)
        steps = [step for job in workflow["jobs"].values() for step in job["steps"]]
        actions = [step["uses"] for step in steps if "uses" in step]
        assert actions, workflow_name
        for action in actions:
            assert re.fullmatch(r"[\w-]+/[\w-]+@[0-9a-f]{40}(?:\s+# [^\r\n]+)?", action), (workflow_name, action)
