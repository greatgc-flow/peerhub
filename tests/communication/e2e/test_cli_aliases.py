"""Public CLI aliases resolve to the same arguments and output."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from peerhub.cli import app, core_cmd
from peerhub.extensions import profile_stats
from peerhub.core import load_limits
from peerhub.core.models import Peer


def _equivalent(capsys, canonical: list[str], alias: list[str]) -> str:
    parser = app.build_parser()
    assert parser.parse_args(alias) == parser.parse_args(canonical)
    assert app.main(canonical) == 0
    expected = capsys.readouterr()
    assert app.main(alias) == 0
    assert capsys.readouterr() == expected
    return expected.out


def test_peer_show_alias(monkeypatch, capsys):
    peer = Peer(peer_id="reader", display_name="Demo reader")
    monkeypatch.setattr(core_cmd, "CoreStore", lambda db, *, readonly: SimpleNamespace(
        get_peer={peer.peer_id: peer}.get))
    db = ["--db", __file__]
    options = ["--peer", peer.peer_id, "--json"]
    output = _equivalent(capsys, [*db, "peer", "get", *options], [*db, "peer", "show", *options])
    assert json.loads(output) == peer.model_dump(mode="json")


@pytest.mark.parametrize("action, positional", [("list", []), ("show", ["cx.standard"]), ("stats", []), ("check", [])])
def test_profile_workspace_short_option(action, positional, monkeypatch, capsys):
    workspace = Path(__file__).resolve().parent
    missing_db = Path(__file__).with_suffix(".db")
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(missing_db))
    monkeypatch.setattr(profile_stats, "installed_models", lambda provider, workspace: None)
    command = ["--db", str(missing_db), "profile", action, *positional, "--json"]
    output = _equivalent(capsys, [*command, "--workspace", str(workspace)], [*command, "-w", str(workspace)])
    assert json.loads(output)["workspace"] == str(workspace)


def test_ask_prompt_file_alias(capsys, monkeypatch):
    monkeypatch.setattr(load_limits, "read_resources", load_limits.ResourceSample)
    db = str(Path(__file__).with_suffix(".db"))
    request_id = "file-alias-preview"
    command = ["--db", db, "ask", "cx", "--dry-run", "--json", "--request-id", request_id]
    output = _equivalent(capsys, [*command, "--query-file", __file__], [*command, "--prompt-file", __file__])
    assert json.loads(output)["request_id"] == request_id


def test_ask_combines_query_files_in_option_order(capsys, monkeypatch):
    from peerhub.extensions import ask as ask_module

    files = {"z-NOW.md": b"\xef\xbb\xbfcurrent state\r\n\r\n", "a-task.md": b"\n  task delta\r\n"}
    monkeypatch.setattr(Path, "read_bytes", lambda path: files[path.name])

    def fake_ask(db, peer, prompt, **options):
        assert peer == "ag"
        assert prompt == "current state\n\n  task delta\n"
        assert options["query_file"] == "a-task.md"
        return {"status": "delivered", "response": "ok"}

    monkeypatch.setattr(ask_module, "ask", fake_ask)
    assert app.main(["--db", __file__, "ask", "ag", "--writable", "--json",
                     "--query-file", "z-NOW.md", "--prompt-file", "a-task.md"]) == 0
    assert json.loads(capsys.readouterr().out)["response"] == "ok"


def test_ask_rejects_positional_prompt_with_repeated_query_files(capsys, monkeypatch):
    def unexpected_resolution(*args, **kwargs):
        pytest.fail("ask must validate the prompt source before selecting a store")

    monkeypatch.setattr(app, "select_store", unexpected_resolution)
    with pytest.raises(SystemExit) as exc:
        app.main(["ask", "ag", "task delta", "--query-file", "NOW.md", "--query-file", "task.md"])
    assert exc.value.code == 2
    assert "ask requires exactly one of prompt or --query-file" in capsys.readouterr().err


def test_ask_missing_prompt_mentions_prompt_file_and_inline_usage_hint(capsys, monkeypatch):
    monkeypatch.setattr(app, "select_store", lambda *a, **kw: pytest.fail("must not select store"))
    with pytest.raises(SystemExit) as exc:
        app.main(["ask", "cx"])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "ask requires exactly one of prompt or --query-file or --prompt-file" in err
    assert 'next: peerhub ask cx "prompt" (or --query-file PATH)' in err


def test_ask_json_without_prompt_emits_one_consistent_hint(capsys, monkeypatch):
    monkeypatch.setattr(app, "select_store", lambda *a, **kw: pytest.fail("must not select store"))
    assert app.main(["ask", "cx", "--json"]) == 2
    captured = capsys.readouterr()
    err_lines = captured.err.splitlines()
    assert len(err_lines) == 1
    assert err_lines[0].startswith("[invalid_arguments] ask requires exactly one of prompt or --query-file")
    assert 'next: peerhub ask cx "prompt" (or --query-file PATH)' in err_lines[0]
    assert "ask_unexpected_failure" not in captured.err
    payload = json.loads(captured.out)
    assert payload["error"]["code"] == "invalid_arguments"
    assert "ask requires exactly one of prompt" in payload["error"]["message"]
    assert payload["guidance"]["code"] == "invalid_arguments"
    assert 'peerhub ask cx "prompt" (or --query-file PATH)' in payload["guidance"]["summary"]


@pytest.mark.parametrize("flags", [[], ["--dry-run"], ["--json"], ["--dry-run", "--json"]])
def test_ask_missing_or_unreadable_query_file_is_user_error(tmp_path, capsys, flags):
    db = str(tmp_path / "core.db")
    missing = str(tmp_path / "missing-prompt.txt")
    assert app.main(["--db", db, "ask", "cx", "--query-file", missing, *flags]) == 2
    captured = capsys.readouterr()
    assert len(captured.err.splitlines()) == 1
    assert captured.err.startswith("[invalid_arguments] cannot read --query-file")
    assert missing in captured.err
    assert "check the path" in captured.err
    assert "[Errno" not in captured.err and "ask_unexpected_failure" not in captured.err
    if "--json" in flags:
        payload = json.loads(captured.out)
        assert payload["error"]["code"] == "invalid_arguments"
        assert missing in payload["error"]["message"] and "[Errno" not in payload["error"]["message"]
        assert payload["guidance"]["code"] == "invalid_arguments"
        assert "check the path" in payload["guidance"]["summary"]
    else:
        assert not captured.out

    bad_utf8 = tmp_path / "bad.bin"
    bad_utf8.write_bytes(b"\xff\xfe\x80")
    for unreadable in (str(tmp_path), str(bad_utf8)):
        assert app.main(["--db", db, "ask", "cx", "--query-file", unreadable, *flags]) == 2
        captured = capsys.readouterr()
        assert "[invalid_arguments]" in captured.err and unreadable in captured.err and "check the path" in captured.err
        assert "[Errno" not in captured.err and "ask_unexpected_failure" not in captured.err


@pytest.mark.parametrize("command, expected", [
    (["peer"], ("show", "alias of get")),
    (["peer", "show"], ("alias of get",)),
    (["ask"], ("--query-file", "--prompt-file", "Repeatable UTF-8 prompt file")),
])
def test_aliases_appear_in_help(command, expected, capsys):
    with pytest.raises(SystemExit) as exc:
        app.build_parser().parse_args([*command, "--help"])
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert all(text in output for text in expected)
