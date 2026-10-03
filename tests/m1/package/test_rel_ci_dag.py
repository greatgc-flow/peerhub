"""Wave 7: REL-005 (deterministic CI needs no provider quota) and REL-006 (publish is causally gated by the live job)."""
import copy
import os
import re
import sys
import textwrap

import pytest
import yaml

from tests.m1.harness import pkg_env
from tests.m1.harness.pkg_env import REPO

pytestmark = [pytest.mark.package, pytest.mark.release, pytest.mark.ci, pytest.mark.timeout(900)]

PROVIDER_CLIS = ("claude", "claude.cmd", "codex", "codex.cmd", "agy", "agy.exe")


def _wf(name):
    return yaml.safe_load((REPO / ".github/workflows" / name).read_text(encoding="utf-8"))


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
    text = yaml.safe_dump(wf["jobs"]["live-validation"])
    for exe in ("claude.cmd", "codex.cmd", "agy.exe"):
        assert exe in text, exe  # the live job verifies/exercises each advertised provider CLI
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
    sysroot = os.environ.get("SystemRoot", "")
    env["PATH"] = os.pathsep.join(
        [os.path.dirname(sys.executable)] + ([os.path.join(sysroot, "System32"), sysroot] if os.name == "nt" else ["/usr/bin", "/bin"]))
    env["PYTHONPATH"] = str(site)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def test_rel_005_deterministic_suite_passes_without_provider_clis_credentials_or_network(tmp_path):
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
    targets = ["tests/m1/architecture", "tests/m1/e2e", "tests/m1/fault/test_flt_bridge.py"]
    # oracle: how many tests exist there, counted by an ordinary collection in the developer environment
    col = pkg_env.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *targets], cwd=REPO)
    expected = int(re.search(r"(\d+) tests? collected", col.stdout).group(1))
    assert expected >= 30
    r = pkg_env.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *targets], cwd=REPO, env=env, timeout=900)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-1500:]
    assert re.search(rf"\b{expected} passed\b", r.stdout) and not re.search(r"\b(failed|error|skipped)\b", r.stdout), r.stdout[-500:]


def test_rel_005_live_and_soak_tests_are_deselected_by_the_default_command(tmp_path):
    t = tmp_path / "t"
    t.mkdir()
    (t / "test_live_gate.py").write_text("import pytest\n\n@pytest.mark.live\ndef test_would_need_a_provider():\n    assert False, 'live test ran'\n\n"
                                         "@pytest.mark.soak\ndef test_would_soak():\n    assert False, 'soak test ran'\n", encoding="utf-8")
    (t / "test_plain.py").write_text("def test_plain():\n    assert True\n", encoding="utf-8")
    base = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-c", str(REPO / "pyproject.toml"), "--rootdir", str(t), str(t)]
    r = pkg_env.run(base, cwd=t, env=pkg_env.clean_env())
    assert r.returncode == 0 and "1 passed" in r.stdout and "2 deselected" in r.stdout, r.stdout
    live = pkg_env.run([*base, "-m", "live"], cwd=t, env=pkg_env.clean_env())  # positive control: the marker really selects it
    assert live.returncode == 1 and "live test ran" in live.stdout
