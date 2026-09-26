"""`ask --effort-hint` through the real CLI entry, with the peer dispatch itself stubbed."""
from pathlib import Path

import pytest

import peerhub.cli as cli_mod
from peerhub.cli import main


class _Result:
    response_text = "ok"
    error_code = None
    request_state = "SUCCEEDED_VERIFIED"


@pytest.fixture
def ws(tmp_path: Path, monkeypatch):
    assert main(["workspace", "init", "--workspace", str(tmp_path)]) == 0
    seen = {}

    def fake_execute(request, **kw):
        seen["profile"] = request.profile_id
        seen["prompt"] = request.prompt
        return _Result()

    monkeypatch.setattr(cli_mod, "execute_direct_ask", fake_execute)
    monkeypatch.setattr(cli_mod, "_ask_exit_code", lambda result: 0)
    return tmp_path, seen


def _policy(ws: Path, mode: str):
    cfg = ws / ".peerhub" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "dispatch-policy.toml").write_text(
        f'[routing]\neffort_routing = "{mode}"\n[routing.preference_map]\n'
        '"deep" = ["ag.deepthink", "cx.deepthink", "cx.effort"]\n',
        encoding="utf-8",
    )


def test_opt_in_routes_the_hint_to_the_first_eligible_profile(ws, capsys):
    path, seen = ws
    _policy(path, "opt-in")
    assert main(["ask", "cx", "hello", "--effort-hint", "deep", "--workspace", str(path)]) == 0
    assert seen["profile"] == "cx.deepthink"


def test_default_advisory_leaves_the_default_profile_and_reports_the_suggestion(ws, capsys):
    path, seen = ws
    _policy(path, "advisory")
    assert main(["ask", "cx", "hello", "--effort-hint", "deep", "--workspace", str(path)]) == 0
    assert seen["profile"] is None
    assert "advisory" in capsys.readouterr().err


def test_cli_override_activates_opt_in_for_one_call(ws):
    path, seen = ws
    _policy(path, "advisory")
    assert main(["ask", "cx", "hello", "--effort-hint", "deep", "--effort-routing", "opt-in",
                 "--workspace", str(path)]) == 0
    assert seen["profile"] == "cx.deepthink"


def test_explicit_profile_pins_and_unknown_hint_is_exit_2(ws, capsys):
    path, seen = ws
    _policy(path, "opt-in")
    assert main(["ask", "cx", "hello", "-p", "cx.standard", "--effort-hint", "deep",
                 "--workspace", str(path)]) == 0
    assert seen["profile"] == "cx.standard"
    assert main(["ask", "cx", "hello", "--effort-hint", "nope", "--workspace", str(path)]) == 2
    assert "unknown effort hint" in capsys.readouterr().err


def test_query_file_prompt_reaches_the_dispatch_unchanged(ws):
    path, seen = ws
    q = path / "q.txt"
    q.write_bytes("첫 줄\r\n둘째\n".encode("utf-8"))
    assert main(["ask", "cx", "--query-file", str(q), "--workspace", str(path)]) == 0
    assert seen["prompt"] == "첫 줄\r\n둘째\n"
