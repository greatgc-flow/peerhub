"""#366 B1: the ag prompt travels over stdin as one stream-json user line (design v4, section 5)."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from peerhub.cli.app import _provider_limit, main
from peerhub.extensions import ask as ask_module
from peerhub.extensions.adapters import CliRuntimeTarget, Sanitizer, ag_stream, base, process
from peerhub.extensions.adapters.ag_stream import encode_user_line, parse_ag_stream_json, terminal_quota, terminal_result
from peerhub.extensions.adapters.base import EXCERPT_CHARS, AgSpec
from peerhub.extensions.adapters.process import BoundedProcess, ProcessResult
from peerhub.extensions.adapters.telemetry import unknown_usage
from peerhub.extensions.ask import ask
from peerhub.extensions.bridge import PrespawnError
from tests.communication.bridge_helpers import bseed, sql
from tests.communication.test_provider_refusal import AG_QUOTA_EXIT_3_OUTPUT as Q_STDERR

PY = sys.executable
FAKE = str(Path(__file__).resolve().parents[1] / "harness" / "fake_cli.py")
FIXTURE = Path(__file__).parents[1] / "fixtures" / "telemetry" / "agy-stream-json-v1.ndjson"
RECORDED = FIXTURE.read_text(encoding="utf-8")
CID = "vendor-session-sentinel-DO-NOT-PERSIST"
QUOTA_LINE = Q_STDERR.splitlines()[0]  # error: Individual quota reached. ... Resets in 20h9m47s.
QUOTA = {"reason": "quota_exhausted", "reset_time": "20h9m47s"}
REAL_DETECT = base._detect_ag_transport  # captured before the communication conftest forces argv
UNICODE = "漢字 😀 שלום é  |  a\r\nb\rc\x00 end"


@pytest.fixture(autouse=True)
def _real_detection(monkeypatch):
    monkeypatch.setattr(base, "_detect_ag_transport", REAL_DETECT)
    base._AG_TRANSPORT_CACHE.clear()
    yield
    base._AG_TRANSPORT_CACHE.clear()


def _lines(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def _events() -> list[dict]:
    return [json.loads(line) for line in RECORDED.splitlines()]


def _nd(events: list[dict]) -> str:
    return "".join(json.dumps(event, separators=(",", ":")) + "\n" for event in events)


def _terminal(status: str, **fields) -> dict:
    return {"event": "result", "result": {"conversation_id": CID, "status": status, **fields}}


def _with_status(status: str) -> str:
    events = _events()
    events[-1]["result"]["status"] = status
    return _nd(events)


# Explicitly synthetic: no recorded stream shows where quota appears in stream mode yet (design §8 Q2).
SYNTHETIC_QUOTA_ERROR = _nd([_events()[0], _terminal("ERROR", error=QUOTA_LINE)])


class Kit:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.ws = root / "agws"
        self.ws.mkdir(exist_ok=True)
        self.msgs, self.launch_log, self.probe_log = root / "msgs.jsonl", root / "launch.jsonl", root / "probes.jsonl"

    def env(self, mode: str = "ok", *, stream: bool = True, **extra: str) -> dict[str, str]:
        return {"FAKE_MODE": mode, "FAKE_KIND": "ag", "FAKE_LOG": str(self.msgs), "FAKE_LAUNCH_LOG": str(self.launch_log),
                "FAKE_HELP_LOG": str(self.probe_log), "FAKE_HELP_STREAM": "1" if stream else "0", **extra}

    def target(self, mode: str = "ok", *, stream: bool = True, env: dict[str, str] | None = None, **kw) -> CliRuntimeTarget:
        kw.setdefault("timeout_s", 20)
        kw.setdefault("writable", True)
        command = kw.pop("command", [PY, FAKE])
        return CliRuntimeTarget("ag", self.ws, command=command, env_extra=self.env(mode, stream=stream, **(env or {})), **kw)

    def replay_env(self, stdout: str | bytes, *, exit_code: int = 0, stderr: str = "") -> dict[str, str]:
        (self.root / "replay.ndjson").write_bytes(stdout.encode("utf-8") if isinstance(stdout, str) else stdout)
        (self.root / "stderr.txt").write_bytes(stderr.encode("utf-8"))
        return {"FAKE_REPLAY": str(self.root / "replay.ndjson"), "FAKE_STDERR_FILE": str(self.root / "stderr.txt"),
                "FAKE_EXIT": str(exit_code)}

    def replay(self, stdout: str | bytes, *, exit_code: int = 0, stderr: str = "", **kw) -> CliRuntimeTarget:
        return self.target("replay", env=self.replay_env(stdout, exit_code=exit_code, stderr=stderr), **kw)

    @property
    def launches(self) -> int:
        return len(_lines(self.launch_log))

    @property
    def messages(self) -> list[dict]:
        return [entry for entry in _lines(self.msgs) if "message" in entry]

    @property
    def probes(self) -> list[str]:
        return [entry["probe"] for entry in _lines(self.probe_log)]


@pytest.fixture
def kit(tmp_path: Path) -> Kit:
    return Kit(tmp_path)


def _drain(target: CliRuntimeTarget, body: str, sid: str = "ext-1") -> list:
    return list(target.deliver(sid, SimpleNamespace(body=body), []))


def _cycle(bridge_h, target):
    bseed(bridge_h)
    return bridge_h.delivery_cycle("b", "s", target)


def _error(result) -> str:
    return result.detail.get("error", "")


def _flat_q() -> str:
    return " ".join(Q_STDERR.split())


# ---- 1-3: framing, Unicode, argv

def test_01_user_line_framing_is_one_ascii_ndjson_message():
    line = encode_user_line('say "hi"\n\tnow')
    assert line == b'{"event":"user","message":{"role":"user","content":"say \\"hi\\"\\n\\tnow"}}\n'
    assert line.isascii() and line.count(b"\n") == 1 and line.endswith(b"\n")
    obj = json.loads(line)
    assert list(obj) == ["event", "message"] and list(obj["message"]) == ["role", "content"]
    assert obj == {"event": "user", "message": {"role": "user", "content": 'say "hi"\n\tnow'}}


def test_02_unicode_round_trips_and_lone_surrogate_is_refused_before_launch(kit):
    assert json.loads(encode_user_line(UNICODE))["message"]["content"] == UNICODE
    events = _drain(kit.target(), UNICODE)
    assert events[-1][0] == "terminal"
    assert [m["message"]["message"]["content"] for m in kit.messages] == [UNICODE]
    assert kit.messages[0]["raw_newlines"] == 1 and kit.messages[0]["raw_ascii"] is True
    assert kit.launches == 1
    for stream in (True, False):
        with pytest.raises(PrespawnError, match="prompt contains a lone surrogate at index 2"):
            _drain(kit.target(stream=stream), "ab\ud800cd")
    assert kit.launches == 1


def test_03_stream_argv_is_exact_with_prompt_never_in_argv(kit):
    target = kit.target(model="gemini-3.8-flash", effort="high")
    assert target.resume_session("ext-1", vendor_session_id="conv-1") == "ok"
    _drain(target, "-looks like a flag")
    (launch,) = _lines(kit.launch_log)
    assert launch["argv"] == ["--input-format", "stream-json", "--output-format", "stream-json", "--model", "gemini-3.8-flash",
                              "--effort", "high", "--mode", "accept-edits", "--conversation", "conv-1", "-p="]
    assert all("looks like" not in arg for arg in launch["argv"])
    # The argv transport is unchanged.
    assert AgSpec().argv("m", "high", "-x", True, "c1") == ["-p", "\n-x", "--output-format", "json", "--model", "m",
                                                             "--effort", "high", "--mode", "accept-edits", "--conversation", "c1"]


# ---- 4-5: recorded stream and fail-closed mutations

def test_04_recorded_stream_gives_response_id_usage_and_one_display(kit):
    shown: list[str] = []
    kind, payload = _drain(kit.replay(RECORDED, on_output=shown.append), "please answer")[-1]
    assert kind == "terminal"
    assert payload["response"] == "answer-fragment-sentinel"
    assert payload["usage"] == {"input_tokens": 10418, "cached_input_tokens": 8113, "output_tokens": 589}
    assert payload["vendor_session"] == {"id": CID, "source": "ag.conversation_id"}
    assert payload["provider_telemetry"]["source"] == "ag.result.usage"
    assert shown == ["answer-fragment-sentinel"]
    assert kit.launches == 1


def _active_step() -> dict:
    return {"event": "step_update", "step_update": {"conversation_id": CID, "step_index": 5, "state": "ACTIVE", "step_type": "tool"}}


# Decision D (#369): an ACTIVE step next to SUCCESS no longer fails closed; it is delivered (test_d_g).
MUTATIONS = {
    "cut_mid_line": (RECORDED[:RECORDED.index('"status"')], "ag stream invalid (malformed_json) (exit 0); stderr: "),
    "no_result": (_nd(_events()[:-1]), "ag stream incomplete (missing_result) (exit 0); stderr: "),
    "event_after_result": (_nd([*_events(), _events()[3]]), "ag stream invalid (event_after_result) (exit 0); stderr: "),
    **{status: (_with_status(status), f"ag stream result {status} (exit 0); stderr: ")
       for status in ("ERROR", "CANCELED", "INTERRUPTED", "INVALID")},
}


@pytest.mark.parametrize("name", list(MUTATIONS))
def test_05_fixture_mutations_fail_closed(kit, bridge_h, name):
    stdout, message = MUTATIONS[name]
    result = _cycle(bridge_h, kit.replay(stdout))
    assert (result.status, _error(result)) == ("uncertain", message)
    assert _provider_limit(_error(result)) is None
    assert kit.launches == 1


# ---- 6-8: transport selection

@pytest.mark.parametrize("env,expected,version", [
    ({}, "stream", "9.8.7"),
    ({"FAKE_HELP_STREAM": "0"}, "argv", "9.8.7"),
    ({"FAKE_HELP_EXIT": "3"}, "undetermined", "9.8.7"),
    ({"FAKE_PROBE_HANG": "--help"}, "undetermined", "9.8.7"),
    ({"FAKE_PROBE_HANG": "--version"}, "undetermined", None),
    ({"FAKE_VERSION": "no version here"}, "undetermined", None),
], ids=["help-flag", "help-no-flag", "help-exit-3", "help-hang", "version-hang", "no-version"])
def test_06_detection_matrix(kit, monkeypatch, env, expected, version):
    # Only the probe that hangs gets a 1 s deadline; the others keep the real one, so a slow start-up never decides.
    real_probe, hang = base._probe_cli, env.get("FAKE_PROBE_HANG")
    monkeypatch.setattr(base, "_probe_cli", lambda b, args, **kw: real_probe(b, args, **{
        **kw, "timeout_s": 1.0 if args[0] == hang else kw["timeout_s"]}))
    meta = kit.target(env=env).execution_metadata()
    assert meta == {"ag_transport": expected, "ag_transport_source": "help", "ag_cli_version": version}


def test_06_missing_binary_is_undetermined_and_never_launches(kit, tmp_path):
    target = kit.target(command=[str(tmp_path / "no-such-agy.exe")])
    assert target.execution_metadata()["ag_transport"] == "undetermined"
    with pytest.raises(PrespawnError, match="cannot determine agy stdin support"):
        _drain(target, "hello there")
    assert kit.launches == 0


def _probe_result(stdout: bytes = b"", returncode: int = 0, **flags: bool) -> ProcessResult:
    fields = {"timed_out": False, **flags}
    return ProcessResult(pid=1, returncode=returncode, stdout=stdout, stderr=b"", output_exceeded=False, duration_s=0.0, **fields)


class Probes:
    def __init__(self, fail: tuple[str, str] | None = None) -> None:
        self.calls: list[str] = []
        self.version = b"agy 1.5.9\n"
        self.help = b"  --input-format string   input format (text, stream-json)\n"
        self.fail = fail

    def __call__(self, base_argv, args, *, env, cwd, timeout_s, max_bytes):
        self.calls.append(args[0])
        out = self.version if args[0] == "--version" else self.help
        if self.fail is not None and self.fail[0] == args[0]:
            return _probe_result(out, **{self.fail[1]: True})
        return _probe_result(out)


def test_06_cache_same_key_skips_help_new_version_or_identity_probes_again(monkeypatch, tmp_path):
    binary = tmp_path / "agy.exe"
    binary.write_bytes(b"one")
    probes = Probes()
    monkeypatch.setattr(base, "_probe_cli", probes)

    def detect():
        return base._detect_ag_transport([str(binary)], {}, str(tmp_path)).transport

    assert detect() == detect() == "stream"
    assert probes.calls == ["--version", "--help", "--version"]
    probes.version = b"agy 1.6.0\n"
    assert detect() == "stream"
    assert probes.calls[3:] == ["--version", "--help"]
    binary.write_bytes(b"a different build")
    probes.help = b"usage: agy -p PROMPT\n"
    assert detect() == "argv"
    assert probes.calls[5:] == ["--version", "--help"]


@pytest.mark.parametrize("probe", ["--version", "--help"])
@pytest.mark.parametrize("flag", ["stdout_truncated", "output_truncated", "timed_out", "drain_timed_out", "silence_timed_out",
                                  "aborted"])  # output_truncated alone = a truncated stderr, which the help is also read from
def test_06b_unusable_probe_is_undetermined_and_never_cached(kit, bridge_h, monkeypatch, probe, flag):
    probes = Probes(fail=(probe, flag))
    monkeypatch.setattr(base, "_probe_cli", probes)
    target = kit.target()
    bseed(bridge_h)
    for _ in range(2):
        result = bridge_h.delivery_cycle("b", "s", target)
        assert result.status == "failed_not_started"
        assert _error(result).startswith("cannot determine agy stdin support: ")
    assert probes.calls.count("--version") == 2
    assert kit.launches == 0


def test_07_detected_argv_cap_fires_before_spawn(kit):
    limit = AgSpec.max_prompt_bytes
    assert limit == (30_000 if sys.platform == "win32" else 120_000)
    with pytest.raises(PrespawnError, match=f"exceeding the ag inline limit of {limit} bytes"):
        _drain(kit.target(stream=False), "x" * (limit + 1))
    assert kit.launches == 0


def test_07a_env_argv_forces_argv_without_probe(kit):
    target = kit.target(env={"PEERHUB_AG_TRANSPORT": "argv"})
    assert target.execution_metadata() == {"ag_transport": "argv", "ag_transport_source": "env", "ag_cli_version": None}
    with pytest.raises(PrespawnError, match="inline limit"):
        _drain(target, "x" * (AgSpec.max_prompt_bytes + 1))
    assert _drain(target, "short prompt")[-1][0] == "terminal"
    (launch,) = _lines(kit.launch_log)
    assert launch["argv"][:4] == ["-p", "short prompt", "--output-format", "json"]
    assert kit.probes == []


@pytest.mark.parametrize("value", ["stream", "ARGV", " x"])
def test_07b_env_invalid_names_the_variable_and_never_launches(kit, value):
    target = kit.target(env={"PEERHUB_AG_TRANSPORT": value})
    assert target.execution_metadata()["ag_transport"] == "invalid_config"
    with pytest.raises(PrespawnError, match="PEERHUB_AG_TRANSPORT must be unset, empty or 'argv'"):
        _drain(target, "hello there")
    assert kit.launches == 0 and kit.probes == []


def test_07c_env_empty_runs_detection(kit):
    meta = kit.target(env={"PEERHUB_AG_TRANSPORT": ""}).execution_metadata()
    assert meta == {"ag_transport": "stream", "ag_transport_source": "help", "ag_cli_version": "9.8.7"}
    assert kit.probes == ["--version", "--help"]


def test_08_undetermined_is_failed_not_started(kit, bridge_h):
    result = _cycle(bridge_h, kit.target(env={"FAKE_HELP_EXIT": "3"}))
    assert (result.status, result.certainty) == ("failed_not_started", "NOT_STARTED")
    assert _error(result) == "cannot determine agy stdin support: --help exit 3"
    assert kit.launches == 0


# ---- 9: happy path

def test_09_happy_path_73kb_unicode_over_stdin(kit, tmp_path, monkeypatch, capsys):
    unit = "Grüße 漢字 😀 שלום   needle-free filler line\n"
    prompt = unit * (73_000 // len(unit.encode("utf-8"))) + "NEEDLE-AT-THE-END"
    assert 72_000 < len(prompt.encode("utf-8")) < 74_000
    query = tmp_path / "query.txt"
    query.write_bytes(prompt.encode("utf-8"))
    db = tmp_path / "store" / "peerhub.db"
    monkeypatch.setattr(ask_module, "CliRuntimeTarget",
                        lambda kind, workspace, **kw: CliRuntimeTarget(kind, workspace, command=[PY, FAKE], env_extra=kit.env(), **kw))
    argv = ["--db", str(db), "ask", "ag", "--query-file", str(query), "--writable", "--json", "-w", str(kit.ws), "--request-id", "r73"]
    assert main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["status"], out["response"]) == ("delivered", "OK")
    assert (out["ag_transport"], out["ag_transport_source"], out["ag_cli_version"]) == ("stream", "help", "9.8.7")
    (message,) = kit.messages
    assert message["message"]["message"]["content"] == prompt
    assert message["raw_newlines"] == 1 and message["raw_ascii"] is True
    assert _lines(kit.msgs)[-1] == {"eof_seen": True}
    assert kit.launches == 1
    (detail,) = [json.loads(row[0]) for row in sql(SimpleNamespace(db_path=db),
                 "SELECT detail FROM bridge_evidence WHERE kind='execution_metadata'")]
    assert detail["ag_transport"] == "stream"
    # The same request id replays the stored result through the allowlist and launches nothing.
    assert main(argv) == 0
    replay = json.loads(capsys.readouterr().out)
    assert (replay["status"], replay["ag_transport"]) == ("recovered_terminal", "stream")
    assert kit.launches == 1


def test_09b_stream_cap_is_exactly_1_000_000_bytes(kit):
    with pytest.raises(PrespawnError, match="exceeding the ag inline limit of 1000000 bytes"):
        _drain(kit.target(), "x" * 1_000_001)
    assert kit.launches == 0
    assert _drain(kit.target(), "x" * 1_000_000)[-1][0] == "terminal"
    (message,) = kit.messages
    assert message["message"]["message"]["content"] == "x" * 1_000_000
    assert kit.launches == 1


# ---- 10-16: stdin writer, deadlines

class _Stdin:
    def __init__(self, close_error: BaseException | None = None) -> None:
        self.close_error = close_error
        self.closed = False

    def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


def _feed(monkeypatch, payload: bytes | None, returns, *, close_error=None, stdin=True):
    seen: list[int] = []
    it = iter(returns)

    def write_some(stream, view):
        seen.append(len(view))
        r = next(it)
        if isinstance(r, BaseException):
            raise r
        return r(len(view)) if callable(r) else r

    monkeypatch.setattr(process, "_write_some", write_some)
    proc = BoundedProcess(["x"], stdin=payload, cwd=".", env={})
    pipe = _Stdin(close_error) if stdin else None
    proc._feed(SimpleNamespace(stdin=pipe))
    assert proc._stdin_done.is_set()
    return proc._stdin_state, proc._stdin_written, seen, pipe


def _forever(fn):
    while True:
        yield fn


def test_10_writer_states(monkeypatch):
    payload = b"y" * 200_000
    state, written, seen, pipe = _feed(monkeypatch, payload, _forever(lambda n: n))
    assert (state, written, pipe.closed) == ("complete", 200_000, True)
    assert seen == [65_536, 65_536, 65_536, 200_000 - 3 * 65_536]
    state, written, seen, _ = _feed(monkeypatch, payload, _forever(lambda n: min(n, 1000)))
    assert (state, written, len(seen)) == ("complete", 200_000, 200)
    for stop in (0, None):
        state, written, _, pipe = _feed(monkeypatch, payload, [lambda n: 7, stop])
        assert (state, written, pipe.closed) == ("short", 7, True)
    state, written, _, pipe = _feed(monkeypatch, payload, [lambda n: n, BrokenPipeError()])
    assert (state, written, pipe.closed) == ("broken", 65_536, True)
    state, written, _, _ = _feed(monkeypatch, payload, [lambda n: n, ValueError("boom")])
    assert (state, written) == ("error", 65_536)
    for close_error in (OSError("close failed"), RuntimeError("close failed")):
        state, written, _, _ = _feed(monkeypatch, b"tiny", _forever(lambda n: n), close_error=close_error)
        assert written == 4 and state != "complete"
    assert _feed(monkeypatch, None, [])[0] == "none"
    assert _feed(monkeypatch, b"payload", [], stdin=False)[0] == "none"


def _short_writer(failure):
    state = {"next": "half"}

    def write_some(stream, view):
        if state["next"] == "half":
            state["next"] = "fail"
            return len(view) // 2
        state["next"] = "half"
        if isinstance(failure, BaseException):
            raise failure
        return failure

    return write_some


@pytest.mark.parametrize("failure,state", [(0, "short"), (None, "short"), (BrokenPipeError(), "broken")])
def test_11_short_parent_write_next_to_a_valid_success_stream(kit, bridge_h, monkeypatch, failure, state):
    monkeypatch.setattr(process, "_write_some", _short_writer(failure))
    n = len(encode_user_line("m0"))
    result = _cycle(bridge_h, kit.replay(RECORDED))
    assert result.status == "uncertain"
    assert _error(result) == f"ag stdin write incomplete ({state}, {n // 2}/{n}) (exit 0); stderr: "
    assert _provider_limit(_error(result)) is None
    assert kit.launches == 1


@pytest.mark.parametrize("stdout,reason", [("", "no_output"), (_nd(_events()[:1]), "missing_result")])
def test_12_eof_without_result_exit_0_is_uncertain(kit, bridge_h, stdout, reason):
    result = _cycle(bridge_h, kit.replay(stdout))
    assert (result.status, _error(result)) == ("uncertain", f"ag stream incomplete ({reason}) (exit 0); stderr: ")


def test_13_interrupted_is_uncertain_without_provider_limit(kit, bridge_h):
    result = _cycle(bridge_h, kit.replay(_with_status("INTERRUPTED")))
    assert (result.status, _error(result)) == ("uncertain", "ag stream result INTERRUPTED (exit 0); stderr: ")
    assert _provider_limit(_error(result)) is None


def test_14_terminate_while_the_writer_is_blocked_is_broken_and_confirmed(kit, monkeypatch):
    real_write = process._write_some
    entered, ready = threading.Event(), threading.Event()

    def write_some(stream, view):
        entered.set()
        return real_write(stream, view)

    monkeypatch.setattr(process, "_write_some", write_some)
    payload = encode_user_line("x" * 900_000)
    proc = BoundedProcess([PY, FAKE, "--input-format", "stream-json", "--output-format", "stream-json", "-p="], stdin=payload,
                          cwd=str(kit.ws), env={**os.environ, **kit.env("noread")}, timeout_s=60,
                          on_stdout=lambda chunk: ready.set()).start()
    assert entered.wait(30)
    assert ready.wait(30)  # the fake has logged its launch and will never read stdin
    proc.terminate()
    res = proc.wait()
    assert res.aborted is True and res.termination_confirmed is True
    assert res.stdin_state == "broken" and res.stdin_bytes_written < len(payload)
    assert kit.launches == 1 and kit.messages == []


# The deadlines also bound the fake's start-up (launch log, then stdin read), so they keep a margin for a loaded host.
@pytest.mark.parametrize("kw", [{"timeout_s": 3.0}, {"timeout_s": 30, "silence_timeout_s": 2.0}], ids=["deadline", "silence"])
def test_15_deadline_and_silence_timeout_are_uncertain_and_bounded(kit, bridge_h, kw):
    t0 = time.monotonic()
    result = _cycle(bridge_h, kit.target("hang", **kw))
    assert result.status == "uncertain"
    assert time.monotonic() - t0 < min(kw["timeout_s"], kw.get("silence_timeout_s", 99)) + 10 + 5 + 2
    assert kit.launches == 1 and len(kit.messages) == 1


# ---- 17-19: quota cases, truncation, S3 negatives

@pytest.mark.parametrize("stdout", ["", _nd(_events()[:1])], ids=["no_output", "init_only"])
def test_17a_absent_terminal_with_nonzero_exit_classifies_stderr(kit, bridge_h, stdout):
    result = _cycle(bridge_h, kit.replay(stdout, exit_code=3, stderr=Q_STDERR))
    assert result.status == "uncertain"
    assert _error(result).startswith("ag exited 3: error: Individual quota reached.")
    assert _provider_limit(_error(result)) == QUOTA


@pytest.mark.parametrize("exit_code,stderr", [(0, ""), (3, "Resets in 1h\n")])
def test_17b_quota_in_a_terminal_error_is_classified_from_the_terminal_only(kit, bridge_h, exit_code, stderr):
    result = _cycle(bridge_h, kit.replay(SYNTHETIC_QUOTA_ERROR, exit_code=exit_code, stderr=stderr))
    assert result.status == "uncertain"
    assert _error(result) == "\n".join(["Individual quota reached (Resets in 20h9m47s)", QUOTA_LINE,
                                        f"ag stream result ERROR (exit {exit_code})"])
    assert _provider_limit(_error(result)) == QUOTA


def test_17c_success_with_nonzero_exit_never_claims_quota(kit, bridge_h):
    result = _cycle(bridge_h, kit.replay(RECORDED, exit_code=1, stderr=Q_STDERR))
    assert (result.status, _error(result)) == ("uncertain", f"ag stream SUCCESS but exited 1; stderr: {_flat_q()}")
    assert _provider_limit(_error(result)) is None


def _over_one_mib() -> str:
    init = _events()[0]
    steps = [{"event": "step_update", "step_update": {"conversation_id": CID, "step_index": i, "state": "DONE",
                                                      "step_type": "agent_response", "text_delta": "z" * 230_000}} for i in range(5)]
    return _nd([init, *steps, _terminal("SUCCESS", response="ok")])


@pytest.mark.parametrize("stdout,max_bytes", [(RECORDED, 512), (_over_one_mib(), 1_048_576)], ids=["recorded", "over_1mib"])
def test_18a_truncated_capture_is_uncertain(kit, bridge_h, stdout, max_bytes):
    result = _cycle(bridge_h, kit.replay(stdout, max_bytes=max_bytes))
    assert (result.status, _error(result)) == ("uncertain", "ag stream incomplete (stdout_truncated) (exit 0); stderr: ")


def test_18b_truncated_stdout_with_quota_stderr_never_claims_quota(kit, bridge_h):
    result = _cycle(bridge_h, kit.replay(SYNTHETIC_QUOTA_ERROR, exit_code=3, stderr=Q_STDERR, max_bytes=128))
    assert result.status == "uncertain"
    assert _error(result).startswith("ag stream incomplete (stdout_truncated) (exit 3); stderr: ")
    assert _provider_limit(_error(result)) is None


def _fixed(obj: dict, width: int) -> str:
    obj = {**obj, "pad": ""}
    obj["pad"] = "p" * (width - 1 - len(json.dumps(obj, separators=(",", ":"))))
    line = json.dumps(obj, separators=(",", ":")) + "\n"
    assert len(line) == width
    return line


SPLICE_WIDTH, SPLICE_KEEP = 400, 100  # max_bytes = 2 * keep * width = 80_000 splices the capture on line boundaries


def _spliced_lines() -> list[str]:
    lines = [_fixed(_events()[0], SPLICE_WIDTH)]
    lines += [_fixed({"event": "step_update", "step_update": {"conversation_id": CID, "step_index": i, "state": "DONE",
                                                              "step_type": "tool"}}, SPLICE_WIDTH) for i in range(600)]
    lines.append(_fixed(_terminal("ERROR", error=QUOTA_LINE), SPLICE_WIDTH))
    return lines


def test_18c_a_spliced_capture_that_validates_is_still_truncated(kit, bridge_h):
    width, keep = SPLICE_WIDTH, SPLICE_KEEP
    lines = _spliced_lines()
    spliced = "".join(lines[:keep] + lines[-keep:])
    assert parse_ag_stream_json(spliced)["complete"] is True
    assert terminal_quota(lines[-1]) is not None  # without row 4 this splice would claim quota
    result = _cycle(bridge_h, kit.replay("".join(lines), exit_code=3, stderr=Q_STDERR, max_bytes=2 * keep * width))
    assert result.status == "uncertain"
    assert _error(result).startswith("ag stream incomplete (stdout_truncated) (exit 3); stderr: ")
    assert _provider_limit(_error(result)) is None


def _success_quoting_quota() -> str:
    events = _events()
    events[-1]["result"]["response"] = QUOTA_LINE
    return _nd(events)


def _quota_in_step_only() -> str:
    events = _events()
    events[1]["step_update"]["text_delta"] = QUOTA_LINE
    events[2]["step_update"]["text_delta"] = QUOTA_LINE
    events[-1]["result"]["status"] = "ERROR"
    return _nd(events)


NEGATIVES = {
    "a_success_quotes_quota": (_success_quoting_quota(), 0, Q_STDERR, None),
    "b_error_without_quota": (_with_status("ERROR"), 0, "", "ag stream result ERROR (exit 0); stderr: "),
    "c_quota_only_in_step": (_quota_in_step_only(), 3, Q_STDERR, f"ag stream result ERROR (exit 3); stderr: {_flat_q()}"),
    "d_error_without_quota_with_q": (_with_status("ERROR"), 3, Q_STDERR, f"ag stream result ERROR (exit 3); stderr: {_flat_q()}"),
    "e_event_after_result": (_nd([*_events(), _events()[3]]), 3, Q_STDERR,
                             f"ag stream invalid (event_after_result) (exit 3); stderr: {_flat_q()}"),
    "g_cut_mid_json": (_nd(_events()[:1]) + '{"event":"step_update","step_up\n', 3, Q_STDERR,
                       f"ag stream invalid (malformed_json) (exit 3); stderr: {_flat_q()}"),
    "h_invalid_usage": (_nd([_events()[0], _terminal("ERROR", error=QUOTA_LINE, usage={"input_tokens": -1})]), 3, Q_STDERR,
                        f"ag stream invalid (invalid_usage) (exit 3); stderr: {_flat_q()}"),
}


@pytest.mark.parametrize("name", list(NEGATIVES))
def test_19_s3_negatives_never_set_provider_limit(kit, bridge_h, name):
    stdout, exit_code, stderr, message = NEGATIVES[name]
    result = _cycle(bridge_h, kit.replay(stdout, exit_code=exit_code, stderr=stderr))
    if message is None:
        assert result.status == "delivered"
    else:
        assert (result.status, _error(result)) == ("uncertain", message)
        assert _provider_limit(_error(result)) is None
    assert kit.launches == 1


STEP_UNFINISHED_QUOTA_ERROR = _nd([_events()[0], _active_step(), _terminal("ERROR", error=QUOTA_LINE)])


def test_19f_an_unfinished_step_never_hides_a_quota_terminal(kit, bridge_h):
    # Decision D2 (#369) replaces v4's "invalid (step_unfinished)": steps never change the decision, so case (i) applies.
    result = _cycle(bridge_h, kit.replay(STEP_UNFINISHED_QUOTA_ERROR, exit_code=3, stderr=Q_STDERR))
    assert result.status == "uncertain"
    assert _error(result) == "\n".join(["Individual quota reached (Resets in 20h9m47s)", QUOTA_LINE,
                                        "ag stream result ERROR (exit 3)"])
    assert _provider_limit(_error(result)) == QUOTA
    assert kit.launches == 1


# ---- 20: no relaunch

def _replayed(stdout, exit_code=0, stderr="", **kw):
    return lambda kit, monkeypatch: kit.replay(stdout, exit_code=exit_code, stderr=stderr, **kw)


def _short_write_scenario(kit, monkeypatch):
    monkeypatch.setattr(process, "_write_some", _short_writer(0))
    return kit.replay(RECORDED)


def _missing_response() -> str:
    events = _events()
    del events[-1]["result"]["response"]
    return _nd(events)


SCENARIOS = {
    "11_short_write": _short_write_scenario,
    "12_missing_result": _replayed(_nd(_events()[:1])),
    "13_interrupted": _replayed(_with_status("INTERRUPTED")),
    "15_deadline": lambda kit, monkeypatch: kit.target("hang", timeout_s=3.0),
    "17a_absent_quota": _replayed("", 3, Q_STDERR),
    "17b_terminal_quota": _replayed(SYNTHETIC_QUOTA_ERROR, 3, Q_STDERR),
    "17c_success_exit_1": _replayed(RECORDED, 1, Q_STDERR),
    "18a_recorded": _replayed(RECORDED, max_bytes=512),
    "18a_over_1mib": _replayed(_over_one_mib(), max_bytes=1_048_576),
    "18b_truncated": _replayed(SYNTHETIC_QUOTA_ERROR, 3, Q_STDERR, max_bytes=128),
    "18c_spliced": _replayed("".join(_spliced_lines()), 3, Q_STDERR, max_bytes=80_000),
    **{f"19{name[0]}": _replayed(stdout, exit_code, stderr) for name, (stdout, exit_code, stderr, _m) in NEGATIVES.items()},
    "19f": _replayed(STEP_UNFINISHED_QUOTA_ERROR, 3, Q_STDERR),
    "22_missing_response": _replayed(_missing_response()),
}


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_20_no_relaunch_after_one_launch(kit, bridge_h, monkeypatch, tmp_path, name):
    target = SCENARIOS[name](kit, monkeypatch)
    first = _cycle(bridge_h, target)
    assert first.status in ("uncertain", "delivered")
    assert kit.launches == 1
    assert bridge_h.delivery_cycle("b", "s", target).status in ("blocked_uncertain", "idle")
    assert kit.launches == 1
    db = tmp_path / "askdb" / "peerhub.db"
    once = ask(db, "ag", "relaunch probe", stream_id="r", request_id="r1", runtime=target)
    assert once["status"] in ("uncertain", "delivered")
    assert kit.launches == 2
    ask(db, "ag", "relaunch probe", stream_id="r", request_id="r1", runtime=target)
    assert kit.launches == 2


# ---- 22: SUCCESS response shape

@pytest.mark.parametrize("value", ["missing", None, 42, ["a"]], ids=["missing", "null", "int", "list"])
def test_22_success_without_string_response_is_uncertain(kit, bridge_h, value):
    # Decision D1 (#369, test h): a non-string SUCCESS response is an invalid terminal field (row 5), not v4's row 11.
    events = _events()
    if value == "missing":
        del events[-1]["result"]["response"]
    else:
        events[-1]["result"]["response"] = value
    result = _cycle(bridge_h, kit.replay(_nd(events)))
    assert (result.status, _error(result)) == ("uncertain", "ag stream invalid (invalid_response) (exit 0); stderr: ")
    assert terminal_result(_nd(events).encode())["observation"] == "invalid"


def test_22_empty_string_response_is_passed_on(kit):
    events = _events()
    events[-1]["result"]["response"] = ""
    kind, payload = _drain(kit.replay(_nd(events)), "please answer")[-1]
    assert kind == "terminal"
    assert payload["response"] == "" and payload["empty_response"] is True


# ---- 23: classifier boundaries

def _result_line(detail) -> str:
    return json.dumps({"event": "result", "result": {"conversation_id": CID, "status": "ERROR", "detail": detail}})


def _nested(depth: int):
    node: object = QUOTA_LINE
    for _ in range(depth):
        node = {"inner": node}
    return node


@pytest.mark.parametrize("detail", [
    [f"filler {i}" for i in range(199)] + [QUOTA_LINE],
    _nested(40),
    {"pad": "x" * 10_000, "message": QUOTA_LINE},
    "first line of the failure\n" + QUOTA_LINE,
    ["context", [QUOTA_LINE]],
], ids=["200th-string", "depth-40", "after-10k", "second-line", "in-list"])
def test_23a_e_quota_anywhere_in_the_terminal_is_found(detail):
    match = terminal_quota(_result_line(detail))
    assert match is not None
    assert (match.reason, match.reset_time, match.reset_phrase, match.line) == (
        "quota_exhausted", "20h9m47s", "Resets in 20h9m47s", QUOTA_LINE)


def _quota_error(line: str) -> str:
    match = terminal_quota(_result_line(line))
    assert match is not None
    return base._ag_quota_error(match, "ERROR", 0, Sanitizer())


def test_23f_long_line_keeps_the_reset_in_the_summary_within_the_bound():
    error = _quota_error("Individual quota reached. " + "x" * 400 + " Resets in 20h9m47s.")
    line1, line2, line3 = error.split("\n")
    assert line1 == "Individual quota reached (Resets in 20h9m47s)"
    assert line2.endswith("...[truncated]") and "20h9m47s" not in line2
    assert len(line1) + 1 + len(line2) <= EXCERPT_CHARS
    assert line3 == "ag stream result ERROR (exit 0)"
    assert _provider_limit(error) == QUOTA


def test_23g_usage_limit_long_line():
    error = _quota_error("You've hit your usage limit." + "x" * 400 + " Try again at 5:00 PM.")
    line1, line2, _line3 = error.split("\n")
    assert line1 == "usage limit reached (Try again at 5:00 PM)"
    assert len(line1) + 1 + len(line2) <= EXCERPT_CHARS
    assert _provider_limit(error) == {"reason": "usage_limit", "reset_time": "5:00 PM"}


def test_23h_a_cut_that_creates_a_higher_priority_reset_drops_line_2():
    iso = "2026-11-15T08:00:00Z"
    line1 = f"Individual quota reached (resets at {iso} )"
    limit = EXCERPT_CHARS - len(line1) - 1 - 14
    head = "Individual quota reached. "
    line = head + "x" * (limit - len(head) - len(" resets 5:30")) + " resets 5:300 later; " + "y" * 100 + f" resets at {iso}."
    assert line.index(iso) > EXCERPT_CHARS
    assert Sanitizer().clean(line, limit).endswith(" resets 5:30...[truncated]")
    assert base.extract_reset_time(line) == iso
    error = _quota_error(line)
    assert error.split("\n") == [line1, "ag stream result ERROR (exit 0)"]
    assert _provider_limit(error) == {"reason": "quota_exhausted", "reset_time": iso}


def test_23i_a_reset_phrase_over_100_characters_is_dropped():
    error = _quota_error("Individual quota reached. " + "x" * 400 + " resets at 2026-11-15T" + "0" * 100)
    assert error.split("\n")[0] == "Individual quota reached"
    assert _provider_limit(error) == {"reason": "quota_exhausted", "reset_time": None}


def test_23_delivery_with_exit_0_uses_the_bounded_summary(kit, bridge_h):
    line = "Individual quota reached. " + "x" * 400 + " Resets in 20h9m47s."
    result = _cycle(bridge_h, kit.replay(_nd([_events()[0], _terminal("ERROR", error=line)])))
    assert result.status == "uncertain"
    assert _error(result).split("\n")[0] == "Individual quota reached (Resets in 20h9m47s)"
    assert _provider_limit(_error(result)) == QUOTA


@pytest.mark.parametrize("kind", ["cc", "cx"])
def test_cc_cx_stdin_bytes_argv_and_metadata_are_unchanged(kit, monkeypatch, kind):
    real_write = process._write_some
    written = bytearray()

    def write_some(stream, view):
        written.extend(view)
        return real_write(stream, view)

    monkeypatch.setattr(process, "_write_some", write_some)
    prompt = "-dash first\r\n" + UNICODE.replace("\x00", "") + "x" * 70_000
    env = {**kit.env(), "FAKE_KIND": kind}
    target = CliRuntimeTarget(kind, kit.ws, command=[PY, FAKE], env_extra=env, timeout_s=20)
    assert target.execution_metadata() == {}
    assert _drain(target, prompt)[-1][1]["response"] == "OK"
    assert bytes(written) == prompt.encode("utf-8")
    (launch,) = _lines(kit.launch_log)
    assert launch["argv"] == base.SPECS[kind].argv(None, None, prompt, False, None)
    assert kit.probes == []


def test_terminal_result_observations():
    assert terminal_result(b"")["observation"] == "absent"
    assert terminal_result(b" \n")["reason"] == "no_output"
    assert terminal_result(_nd(_events()[:1]).encode())["observation"] == "absent"
    assert terminal_result(b"\xff\xfe")["reason"] == "invalid_encoding"
    observed = terminal_result(RECORDED.encode())
    assert observed["observation"] == "complete" and observed["status"] == "SUCCESS"
    assert observed["response"] == "answer-fragment-sentinel" and observed["conversation_id"] == CID
    assert observed["terminal_line"] == RECORDED.splitlines()[-1]
    assert observed["validation"] is None
    assert ag_stream.terminal_result(_with_status("ERROR").encode())["response"] is None


# ---- decision D (#369): delivery from the framing and the terminal only; step events are telemetry

RESUMED = FIXTURE.with_name("agy-stream-json-resume-v1.ndjson").read_text(encoding="utf-8")


def _step(step_type, index: int, state: str = "DONE", cid: str = CID) -> dict:
    return {"event": "step_update", "step_update": {"conversation_id": cid, "step_index": index, "state": state,
                                                    "step_type": step_type}}


def _with_steps(*steps: dict, status: str = "SUCCESS") -> str:
    events = _events()
    events[-1]["result"]["status"] = status
    return _nd([*events[:-1], *steps, events[-1]])


def _evidence(bridge_h, kind: str) -> list[dict]:
    return [json.loads(row[0]) for row in sql(bridge_h, "SELECT detail FROM bridge_evidence WHERE kind=?", (kind,))]


def test_d_a_recorded_resume_is_delivered_with_the_session_cumulative_result_usage(kit, bridge_h):
    assert parse_ag_stream_json(RESUMED)["complete"] is True  # system_message is a known step type
    result_usage = json.loads(RESUMED.splitlines()[-1])["result"]["usage"]
    kind, payload = _drain(kit.replay(RESUMED), "resume please")[-1]
    assert kind == "terminal" and payload["response"].strip() == "answer-fragment-sentinel"
    assert payload["usage"] == {"input_tokens": 11311, "cached_input_tokens": 7001, "output_tokens": 24}
    telemetry = payload["provider_telemetry"]
    assert (telemetry["scope"], telemetry["source"], telemetry["reported"]) == ("session_cumulative", "ag.result.usage", result_usage)
    assert telemetry["coverage"]["state"] == "MEASURED"
    assert "ag_stream_validation" not in payload
    result = _cycle(bridge_h, kit.replay(RESUMED))
    assert result.status == "delivered"
    (terminal,) = _evidence(bridge_h, "terminal")
    assert (terminal["usage"], terminal["usage_scope"]) == (payload["usage"], "session_cumulative")
    assert "ag_stream_validation" not in terminal
    (telemetry,) = _evidence(bridge_h, "session_telemetry")
    assert (telemetry["usage"]["scope"], telemetry["usage"]["reported"]) == ("session_cumulative", result_usage)


def test_d_b_unknown_step_type_with_success_is_delivered_and_named_in_the_evidence(kit, bridge_h, tmp_path):
    stream = _with_steps(_step("future.step", 5))
    expected = {"reason": "unknown_step_type", "unknown_step_types": ["future_step"], "unknown_step_type_count": 1}
    kind, payload = _drain(kit.replay(stream), "please answer")[-1]
    assert kind == "terminal" and payload["response"] == "answer-fragment-sentinel"
    assert payload["ag_stream_validation"] == expected
    assert "usage" not in payload
    assert payload["provider_telemetry"] == unknown_usage("unknown_step_type")
    assert payload["vendor_session"] == {"id": CID, "source": "ag.conversation_id"}
    result = _cycle(bridge_h, kit.replay(stream))
    assert result.status == "delivered"
    (terminal,) = _evidence(bridge_h, "terminal")
    assert terminal["ag_stream_validation"] == expected and terminal.get("usage") is None
    (telemetry,) = _evidence(bridge_h, "session_telemetry")
    assert telemetry["usage"]["coverage"] == {"state": "UNKNOWN", "reason": "unknown_step_type"}
    assert bridge_h.bridge.delivery_details(result.delivery_id)["ag_stream_validation"] == expected
    db = tmp_path / "askdb" / "peerhub.db"
    first = ask(db, "ag", "unknown step", stream_id="r", request_id="r1", runtime=kit.replay(stream))
    assert (first["status"], first["ag_stream_validation"]) == ("delivered", expected)
    again = ask(db, "ag", "unknown step", stream_id="r", request_id="r1", runtime=kit.replay(stream))
    assert (again["status"], again["ag_stream_validation"]) == ("recovered_terminal", expected)
    assert kit.launches == 3


def test_d_c_unknown_type_names_are_bounded_deduplicated_and_sanitized():
    # a-b / a.b and x*60 / x*41 are distinct types that display alike: shown once, counted twice.
    names = ["alpha", "alpha", "a-b", "a.b", "Beta-Type", "x" * 60, "x" * 41, "émoji😀", "five", "six", "seven"]
    observed = terminal_result(_with_steps(*[_step(name, 5 + i) for i, name in enumerate(names)]).encode())
    assert (observed["observation"], observed["status"]) == ("complete", "SUCCESS")
    assert observed["validation"] == {"reason": "unknown_step_type",
                                      "unknown_step_types": ["alpha", "a_b", "_eta__ype", "x" * 40, "_moji_"],
                                      "unknown_step_type_count": 10}


def test_d_d_unknown_step_type_with_a_non_success_result_is_uncertain(kit, bridge_h):
    result = _cycle(bridge_h, kit.replay(_with_steps(_step("future_step", 5), status="ERROR")))
    assert (result.status, _error(result)) == ("uncertain", "ag stream result ERROR (exit 0); stderr: ")
    assert _provider_limit(_error(result)) is None
    assert kit.launches == 1


def _cid_mismatch_in_result() -> str:
    events = _events()
    events[-1]["result"]["conversation_id"] = "another-conversation"
    return _nd(events)


FRAMING = {
    "second_init_cid_mismatch": (_nd([_events()[0], {**_events()[0], "conversation_id": "another-conversation"},
                                      *_events()[1:]]), "session_mismatch"),
    "event_after_result": (_nd([*_events(), _step("tool", 9)]), "event_after_result"),
    "second_result": (_nd([*_events(), _events()[-1]]), "event_after_result"),
    "step_cid_mismatch": (_with_steps(_step("future_step", 5, cid="another-conversation")), "session_mismatch"),
    "result_cid_mismatch": (_cid_mismatch_in_result(), "session_mismatch"),
}


@pytest.mark.parametrize("name", list(FRAMING))
def test_d_e_a_framing_violation_is_invalid(kit, bridge_h, name):
    stdout, reason = FRAMING[name]
    result = _cycle(bridge_h, kit.replay(stdout))
    assert (result.status, _error(result)) == ("uncertain", f"ag stream invalid ({reason}) (exit 0); stderr: ")
    assert terminal_result(stdout.encode())["observation"] == "invalid"
    assert kit.launches == 1


def test_d_f_no_result_is_absent_even_after_an_unknown_step(kit, bridge_h):
    stdout = _nd([_events()[0], _step("future_step", 5)])
    assert terminal_result(stdout.encode())["observation"] == "absent"
    result = _cycle(bridge_h, kit.replay(stdout, exit_code=3, stderr=Q_STDERR))
    assert result.status == "uncertain"
    assert _error(result).startswith("ag exited 3: error: Individual quota reached.")
    assert _provider_limit(_error(result)) == QUOTA


def test_d_g_an_unfinished_known_step_with_success_is_delivered(kit, bridge_h):
    stream = _with_steps(_active_step())
    kind, payload = _drain(kit.replay(stream), "please answer")[-1]
    assert kind == "terminal" and payload["response"] == "answer-fragment-sentinel"
    assert payload["ag_stream_validation"] == {"reason": "step_unfinished", "unknown_step_types": [],
                                               "unknown_step_type_count": 0}
    assert payload["provider_telemetry"] == unknown_usage("step_unfinished") and "usage" not in payload
    assert _cycle(bridge_h, kit.replay(stream)).status == "delivered"


@pytest.mark.parametrize("extra", [{"event": "future_event", "payload": {"secret": "sentinel"}}, _events()[0]],
                         ids=["unknown_event_type", "second_init_same_id"])
def test_d_i_unknown_top_level_event_is_delivered_with_unknown_usage(kit, bridge_h, extra):
    # Decision D1 judges only the framing and the terminal; the validator's `unknown_event` is telemetry only (D3).
    stream = _with_steps(extra)
    expected = {"reason": "unknown_event", "unknown_step_types": [], "unknown_step_type_count": 0}
    kind, payload = _drain(kit.replay(stream), "please answer")[-1]
    assert kind == "terminal" and payload["response"] == "answer-fragment-sentinel"
    assert payload["ag_stream_validation"] == expected
    assert payload["provider_telemetry"] == unknown_usage("unknown_event") and "usage" not in payload
    result = _cycle(bridge_h, kit.replay(stream))
    assert result.status == "delivered"
    (terminal,) = _evidence(bridge_h, "terminal")
    assert terminal["ag_stream_validation"] == expected and terminal.get("usage") is None
    (telemetry,) = _evidence(bridge_h, "session_telemetry")
    assert telemetry["usage"]["coverage"] == {"state": "UNKNOWN", "reason": "unknown_event"}
    assert kit.launches == 2


# ---- #371: auto-denied tool actions on both transports (agy 1.5.9 capture)

DENIED = FIXTURE.with_name("agy-stream-json-denied-v1.ndjson").read_text(encoding="utf-8")
TRANSPORTS = pytest.mark.parametrize("stream", [False, True], ids=["argv", "stream"])


def _denied_events() -> list[dict]:
    return [json.loads(line) for line in DENIED.splitlines()]


def _stdout(events: list[dict], stream: bool) -> str:
    """The stream as captured, or the argv transport's one flat object with the same fields at the top level."""
    result = events[-1]["result"]
    return _nd(events) if stream else json.dumps({key: result[key] for key in ("response", "conversation_id", "denied_actions")
                                                  if key in result}) + "\n"


def _ask_ag(kit, tmp_path, monkeypatch, capsys, stdout: str, stream: bool) -> tuple[int, dict]:
    env = kit.replay_env(stdout)
    monkeypatch.setattr(ask_module, "CliRuntimeTarget", lambda kind, workspace, **kw: CliRuntimeTarget(
        kind, workspace, command=[PY, FAKE], env_extra=kit.env("replay", stream=stream, **env), **kw))
    code = main(["--db", str(tmp_path / "store" / "peerhub.db"), "ask", "ag", "run the probe", "--writable", "--json",
                 "-w", str(kit.ws)])
    return code, json.loads(capsys.readouterr().out)


@TRANSPORTS
def test_e_a_denied_empty_turn_is_denied_empty_with_the_names_and_exit_1(kit, tmp_path, monkeypatch, capsys, stream):
    code, out = _ask_ag(kit, tmp_path, monkeypatch, capsys, _stdout(_denied_events(), stream), stream)
    assert (out["status"], out["denied_actions"], code) == ("denied_empty", ["command"], 1)
    assert out["ag_transport"] == ("stream" if stream else "argv")
    assert out["error"]["code"] == "denied_empty" and out["guidance"]["code"] == "ask_denied_empty"
    assert kit.launches == 1


def test_e_b_a_failed_tool_step_validates_and_keeps_measured_usage(kit):
    checked = parse_ag_stream_json(DENIED)
    assert (checked["complete"], checked["reason"]) == (True, None)
    assert [step["step_index"] for step in checked["steps"]] == [0, 1, 2]
    observed = terminal_result(DENIED.encode())
    assert (observed["observation"], observed["status"], observed["response"]) == ("complete", "SUCCESS", "")
    assert observed["validation"] is None
    kind, payload = _drain(kit.replay(DENIED), "run the probe")[-1]
    assert kind == "terminal" and payload["response"] == "" and payload["denied_actions"] == ["command"]
    assert payload["usage"] == {"input_tokens": 9044, "cached_input_tokens": 0, "output_tokens": 49}
    assert payload["provider_telemetry"]["coverage"]["state"] == "MEASURED"
    assert "ag_stream_validation" not in payload


@TRANSPORTS
def test_e_c_denied_actions_next_to_a_reply_are_delivered_alike(kit, tmp_path, monkeypatch, capsys, stream):
    events = _denied_events()
    events[-1]["result"]["response"] = "answer-fragment-sentinel"
    code, out = _ask_ag(kit, tmp_path, monkeypatch, capsys, _stdout(events, stream), stream)
    assert (out["status"], out["response"], code) == ("delivered", "answer-fragment-sentinel", 0)
    assert "denied_actions" not in out and "empty_response" not in out  # names are read for an empty reply only


def _denied_only_mentioned() -> str:
    events = _denied_events()
    listed = events[-1]["result"].pop("denied_actions")
    events[3]["step_update"]["tool_info"]["denied_actions"] = listed
    events[4]["step_update"]["denied_actions"] = listed
    events[2]["step_update"]["text_delta"] = json.dumps({"denied_actions": listed})
    return _nd(events)


def test_e_d_mentions_outside_the_terminal_result_never_produce_names(kit, tmp_path, monkeypatch, capsys):
    mention = _denied_only_mentioned()
    assert base.denied_action_names(mention) == []
    events = _denied_events()
    events[-1]["result"]["response"] = json.dumps({"denied_actions": [{"action": "command"}]})
    del events[-1]["result"]["denied_actions"]
    assert base.denied_action_names(_nd(events)) == []
    code, out = _ask_ag(kit, tmp_path, monkeypatch, capsys, mention, True)
    assert (out["status"], out["response"], out["empty_response"], code) == ("delivered", "", True, 0)
    assert "denied_actions" not in out


def test_e_e_names_come_from_the_top_level_or_the_terminal_result_only():
    flat = '{"response":"","denied_actions":[{"action":"command"},{"action":"edit"}]}'
    nested = _nd(_denied_events())
    assert base.denied_action_names(flat) == ["command", "edit"]
    assert base.denied_action_names(nested) == ["command"]
    not_terminal = {"event": "step_update", "result": {"denied_actions": [{"action": "command"}]}}
    assert base.denied_action_names(json.dumps(not_terminal)) == []
