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


@pytest.mark.parametrize("command, expected", [
    (["peer"], ("show", "alias of get")),
    (["peer", "show"], ("alias of get",)),
    (["ask"], ("--query-file", "--prompt-file")),
])
def test_aliases_appear_in_help(command, expected, capsys):
    with pytest.raises(SystemExit) as exc:
        app.build_parser().parse_args([*command, "--help"])
    assert exc.value.code == 0
    output = capsys.readouterr().out
    assert all(text in output for text in expected)
