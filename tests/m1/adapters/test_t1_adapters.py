"""T1: real CLI adapters (cc/cx/ag) behind RuntimeTarget, exercised against python fake CLIs (no real provider, deterministic)."""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from peerhub.extensions.adapters import CliRuntimeTarget, Sanitizer, process, run_bounded
from peerhub.extensions.bridge import PrespawnError, RuntimeTargetError
from tests.m1.adapters.conftest import FAKE, calls
from tests.m1.bridge_helpers import bseed, delivery_rows, kinds, offset_row, responses, sql
from tests.m1.control_helpers import ctl, running
from tests.m1.harness.bridge import BridgeHarness

pytestmark = [pytest.mark.integration, pytest.mark.bridge, pytest.mark.security]
KINDS = ["cc", "cx", "ag"]
SECRET = "Zq9-hunter2-s3cr3t-value"


def rec(body="Reply with OK", rid="r1"):
    return SimpleNamespace(record_id=rid, body=body, author_peer_id="a")


def pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True).stdout
        return f" {pid} ".encode() in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def drain(gen):
    return list(gen)


def bridge_cycle(tmp_path, adapter):
    h = BridgeHarness(tmp_path / "ws")
    (r,) = bseed(h)
    return h, r, h.delivery_cycle("b", "s", adapter)


def exited(a):
    return next(e for e in a.evidence if e["event"] == "exited")


# ---------------------------------------------------------------- success
@pytest.mark.parametrize("kind", KINDS)
def test_t1_success_events_and_invocation_shape(mk, kind):
    a = mk(kind, model="m-test", effort="low" if kind != "ag" else None)
    ev = drain(a.deliver(a.create_session(), rec(), []))
    assert [e[0] for e in ev] == ["started", "terminal"] and ev[1][1] == {"response": "OK"}
    (c,) = calls(mk.log)
    assert os.path.samefile(c["cwd"], mk.ws)
    assert "Reply with OK" in c["prompt"]
    if kind == "ag":
        assert c["argv"][:2] == ["-p", "Reply with OK"] and "--model" in c["argv"]
    else:
        assert "Reply with OK" not in " ".join(c["argv"])  # prompt travels on stdin, never in argv
    assert a.binding() == ("m-test/low" if kind != "ag" else "m-test/default")


@pytest.mark.parametrize("kind", KINDS)
def test_t1_bridge_roundtrip_is_terminal_with_durable_response(tmp_path, mk, kind):
    h, r, res = bridge_cycle(tmp_path, mk(kind))
    assert res.status == "delivered" and res.certainty == "TERMINAL"
    (resp,) = responses(h)
    assert json.loads(resp[4]) == {"response": "OK"} or json.loads(resp[4]) == "OK"
    assert kinds(h, delivery_rows(h, r.record_id)[-1][0])[-3:] == ["terminal", "response_appended", "offset_acked"]
    assert offset_row(h)[0] == r.position


def test_t1_prompt_is_passed_literally_without_shell(mk):
    nasty = "a & b; $(whoami) `x` | > out.txt \"q\" %PATH%"
    drain(mk("ag").deliver("s", rec(nasty), []))
    assert calls(mk.log)[0]["argv"][1] == nasty
    assert not (mk.ws / "out.txt").exists()


# ---------------------------------------------------------------- failure semantics through the real Bridge
def test_t1_nonzero_exit_is_uncertain_not_prespawn(tmp_path, mk):
    h, r, res = bridge_cycle(tmp_path, mk("cc", "nonzero"))
    assert res.status == "uncertain" and res.certainty == "STARTED"  # a process existed
    assert responses(h) == [] and offset_row(h)[0] == 0
    assert "started" in kinds(h, delivery_rows(h, r.record_id)[-1][0])
    assert calls(mk.log)  # positive control: the CLI really ran


def test_t1_nonzero_error_text_has_no_prompt(mk):
    a = mk("cc", "nonzero")
    with pytest.raises(RuntimeTargetError) as ei:
        drain(a.deliver("s", rec("very secret prompt text"), []))
    assert not isinstance(ei.value, PrespawnError)
    assert "exited 3" in str(ei.value) and "very secret prompt text" not in str(ei.value)
    r = run_bounded([sys.executable, FAKE], stdin=b"very secret prompt text", cwd=str(mk.ws),
                    env={**os.environ, "FAKE_MODE": "nonzero", "FAKE_KIND": "cc"})
    assert b"very secret prompt text" in r.stderr  # positive control: the fake really echoes the prompt


def test_t1_missing_binary_is_prespawn_and_leaves_state_unchanged(tmp_path, mk):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    res = h.delivery_cycle("b", "s", mk("cx", command=[str(tmp_path / "no" / "such-binary")]))
    assert res.status == "failed_not_started" and res.certainty == "NOT_STARTED"
    assert responses(h) == [] and offset_row(h)[0] == 0 and not calls(mk.log)
    # positive control: the same record delivers once a real command is configured
    ok = h.delivery_cycle("b", "s", mk("cx"))
    assert ok.status == "delivered" and ok.certainty == "TERMINAL" and len(responses(h)) == 1


def test_t1_executable_not_on_path_is_prespawn(monkeypatch, mk):
    monkeypatch.setattr("peerhub.extensions.adapters.base.shutil.which", lambda n: None)
    with pytest.raises(PrespawnError, match="not found"):
        drain(CliRuntimeTarget("cc", mk.ws).deliver("s", rec(), []))


def test_t1_hang_times_out_kills_process_and_is_uncertain(tmp_path, mk):
    a = mk("cc", "hang", timeout_s=1.0)
    t0 = time.monotonic()
    h, r, res = bridge_cycle(tmp_path, a)
    assert time.monotonic() - t0 < 30
    assert res.status == "uncertain" and res.certainty == "STARTED"
    assert responses(h) == [] and offset_row(h)[0] == 0
    assert exited(a)["timed_out"] is True and not pid_alive(exited(a)["pid"])


def test_t1_huge_output_is_bounded_and_killed(mk):
    a = mk("ag", "huge", max_bytes=100_000)
    with pytest.raises(RuntimeTargetError, match="exceeded 100000"):
        drain(a.deliver("s", rec(), []))
    assert exited(a)["output_exceeded"] is True and not pid_alive(exited(a)["pid"])
    env = {**os.environ, "FAKE_KIND": "ag"}
    big = run_bounded([sys.executable, FAKE, "-p", "x"], stdin=None, cwd=str(mk.ws), max_bytes=100_000, env={**env, "FAKE_MODE": "huge"})
    assert len(big.stdout) == 100_000 and big.output_exceeded  # captured bytes never exceed the bound
    ok = run_bounded([sys.executable, FAKE, "-p", "x"], stdin=None, cwd=str(mk.ws), max_bytes=100_000, env={**env, "FAKE_MODE": "ok"})
    assert not ok.output_exceeded and ok.returncode == 0  # positive control


@pytest.mark.parametrize("kind", KINDS)
def test_t1_garbage_output_is_uncertain_never_success(tmp_path, mk, kind):
    h, r, res = bridge_cycle(tmp_path, mk(kind, "garbage"))
    assert res.status == "uncertain" and responses(h) == [] and offset_row(h)[0] == 0
    with pytest.raises(RuntimeTargetError, match="unparseable"):
        drain(mk(kind, "garbage").deliver("s", rec(), []))


def test_t1_vendor_error_result_is_not_a_response(tmp_path, mk):
    h, r, res = bridge_cycle(tmp_path, mk("cc", "vendor_error"))
    assert res.status == "uncertain" and responses(h) == []
    assert drain(mk("cc").deliver("s", rec(), []))[-1][0] == "terminal"  # positive control (same parser, ok result)


def test_t1_consumer_stop_does_not_orphan_process(mk):
    a = mk("cc", "hang", timeout_s=60)
    g = a.deliver("s", rec(), [])
    ev = next(g)
    pid = int(ev[1].split(":")[1])
    assert ev[0] == "started" and pid_alive(pid)  # positive control: it is running before the consumer stops
    g.close()
    assert not pid_alive(pid) and any(e["event"] == "aborted" and e["pid"] == pid for e in a.evidence)


# ---------------------------------------------------------------- secrets / prompts sanitized
@pytest.mark.parametrize("kind", KINDS)
def test_t1_secret_echo_is_redacted_in_response_and_evidence(mk, kind):
    a = mk(kind, "secret_echo", env_extra={"PH_TEST_API_KEY": SECRET, "FAKE_SECRET": SECRET})
    ev = drain(a.deliver("s", rec(), []))
    assert SECRET not in json.dumps(ev) and ev[-1][1]["response"] == "here is [REDACTED]"
    assert SECRET not in json.dumps(list(a.evidence))
    raw = run_bounded([sys.executable, FAKE, "-p", "x"], stdin=b"x", cwd=str(mk.ws),
                      env={**os.environ, "FAKE_MODE": "secret_echo", "FAKE_KIND": kind, "FAKE_SECRET": SECRET})
    assert SECRET.encode() in raw.stdout  # positive control: the fake does echo it


def test_t1_secret_in_failure_text_is_redacted_via_env_and_patterns(mk):
    a = mk("cc", "secret_fail", env_extra={"PH_TEST_API_KEY": SECRET, "FAKE_SECRET": SECRET})
    with pytest.raises(RuntimeTargetError) as ei:
        drain(a.deliver("s", rec(), []))
    assert SECRET not in str(ei.value) and "abcdefghijkl1234" not in str(ei.value)
    # pattern redaction works without registering the value (not in a *KEY*-named variable)
    b = mk("cc", "secret_fail", env_extra={"FAKE_SECRET": "plainvalue99"})
    with pytest.raises(RuntimeTargetError) as ei2:
        drain(b.deliver("s", rec(), []))
    assert "plainvalue99" not in str(ei2.value) and "abcdefghijkl1234" not in str(ei2.value)


def test_t1_sanitizer_redacts_before_truncating_and_keeps_innocent_text():
    s = Sanitizer(secrets=[SECRET])
    out = s.clean("y" * 90 + SECRET + "tail", limit=100)
    assert SECRET[:6] not in out and out.startswith("y" * 90 + "[REDACTED]")  # no secret prefix survives the cut
    assert s.clean("plain OK text") == "plain OK text"
    assert Sanitizer().with_prompt("tell me").clean("please tell me now") == "please [REDACTED] now"


# ---------------------------------------------------------------- discovery / capabilities (LIVE-004/005 offline twins)
def test_t1_discovery_reports_observed_version_with_timestamp_not_hardcoded(mk):
    before = datetime.now(timezone.utc).replace(microsecond=0)
    d1 = mk("cc", env_extra={"FAKE_VERSION": "3.4.5"}).discover()
    d2 = mk("cc", env_extra={"FAKE_VERSION": "7.8.9-beta.1"}).discover()
    assert (d1["version"], d2["version"]) == ("3.4.5", "7.8.9-beta.1") and d1["available"] and d2["available"]
    assert before <= datetime.fromisoformat(d1["observed_at"]) <= datetime.now(timezone.utc)
    src = Path(process.__file__).with_name("base.py").read_text(encoding="utf-8")
    assert "3.4.5" not in src


def test_t1_discovery_unavailable_is_reported_not_synthesized(mk, tmp_path):
    d = mk("cx", command=[str(tmp_path / "missing-cli")]).discover()
    assert d["available"] is False and d["version"] is None and d["reason"]
    assert {v["status"] for v in d["capabilities"].values()} == {"unavailable"}


@pytest.mark.parametrize("help_resume,cli", [("1", "supported"), ("0", "unsupported")])
def test_t1_capabilities_adapter_never_claims_more_than_it_implements(mk, help_resume, cli):
    caps = mk("cx", env_extra={"FAKE_HELP_RESUME": help_resume}).discover()["capabilities"]
    assert caps["resume"]["cli_status"] == cli and caps["resume"]["status"] == "unsupported" and caps["resume"]["fallback"]
    assert all(caps[c]["status"] == "unsupported" for c in ("interrupt", "terminate", "steer"))


def test_t1_resume_is_unsupported_and_spawns_nothing(mk):
    a = mk("cc")
    assert a.resumable is False and a.resume_session("anything") == "unsupported"
    assert calls(mk.log) == []
    assert drain(a.deliver(a.create_session(), rec(), []))[-1][0] == "terminal"  # positive control: the adapter works


def test_t1_bridge_control_effects_report_unsupported_not_done(tmp_path, mk):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    running(h)  # a STARTED, non-terminal delivery exists, so an interrupt would be applicable
    c = ctl(h, "control.pause", "p1")
    res = h.handle_control(c.record_id, mk("cc"), "b")
    assert res.runtime_outcome == "unsupported" and not calls(mk.log)
    # positive control: a runtime that supports interrupt gets it applied
    from tests.m1.fakes import FakeRuntimeTarget
    c2 = ctl(h, "control.pause", "p2")
    assert h.handle_control(c2.record_id, FakeRuntimeTarget(), "b").runtime_outcome == "done"


@pytest.mark.parametrize("kind", ["cc", "cx", "ag"])
def test_t1_unsupported_cancel_never_claims_process_termination(tmp_path, mk, kind):
    h = BridgeHarness(tmp_path / "ws")
    bseed(h)
    running(h)
    c = ctl(h, "control.cancel", "c1")
    res = h.handle_control(c.record_id, mk(kind), "b")
    assert res.runtime_outcome == "unsupported" and not calls(mk.log)  # nothing was spawned or signalled
    ev = [r for r in sql(h, "SELECT kind, detail FROM bridge_evidence WHERE kind='control_applied'") if '"control_kind": "control.cancel"' in r[1]]
    assert len(ev) == 1 and '"runtime_outcome": "unsupported"' in ev[0][1] and "terminated" not in ev[0][1].lower()
    from tests.m1.fakes import FakeRuntimeTarget  # positive control: only a runtime that implements terminate reports done
    c2 = ctl(h, "control.cancel", "c2")
    assert h.handle_control(c2.record_id, FakeRuntimeTarget(), "b").runtime_outcome == "done"


# ---------------------------------------------------------------- pre-spawn argument validation
def test_t1_invalid_model_rejected_before_anything(mk):
    for bad in ("m&calc", "m x", 'm"q', "", "-x;y"):
        with pytest.raises(ValueError):
            CliRuntimeTarget("cc", mk.ws, model=bad)
    CliRuntimeTarget("cc", mk.ws, model="claude-haiku-4-5-20251001")  # positive control
    with pytest.raises(ValueError):
        CliRuntimeTarget("zz", mk.ws)


def test_t1_oversize_prompt_and_missing_workspace_are_prespawn(mk, tmp_path):
    a = mk("ag")
    with pytest.raises(PrespawnError, match="inline limit"):
        drain(a.deliver("s", rec("x" * 1_000_001), []))
    gone = CliRuntimeTarget("ag", tmp_path / "gone", command=[sys.executable, FAKE])
    with pytest.raises(PrespawnError, match="workspace"):
        drain(gone.deliver("s", rec(), []))
    assert calls(mk.log) == []
    assert drain(a.deliver("s", rec("x" * 1000), []))[-1][0] == "terminal"  # positive control


def test_t1_cmd_wrapper_rejects_shell_metacharacters(monkeypatch):
    monkeypatch.setattr(process.sys, "platform", "win32")
    for bad in ("a&b", "a|b", "a>b", "a^b", "a%b", 'a"b', "a\nb"):
        with pytest.raises(ValueError):
            process.check_argv(["C:\\x\\tool.cmd", bad])
    process.check_argv(["C:\\x\\tool.cmd", "plain-arg_1.2"])  # positive control
    process.check_argv(["C:\\x\\tool.exe", "a&b"])  # real executables take args verbatim


def test_t1_workspace_is_not_mutated_and_is_child_cwd(mk):
    before = sorted(p.name for p in mk.ws.iterdir())
    drain(mk("cx").deliver("s", rec(), []))
    assert sorted(p.name for p in mk.ws.iterdir()) == before
    assert os.path.samefile(calls(mk.log)[0]["cwd"], mk.ws)


def test_t1_catch_up_is_in_prompt_and_bounded(mk):
    drain(mk("ag").deliver("s", rec("now"), [SimpleNamespace(record_id="r0", body="earlier fact", author_peer_id="a")]))
    p = calls(mk.log)[0]["prompt"]
    assert "earlier fact" in p and p.endswith("now")
    drain(mk("ag").deliver("s", rec("now"), [SimpleNamespace(record_id="r0", body="z" * 50_000, author_peer_id="a")]))
    assert calls(mk.log)[1]["prompt_len"] < 25_000
