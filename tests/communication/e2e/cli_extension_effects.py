"""Behavioural option proofs for the new public first-party extension commands."""
import json
import io
from types import SimpleNamespace
from unittest.mock import patch

from peerhub.extensions.observation_model import EvidenceState
from peerhub.core.store import CoreStore
from tests.communication.fakes import FakeRuntimeTarget


def ask_effect(e, option):
    db = e.fresh(False)
    captured = []
    def target(kind, workspace, **kw):
        captured.append((kind, workspace, kw))
        rt = FakeRuntimeTarget()
        rt.binding = lambda: f"{kw.get('model')}/{kw.get('effort')}"
        rt.script_deliver(("started", "exec"), ("terminal", {"response": "hello"}))
        return rt
    argv = ["ask", "cx", "prompt", "--json"]
    expected = "prompt"
    if option == "peer":
        argv[1] = "cc"
    elif option == "prompt":
        argv[2] = expected = "different text"
    elif option == "--query-file":
        query = e.tmp / "query.txt"
        query.write_text("한글 prompt\n", encoding="utf-8")
        expected = query.read_text(encoding="utf-8")
        argv = ["ask", "cx", "--query-file", str(query), "--json"]
    elif option in ("--workspace", "-w"):
        argv += [option, str(e.tmp)]
    elif option in ("--json", "--writable"):
        argv += [option] if option != "--json" else []
    else:
        value = {"--stream": "conversation", "--request-id": "stable", "--author-peer": "operator",
                 "--model": "custom-model", "--effort": "low", "--timeout-seconds": "12",
                 "--profile": "cx.standard", "-p": "cx.standard", "--silence-timeout-seconds": "3",
                 "--max-output-bytes": "1234"}[option]
        argv += [option, value]
    with patch("peerhub.extensions.ask.CliRuntimeTarget", target):
        result = e.j(db, *argv)
        sid = result["stream_id"]
        prompt = next(r for r in CoreStore(db).read_records(sid) if r.kind == "prompt")
        assert prompt.body == expected and result["response"] == "hello"
        if option == "peer":
            assert result["peer_id"] == "cc" and captured[0][0] == "cc"
        elif option == "--stream":
            assert sid == "conversation"
        elif option == "--author-peer":
            assert prompt.author_peer_id == "operator"
        elif option == "--request-id":
            assert e.j(db, *argv)["status"] == "recovered_terminal"
            assert len([r for r in CoreStore(db).read_records(sid) if r.kind == "prompt"]) == 1
        elif option in ("--workspace", "-w"):
            assert captured[0][1] == str(e.tmp)
        elif option in ("--model", "--effort", "--timeout-seconds", "--max-output-bytes", "--profile", "-p", "--silence-timeout-seconds"):
            key = {"--model": "model", "--effort": "effort", "--timeout-seconds": "timeout_s",
                   "--profile": "profile", "-p": "profile", "--silence-timeout-seconds": "silence_timeout_s",
                   "--max-output-bytes": "max_bytes"}[option]
            expected_value = {"model": "custom-model", "effort": "low", "timeout_s": 12, "max_bytes": 1234,
                              "profile": "cx.standard", "silence_timeout_s": 3}[key]
            assert captured[0][2][key] == expected_value
        elif option in ("--writable"):
            key = option.removeprefix("--")
            assert captured[0][2][key] is True
            e.j(db, "ask", "cx", "again", "--json")  # not passing the flag keeps the default
            assert captured[1][2][key] is False
        elif option == "--json":
            code, out, err = e.run(db, "ask", "cx", "next")
            assert code == 0 and out.startswith("hello\nsession: sess_")


def diag_json(e):
    from peerhub.extensions.observation import ObservationStore
    db = e.fresh()
    ObservationStore(db)
    result = e.j(db, "diag", "--json")
    assert result["status"] == "OK" and "peers" in result["sections"]
    assert not e.run(db, "diag")[1].lstrip().startswith("{")


def session_selector_effect(e, option):
    from tests.communication.fakes.runtime import NativeRuntimeTarget
    rt = NativeRuntimeTarget()
    db = e.fresh(False)
    with patch("peerhub.extensions.ask.CliRuntimeTarget", lambda *a, **kw: rt):
        first = e.j(db, "ask", "cx", "first", "--json")
        fresh = e.j(db, "ask", "cx", "second", "--fresh", "--json")
        assert fresh["session"]["handle"] != first["session"]["handle"]
        assert fresh["session"]["reason"] == "explicit_fresh"
        if option == "--session":
            exact = e.j(db, "ask", "cx", "third", "--session", first["session"]["handle"], "--json")
            assert exact["session"]["handle"] == first["session"]["handle"]
            assert exact["session"]["mode"] == "resumed"


def diag_live(e, option):
    from peerhub.extensions.observation import ObservationStore
    db = e.fresh()
    ObservationStore(db)
    if option == "--view":
        seed_view_pool(db)
        rich_code, rich, _ = e.run(db, "diag", "--view", "rich")
        plain_code, plain, _ = e.run(db, "diag", "--view", "plain")
        assert rich_code == plain_code == 0
        assert_view_glyph_effect(rich, plain)
        assert_vt_view_effect(e, db, "diag")
        return
    pauses = []
    from peerhub.extensions.diag_watch import dashboard_snapshots
    def frames(path, **kw):
        return dashboard_snapshots(path, **kw, sleep=pauses.append)
    with patch("peerhub.extensions.diag_watch.dashboard_snapshots", frames):
        code, out, _ = e.run(db, "diag", "--live", "--json", "--cycles", "2", "--interval-seconds", ".125")
    assert code == 0 and len(out.splitlines()) == 2
    assert pauses == [.125]
    assert all(json.loads(line)["status"] == "OK" for line in out.splitlines())


def seed_view_pool(db):
    from peerhub.extensions.observation import ObservationStore
    from tests.communication.e2e.test_cli_diag_quota import seed

    ObservationStore(db)
    seed(db, pools=[("view-5H", "QUOTA")], rows=[
        ("view-peer", "quota", "view-5H", "MEASURED", 5,
         {"remaining_fraction": 0.5}, "view-fixture"),
    ])


def assert_view_glyph_effect(rich, plain):
    # Same A-G decisions, visibly different bars even with redirected output
    # and NO_COLOR. Fail if --view becomes inert or both styles use ASCII.
    assert rich != plain
    assert "█" in rich and "░" in rich
    assert "█" not in plain and "░" not in plain
    assert "#" in plain and "[" in plain
    for out in (rich, plain):
        assert "PEER" in out and "FORECAST" in out and "Threats: unknown" in out
        assert "view-5H" in out and "50%" in out
        assert "\x1b" not in out  # redirected output never emits colour/cursors


def assert_vt_view_effect(e, db, command):
    """The --view help promise applies to both ASCII and rich VT frames."""
    class Output(io.StringIO):
        @property
        def encoding(self):
            return "utf-8"

        def isatty(self):
            return True

    for style in ("plain", "rich"):
        for vt in (True, False):
            output = Output()
            args = (["--cycles", "1"] if command == "monitor" else ["--live", "--cycles", "1"])
            with patch("sys.stdout", output), patch("peerhub.cli.view.enable_vt", lambda: vt):
                assert e.run(db, command, "--view", style, *args)[0] == 0
            text = output.getvalue()
            if vt:
                assert "\x1b[?25l\x1b[H\x1b[2J" in text
                assert text.endswith("\x1b[0m\x1b[?25h")
            else:
                assert "\x1b" not in text
            assert " [H" not in text


def refresh_effect(e, option):
    db = e.fresh()
    captured = []
    def probe(**kw):
        captured.append(kw)
        ev = SimpleNamespace(state=EvidenceState.ABSENT, value=None, evidence_ref="test", source_tag="fake",
                             observed_at=100, captured_at=100)
        return [SimpleNamespace(evidence=ev)]
    argv = ["observation", "refresh", "--peers", "cc"]
    if option == "--peers":
        argv[-1] = "cx"
    elif option == "--system-dir":
        argv += [option, str(e.tmp)]
    elif option == "--timeout-seconds":
        argv += [option, "2"]
    with patch("peerhub.extensions.quota_probes.poll_claude_usage", probe), \
         patch("peerhub.extensions.quota_probes.poll_codex_usage", probe):
        result = e.j(db, *argv)
    assert result["observations"][0]["state"] == "ABSENT"
    if option == "--peers":
        assert captured[0]["instance_id"] == "cx" and result["observations"][0]["subject_ref"] == "cx"
    elif option == "--system-dir":
        assert captured[0]["sys_dir"] == e.tmp
    else:
        assert captured[0]["deadline_sec"] == 2


def monitor_effect(e, option):
    db = e.fresh()
    if option == "--view":
        seed_view_pool(db)
    captured = []
    def probe(**kw):
        captured.append(kw)
        from peerhub.extensions.observation_model import EvidenceState
        from types import SimpleNamespace
        ev = SimpleNamespace(state=EvidenceState.ABSENT, value=None, evidence_ref="test", source_tag="fake",
                             observed_at=100, captured_at=100)
        return [SimpleNamespace(evidence=ev)]
    def probe_agy(**kw):
        from peerhub.extensions.observation_model import EvidenceState
        from types import SimpleNamespace
        ev = SimpleNamespace(state=EvidenceState.ERROR, value=None, evidence_ref="agy_usage_consumed_tokens", source_tag="fake", observed_at=100, captured_at=100)
        return [SimpleNamespace(evidence=ev, extra={"reason": "x", "consumed_tokens": 1, "warning": "tokens used"})]
    argv = ["monitor", "--cycles", "1"]
    if option == "--interval-seconds":
        argv = ["monitor", "--cycles", "2", option, "3.14"]  # the pause happens between cycles: two cycles, one sleep
    elif option == "--cycles":
        argv = ["monitor", "--cycles", "2"]
    elif option == "--peers":
        argv += [option, "cx"]
    elif option == "--system-dir":
        argv += [option, str(e.tmp)]
    elif option == "--timeout-seconds":
        argv += [option, "2"]
    elif option == "--collect-every":
        argv = ["monitor", "--cycles", "2", "--collect-every", "2"]
    elif option == "--allow-agy-token-use":
        argv = ["monitor", "--cycles", "2", "--peers", "ag", option]
    elif option == "--json":
        argv += [option]
    elif option == "--view":
        argv += [option, "rich"]
    sleeps = []
    with patch("peerhub.extensions.quota_probes.poll_claude_usage", probe), \
         patch("peerhub.extensions.quota_probes.poll_codex_usage", probe), \
         patch("peerhub.extensions.quota_probes.poll_agy_usage", probe_agy if option == "--allow-agy-token-use" else probe), \
         patch("time.sleep", lambda s: sleeps.append(s)):
        code, out, err = e.run(db, *argv)
        if option == "--view":
            plain_code, plain, plain_err = e.run(db, "monitor", "--cycles", "1", "--view", "plain")
            assert_vt_view_effect(e, db, "monitor")
    if option == "--view":
        assert code == plain_code == 0 and "refresh:" in err and "refresh:" in plain_err
        assert_view_glyph_effect(out, plain)
    elif option == "--interval-seconds": assert sleeps == [3.14]
    elif option == "--cycles": assert len(sleeps) == 1
    elif option == "--peers": assert captured[0]["instance_id"] == "cx"
    elif option == "--system-dir":
        assert code == 0 and captured
        assert all(call["sys_dir"] == e.tmp for call in captured)
    elif option == "--timeout-seconds": assert captured[0]["deadline_sec"] == 2
    elif option == "--collect-every": assert len(captured) == 3
    elif option == "--allow-agy-token-use": assert code == 1 and err.count("tokens used") == 2
    elif option == "--json": import json; assert "cycle" in json.loads(out.splitlines()[0])

EFFECT = {f"ask {option}": (lambda e, option=option: ask_effect(e, option)) for option in (
    "peer", "prompt", "--query-file", "--stream", "--request-id", "--author-peer", "-w", "--workspace", "--model",
    "--effort", "-p", "--profile", "--writable", "--timeout-seconds", "--silence-timeout-seconds",
    "--max-output-bytes", "--json")}
EFFECT.update({f"ask {option}": (lambda e, option=option: session_selector_effect(e, option))
               for option in ("--fresh", "--session")})
EFFECT["diag --json"] = diag_json
EFFECT.update({f"diag {option}": (lambda e, option=option: diag_live(e, option))
               for option in ("--live", "--interval-seconds", "--cycles", "--view")})
EFFECT.update({f"observation refresh {option}": (lambda e, option=option: refresh_effect(e, option))
               for option in ("--peers", "--system-dir", "--timeout-seconds")})
EFFECT.update({f"monitor {option}": (lambda e, option=option: monitor_effect(e, option))
               for option in ("--interval-seconds", "--cycles", "--peers", "--system-dir", "--timeout-seconds", "--json", "--allow-agy-token-use", "--collect-every", "--view")})
