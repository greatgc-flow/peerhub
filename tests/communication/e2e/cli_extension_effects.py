"""Behavioural option proofs for the new public first-party extension commands."""
import json
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
    elif option == "--json":
        pass
    else:
        value = {"--stream": "conversation", "--request-id": "stable", "--author": "operator",
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
        elif option == "--author":
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
        elif option == "--json":
            code, out, err = e.run(db, "ask", "cx", "next")
            assert code == 0 and out.strip() == "hello"


def diag_json(e):
    from peerhub.extensions.observation import ObservationStore
    db = e.fresh()
    ObservationStore(db)
    result = e.j(db, "diag", "--json")
    assert result["status"] == "OK" and "peers" in result["sections"]
    assert not e.run(db, "diag")[1].lstrip().startswith("{")


def diag_live(e, option):
    from peerhub.extensions.observation import ObservationStore
    db = e.fresh()
    ObservationStore(db)
    pauses = []
    from peerhub.extensions.diag_watch import dashboard_snapshots
    def frames(path, **kw):
        return dashboard_snapshots(path, **kw, sleep=pauses.append)
    with patch("peerhub.extensions.diag_watch.dashboard_snapshots", frames):
        code, out, _ = e.run(db, "diag", "--live", "--json", "--count", "2", "--interval-seconds", ".125")
    assert code == 0 and len(out.splitlines()) == 2
    assert pauses == [.125]
    assert all(json.loads(line)["status"] == "OK" for line in out.splitlines())


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
    elif option == "--sys-dir":
        argv += [option, str(e.tmp)]
    elif option == "--timeout-seconds":
        argv += [option, "2"]
    with patch("peerhub.extensions.quota_probes.poll_claude_usage", probe), \
         patch("peerhub.extensions.quota_probes.poll_codex_usage", probe):
        result = e.j(db, *argv)
    assert result["observations"][0]["state"] == "ABSENT"
    if option == "--peers":
        assert captured[0]["instance_id"] == "cx" and result["observations"][0]["subject_ref"] == "cx"
    elif option == "--sys-dir":
        assert captured[0]["sys_dir"] == e.tmp
    else:
        assert captured[0]["deadline_sec"] == 2


EFFECT = {f"ask {option}": (lambda e, option=option: ask_effect(e, option)) for option in (
    "peer", "prompt", "--query-file", "--stream", "--request-id", "--author", "--workspace", "-w", "--model",
    "--effort", "--timeout-seconds", "--max-output-bytes", "--json", "--profile", "-p", "--silence-timeout-seconds")}
EFFECT["diag --json"] = diag_json
EFFECT.update({f"diag {option}": (lambda e, option=option: diag_live(e, option))
               for option in ("--live", "--interval-seconds", "--count")})
EFFECT.update({f"observation refresh {option}": (lambda e, option=option: refresh_effect(e, option))
               for option in ("--peers", "--sys-dir", "--timeout-seconds")})
