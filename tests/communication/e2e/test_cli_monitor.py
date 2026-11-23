"""Tests for peerhub monitor command."""

import json
import os
import re
from unittest.mock import patch

import pytest

from peerhub.cli.app import main
from peerhub.cli.view import _ANSI
from tests.communication.e2e.cli_extension_effects import lead_isolated_env


def _child_env(tmp_path, **extra):
    """The environment of a child `peerhub` process: no real Claude folder is read (#376 test 18)."""
    return lead_isolated_env(tmp_path, dict(os.environ, **extra))


def test_query_file_links_from_a_separate_monitor_process(tmp_path, monkeypatch, capsys):
    import sqlite3
    import subprocess
    import sys
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import closing
    from pathlib import Path
    from threading import Event

    from tests.communication.fakes import FakeRuntimeTarget

    workspace = tmp_path / "execution"
    workspace.mkdir()
    cwd = workspace / "caller"
    cwd.mkdir()
    query = cwd / "review 한 글.txt"
    query.write_text("same prompt", encoding="utf-8")
    alias = cwd / "replay.txt"
    alias.write_text("same prompt", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("same prompt", encoding="utf-8")
    (workspace / "review").touch()  # a matching stream ID still has no backing file
    db = str(tmp_path / "test.db")
    root = Path(__file__).resolve().parents[3]
    env = _child_env(tmp_path, TERM="xterm", PYTHONIOENCODING="utf-8", PYTHONUTF8="1", COLUMNS="120", LINES="60")
    for key in ("NO_COLOR", "PEERHUB_NO_LINKS", "PEERHUB_MONITOR_TEXT"):
        env.pop(key, None)
    monkeypatch.chdir(cwd)
    monkeypatch.setattr("peerhub.cli.support.refresh_dispatch_quota", lambda *_: None)
    entered, finish = Event(), Event()

    def wait_for_monitor():
        entered.set()
        assert finish.wait(20)

    target = FakeRuntimeTarget().script_deliver(("started", "execution"), ("call", wait_for_monitor),
                                                 ("terminal", {"response": "done"}))
    monkeypatch.setattr("peerhub.extensions.ask.CliRuntimeTarget", lambda *_a, **_kw: target)
    base_ask = ["--db", db, "ask", "cx", "--workspace", str(workspace), "--json"]
    ask_args = [*base_ask, "--stream", "review", "--request-id", "q1"]

    def monitor(*options, tty=True, vt=True, watch=False):
        stub = ("import sys; from peerhub.cli.app import main; from peerhub.cli import view; "
                f"sys.stdout.isatty = lambda: {tty!r}; view.enable_vt = lambda: {vt!r}; "
                "raise SystemExit(main())")
        mode = ["--cycles", "1", "--no-probe"] if watch else ["--snapshot"]
        return subprocess.run([sys.executable, "-c", stub, "--db", db, "monitor", *mode,
                               "--group", "activity", "--view", "rich", *options],
                              cwd=root, env=env, capture_output=True, encoding="utf-8", check=True, timeout=10).stdout

    file_link = f"\x1b]8;;{query.resolve().as_uri()}\x1b\\"
    db_target = Path(db).resolve().as_uri()

    def file_targets(screen):
        return set(re.findall(r"\x1b\]8;;(file://[^\x1b]+)\x1b\\", screen))

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(main, [*ask_args, "--query-file", query.name])
        try:
            assert entered.wait(10)
            running = monitor("--expand")
            assert file_link in running and "RUNNING 1" in running and "lease confirmed" in running
            running_json = json.loads(monitor("--json"))
            assert running_json["groups"]["activity"]["data"]["asks"][0]["state"] == "run"
        finally:
            finish.set()
        assert pending.result(timeout=10) == 0
    result = json.loads(capsys.readouterr().out)
    assert file_link in monitor(watch=True)
    recent = monitor("--expand")
    assert file_link in recent and "RECENT 1" in recent
    assert file_targets(recent) == {db_target, query.resolve().as_uri()}
    assert f"peerhub outcome {result['requested_record_id']}" in recent
    assert "accepted|rejected|retried" in recent
    for tty, vt in ((False, True), (True, False)):
        assert "file://" not in monitor(tty=tty, vt=vt)
    plain = monitor("--view", "plain")
    assert "\x1b" not in plain and "file://" not in plain
    env["PEERHUB_MONITOR_TEXT"] = "off"
    assert file_targets(monitor()) == {db_target} and "same prompt" not in monitor("--expand")
    del env["PEERHUB_MONITOR_TEXT"]
    assert main([*ask_args, "--query-file", alias.name]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "recovered_terminal"
    assert target.count("deliver") == 1
    assert file_link in monitor()
    with closing(sqlite3.connect(db)) as conn:
        ((raw,),) = conn.execute("SELECT detail FROM bridge_evidence WHERE kind='caller'").fetchall()
        facts = json.loads(raw)
    assert facts["query_file"] == str(query.resolve())
    assert facts["workspace"] == str(workspace.resolve())
    assert facts["cwd"] == str(cwd) and facts["stream_file"] is None
    public = monitor("--json")
    ended_json = json.loads(public)["groups"]["activity"]["data"]["asks"][0]
    assert ended_json["state"] == "ok"
    assert set(ended_json) == set(running_json["groups"]["activity"]["data"]["asks"][0]) == {
        "state", "superseded", "alive", "alive_reason", "peer", "profile", "model", "effort", "stream", "name", "lead",
        "started_at", "elapsed_s", "ended_at", "ended_exact", "age_s", "lease_left_s", "tokens", "ask_line", "reply_line",
        "ask_line_long", "reply_line_long", "note", "reasons", "session",
    }
    assert json.dumps(str(query.resolve()), ensure_ascii=False)[1:-1] not in public and all(f'"{key}"' not in public for key in ("query_file", "stream_file", "caller_file"))

    query.unlink()
    assert file_targets(monitor()) == {db_target}
    for metadata in ({}, {"workspace": facts["workspace"], "query_file": None, "stream_file": None},
                     {"workspace": facts["workspace"], "query_file": str(outside)}):
        legacy = {key: value for key, value in facts.items() if key not in ("workspace", "query_file", "stream_file")}
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("INSERT INTO bridge_evidence (delivery_id, stream_id, peer_id, kind, certainty, detail) "
                         "SELECT delivery_id, stream_id, peer_id, kind, certainty, ? FROM bridge_evidence "
                         "WHERE kind='caller' AND stream_id='review' LIMIT 1", (json.dumps(legacy | metadata),))
        assert file_targets(monitor()) == {db_target}
    assert main([*base_ask[:4], "inline prompt", *base_ask[4:], "--stream", "inline", "--request-id", "q2"]) == 0
    assert main([*base_ask, "--stream", "outside", "--request-id", "q3", "--query-file", str(outside)]) == 0
    with closing(sqlite3.connect(db)) as conn:
        rows = conn.execute("SELECT stream_id, detail FROM bridge_evidence WHERE kind='caller' AND stream_id != 'review'").fetchall()
    assert {stream for stream, _ in rows} == {"inline", "outside"}
    assert all(json.loads(raw)["query_file"] is None for _, raw in rows)
    assert file_targets(monitor()) == {db_target}


def test_child_peerhub_environments_read_no_real_claude_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "real-claude"))
    monkeypatch.setenv("PEERHUB_LEAD_DIRS", str(tmp_path / "registered"))
    env = _child_env(tmp_path, TERM="xterm")
    assert "PEERHUB_LEAD_DIRS" not in env and env["TERM"] == "xterm"
    assert env["CLAUDE_CONFIG_DIR"] != str(tmp_path / "real-claude")
    assert os.path.isdir(env["CLAUDE_CONFIG_DIR"]) and os.listdir(env["CLAUDE_CONFIG_DIR"]) == []
    assert os.path.commonpath([env["CLAUDE_CONFIG_DIR"], str(tmp_path)]) == str(tmp_path)


def test_monitor_groups_is_read_only_and_does_not_require_a_store(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "absent" / "core.db"
    with patch("peerhub.extensions.quota_capture.refresh_quota", side_effect=AssertionError("probe")):
        assert main(["--db", str(db), "monitor", "groups", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "2.0"
    assert [group["id"] for group in payload["groups"]] == [
        "overview", "activity", "peers", "execution", "sessions", "usage", "context", "capacity", "runtime", "coverage", "store"
    ]
    assert all(group.get("description") for group in payload["groups"])
    assert payload["selection_scope"] == "grouped_views"
    assert not db.parent.exists()


def test_monitor_groups_human_output_lists_ids_and_descriptions(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["monitor", "groups"]) == 0
    output = capsys.readouterr().out
    for group_id in ("overview", "execution", "sessions", "usage", "context", "capacity", "runtime", "coverage", "store"):
        assert group_id in output
    assert "delivery certainty" in output
    assert "quota" in output.lower()
    from peerhub.cli.monitor_preferences import GROUP_IDS
    with pytest.raises(SystemExit) as exc:
        main(["monitor", "--help"])
    assert exc.value.code == 0
    help_output = capsys.readouterr().out
    assert all(group_id in help_output for group_id in GROUP_IDS)


def test_monitor_groups_human_output_explains_invalid_preference(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / ".peerhub" / "config" / "monitor.toml"
    config.parent.mkdir(parents=True)
    config.write_text("invalid = [", encoding="utf-8")
    assert main(["monitor", "groups"]) == 1
    output = capsys.readouterr().out
    assert "Defaults: workspace (ERROR)" in output
    assert "Reason: invalid_toml" in output


def test_monitor_bare_watch_uses_default_grouped_view_and_explicit_schema_11(tmp_path, monkeypatch, capsys):
    from peerhub.cli import monitor

    db = _db(tmp_path)
    calls = []
    monkeypatch.setattr(monitor, "_run_monitor_grouped_watch",
                        lambda args, selected, preference: calls.append((selected, preference["source"])) or 0)
    assert main(["--db", str(db), "monitor", "--json", "--cycles", "1"]) == 0
    assert calls == [(["overview", "activity", "peers", "execution", "sessions", "usage", "context", "capacity", "coverage"],
                      "packaged")]
    calls.clear()
    # Explicit schema selection preserves the frozen 1.1 wire contract.
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota",
                        lambda *a, **kw: {"status": "OK", "observations": [], "warnings": []})
    assert main(["--db", str(db), "monitor", "--json", "--schema-version", "1.1", "--cycles", "1"]) == 0
    assert all(frame["schema_version"] == "1.1" for frame in map(json.loads, capsys.readouterr().out.splitlines()))
    assert calls == []


@pytest.mark.parametrize("json_output", [False, True])
def test_bare_monitor_renders_default_session_and_execution_groups(tmp_path, monkeypatch, capsys, json_output):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota",
                        lambda *a, **kw: {"status": "OK", "observations": [], "warnings": []})
    monkeypatch.setattr("peerhub.cli.monitor.sleep", lambda seconds: None)
    db = _db(tmp_path)
    argv = ["--db", str(db), "monitor", "--view", "plain", "--cycles", "1"]
    if json_output:
        argv.append("--json")
    code = main(argv)
    assert code in (0, 4)
    output = capsys.readouterr().out
    if json_output:
        frames = [json.loads(line) for line in output.splitlines()]
        assert len(frames) == 2
        for frame in frames:
            from peerhub.cli.monitor_preferences import GROUP_IDS
            assert frame["snapshot"]["selected_groups"] == [
                "overview", "activity", "peers", "execution", "sessions", "usage", "context", "capacity", "coverage"
            ]
            assert frame["snapshot"]["available_groups"] == list(GROUP_IDS)
            assert {"sessions", "execution"} <= set(frame["snapshot"]["groups"])
    else:  # #374: verdict, line 2, RUNNING (activity is a default group) and POOLS; the footer is --expand only
        assert output.startswith(("OK", "ATTN")) and "\nRUNNING 0\n" in output and "\nPOOLS " in output
        assert "SESSIONS [" not in output and "EXECUTION [" not in output and "delivery (sampled)" not in output
        assert '"groups"' not in output


def test_monitor_schema_11_rejects_grouped_selection(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main(["--db", str(db), "monitor", "--json", "--schema-version", "1.1", "--group", "sessions", "--cycles", "1"])
    assert exc.value.code == 2


def test_monitor_schema_11_rejects_snapshot(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main(["--db", str(db), "monitor", "--json", "--schema-version", "1.1", "--snapshot"])
    assert exc.value.code == 2


def test_monitor_legacy_alias_is_removed(tmp_path, capsys):
    from peerhub.cli.monitor import _legacy_alias_available

    assert _legacy_alias_available("0.19.9") is True
    assert _legacy_alias_available("0.20.0") is False
    with pytest.raises(SystemExit) as exc:
        main(["monitor", "--help"])
    assert exc.value.code == 0
    assert "--legacy" not in capsys.readouterr().out
    from tools.command_inventory import derive, option_keys
    assert "monitor --legacy" not in option_keys(derive())

    db = _db(tmp_path)
    for options in (["--snapshot", "--group", "store"], ["--json", "--cycles", "1"]):
        with pytest.raises(SystemExit) as exc:
            main(["--db", str(db), "monitor", "--legacy", *options])
        assert exc.value.code == 2
        assert "unrecognized arguments: --legacy" in capsys.readouterr().err


def test_monitor_schema_version_is_json_only_and_schema_11_rejects_grouped_views(tmp_path, capsys):
    db = _db(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main(["--db", str(db), "monitor", "--schema-version", "1.1", "--snapshot"])
    assert exc.value.code == 2
    assert "requires --json" in capsys.readouterr().err

    for args in (("--json", "--schema-version", "1.1", "--group", "store"),
                 ("--json", "--schema-version", "1.1", "--snapshot")):
        with pytest.raises(SystemExit) as exc:
            main(["--db", str(db), "monitor", *args])
        assert exc.value.code == 2


def test_schema_11_watch_is_explicit_legacy_ndjson(tmp_path, monkeypatch, capsys):
    from pathlib import Path

    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota",
                        lambda *a, **kw: {"status": "OK", "observations": [], "warnings": []})
    monkeypatch.setattr("peerhub.cli.monitor.sleep", lambda seconds: None)
    db = _db(tmp_path)
    assert main(["--db", str(db), "monitor", "--probe", "--json", "--schema-version", "1.1", "--cycles", "1"]) == 0
    output = capsys.readouterr()
    frames = [json.loads(line) for line in output.out.splitlines()]
    assert len(frames) == 2
    assert all(frame["schema_version"] == "1.1" for frame in frames)
    assert all("snapshot" in frame for frame in frames)
    assert output.err == ""
    fixture = Path(__file__).parent / "fixtures" / "monitor_0.16.1_schema_1.1.jsonl"
    expected = [json.loads(line) for line in fixture.read_text(encoding="utf-8").splitlines()]

    def normalize(frame):
        normalized = json.loads(json.dumps(frame))
        normalized["store_selection"]["path"] = "<DB>"
        normalized["snapshot"]["read_at"] = 0.0
        return normalized

    assert [normalize(frame) for frame in frames] == expected


def _assert_monitor_reset_countdown(rendered):
    lines = _ANSI.sub("", rendered).splitlines()
    # G.3–4: the POOLS row keeps a reset when the forecast is unavailable.
    header = next(line for line in lines if re.search(r"\bPOOLS \d+\b", line))
    assert "EXH/RESET" in header.split()
    row = next(line.split() for line in lines if line.split()[:2] == ["cx", "5h"])
    pair, = (cell for cell in row if "/" in cell)
    assert pair.replace("—", "-") == "-/4h58m"


@pytest.mark.parametrize("tty", [False, True])
def test_monitor_reset_countdown_tty_plain_parity(tmp_path, monkeypatch, tty):
    import io

    from peerhub.extensions.monitor_read_model import render_monitor_frame
    from peerhub.extensions.observation_model import EvidenceState, Observation, epoch_to_iso
    from tests.communication.harness.observation import T0, ObservationHarness

    h = ObservationHarness(tmp_path / "workspace")
    h.register_pool({"schema_version": "1.0", "resource_pool_id": "quota:cx:5h",
                     "provider": "openai", "kind": "QUOTA", "metadata": {}})
    h.obs.persist(Observation(
        observation_id="reset-fixture", subject_ref="cx", resource_pool_ref="quota:cx:5h",
        kind="quota", source="fixture", observed_at=epoch_to_iso(T0),
        captured_at=epoch_to_iso(T0), state=EvidenceState.MEASURED,
        payload={"remaining_fraction": 0.75, "resets_at": T0 + 18000},
    ))

    class Output(io.StringIO):
        def isatty(self):
            return tty

    output = Output()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr("peerhub.cli.view.enable_vt", lambda: True)
    monkeypatch.setenv("COLUMNS", "120")
    monkeypatch.setenv("LINES", "24")
    monkeypatch.setattr("peerhub.extensions.monitor_read_model.render_monitor_frame",
                        lambda db, groups, **kw: render_monitor_frame(db, groups, read_at=T0 + 120, **kw))

    assert main(["--db", str(h.db_path), "monitor", "--snapshot", "--group", "capacity"]) in (0, 4)
    _assert_monitor_reset_countdown(output.getvalue())


@pytest.mark.skipif(os.name == "nt", reason="POSIX PTY parity; simulated TTY parity runs on Windows")
def test_monitor_reset_countdown_posix_pty_plain_parity(tmp_path):
    import fcntl
    import pty
    import struct
    import subprocess
    import sys
    import termios
    import time

    from peerhub.extensions.observation_model import EvidenceState, Observation, epoch_to_iso
    from tests.communication.harness.observation import ObservationHarness

    h = ObservationHarness(tmp_path / "workspace")
    h.register_pool({"schema_version": "1.0", "resource_pool_id": "quota:cx:5h",
                     "provider": "openai", "kind": "QUOTA", "metadata": {}})
    now = time.time()
    h.obs.persist(Observation(
        observation_id="reset-fixture", subject_ref="cx", resource_pool_ref="quota:cx:5h",
        kind="quota", source="fixture", observed_at=epoch_to_iso(now),
        captured_at=epoch_to_iso(now), state=EvidenceState.MEASURED,
        payload={"remaining_fraction": 0.75, "resets_at": now + 17910},
    ))
    command = [sys.executable, "-m", "peerhub", "--db", str(h.db_path),
               "monitor", "--snapshot", "--group", "capacity"]
    env = _child_env(tmp_path, TERM="xterm", NO_COLOR="1", COLUMNS="120", LINES="24")
    plain = subprocess.run(command, capture_output=True, text=True, env=env, timeout=30)
    assert plain.returncode in (0, 4)
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 120, 0, 0))
    process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=subprocess.PIPE, env=env)
    os.close(slave)
    output = bytearray()
    try:
        while True:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break
            if not chunk:
                break
            output.extend(chunk)
        assert process.wait(timeout=30) in (0, 4)
    finally:
        os.close(master)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    for rendered in (plain.stdout, output.decode("utf-8")):
        _assert_monitor_reset_countdown(rendered)


def test_bare_monitor_default_tty_uses_grouped_sections(tmp_path, monkeypatch):
    import io

    from peerhub.cli import monitor

    class TTY(io.StringIO):
        @property
        def encoding(self):
            return "utf-8"

        def isatty(self):
            return True

    output = TTY()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr("peerhub.cli.view.enable_vt", lambda: True)
    monkeypatch.setattr(monitor, "sleep", lambda seconds: None)
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota",
                        lambda *a, **kw: {"status": "OK", "observations": [], "warnings": []})
    db = _db(tmp_path)
    assert main(["--db", str(db), "monitor", "--view", "rich", "--cycles", "1"]) in (0, 4)
    rendered = output.getvalue()
    assert "RUNNING 0" in rendered and "POOLS " in rendered  # #374: the compact sections; the footer is --expand only
    assert "SESSIONS [" not in rendered and "EXECUTION [" not in rendered
    assert '"groups"' not in rendered


def test_bare_monitor_grouped_output_supports_ascii_stdout(tmp_path, monkeypatch):
    import io

    class AsciiOutput(io.StringIO):
        encoding = "ascii"

        def write(self, value):
            value.encode(self.encoding)
            return super().write(value)

        def isatty(self):
            return False

    output = AsciiOutput()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr("peerhub.cli.monitor.sleep", lambda seconds: None)
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota",
                        lambda *a, **kw: {"status": "OK", "observations": [], "warnings": []})
    assert main(["--db", str(_db(tmp_path)), "monitor", "--view", "plain", "--cycles", "1"]) in (0, 4)
    assert "RUNNING 0" in output.getvalue() and "POOLS " in output.getvalue()  # #374: written to an ASCII stdout


def test_monitor_snapshot_does_not_refresh_providers(tmp_path, monkeypatch, capsys):
    db = _db(tmp_path)
    calls = []
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota",
                        lambda *args, **kwargs: calls.append("refresh") or {"status": "OK"})
    assert _run(db, "--snapshot", "--group", "store", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "2.0"
    assert payload["selected_groups"] == ["store"]
    assert "store" in payload["available_groups"]
    assert payload["preference"]["source"] == "command"
    assert payload["groups"]["store"]["coverage"]["read"] == "complete"
    assert calls == []


def test_monitor_snapshot_refuses_future_schema(tmp_path, capsys):
    import sqlite3

    db = _db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version=999")
    assert _run(db, "--snapshot", "--group", "store", "--json") == 6
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "FAILED"
    assert payload["errors"]["schema"] == "SchemaVersionError"


def test_monitor_snapshot_returns_error_for_selected_group_query_failure(tmp_path, capsys):
    import sqlite3

    db = _db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE observation_rollups")
    assert _run(db, "--snapshot", "--group", "capacity", "--json") == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["groups"]["capacity"]["status"] == "ERROR"


def test_monitor_usage_snapshot_propagates_required_scope_query_failure(tmp_path, capsys):
    from tests.communication.bridge.test_retained_sessions import _ProviderTelemetryRuntime
    from tests.communication.harness.bridge import BridgeHarness
    from tests.communication.bridge_helpers import bseed

    h = BridgeHarness(tmp_path / "bridge")
    bseed(h)
    h.delivery_cycle("b", "s", _ProviderTelemetryRuntime())
    with h.cs.transaction() as conn:
        conn.execute("DROP TABLE bridge_monitor_delivery_scope")
    assert main(["--db", str(h.db_path), "monitor", "--snapshot", "--group", "usage", "--json"]) == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PARTIAL"
    assert payload["groups"]["usage"]["status"] == "ERROR"


def test_monitor_snapshot_group_all_deduplicates(tmp_path, capsys):
    db = _db(tmp_path)
    assert _run(db, "--snapshot", "--group", "all", "--group", "all", "--json") in (0, 4)
    assert json.loads(capsys.readouterr().out)["selected_groups"] == [
        "overview", "activity", "peers", "execution", "sessions", "usage", "context", "capacity", "runtime", "coverage", "store"
    ]


def test_monitor_snapshot_plain_view_renders_group_heading(tmp_path, capsys):
    db = _db(tmp_path)
    assert _run(db, "--snapshot", "--group", "store", "--view", "plain") == 0
    output = capsys.readouterr().out
    # #374: `store` owns no compact section; lines 1-2 say what was not read and when
    assert output.startswith("OK? (asks, quota, accounts, facts not read)") and "\nPeerHub Monitor " in output
    assert "STORE [OK]" not in output
    assert '"groups"' not in output


def test_monitor_defaults_set_and_reset_workspace_layer(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(tmp_path / "global-config"))
    assert main(["monitor", "defaults", "set", "--group", "usage", "sessions", "usage", "--json"]) == 0
    written = json.loads(capsys.readouterr().out)
    assert written["selected_groups"] == ["sessions", "usage"]
    workspace_file = tmp_path / ".peerhub" / "config" / "monitor.toml"
    assert workspace_file.is_file()
    assert main(["monitor", "defaults", "show", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["selected_groups"] == ["sessions", "usage"]
    assert shown["preference"]["source"] == "workspace"
    assert main(["monitor", "defaults", "reset", "--json"]) == 0
    assert not workspace_file.exists()
    reset = json.loads(capsys.readouterr().out)
    assert reset["changed"] is True


def test_monitor_default_precedence_and_corrupt_effective_file_fail_closed(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    global_dir = tmp_path / "global"
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(global_dir))
    assert main(["monitor", "defaults", "set", "--global", "--group", "runtime", "store", "--json"]) == 0
    capsys.readouterr()
    assert main(["monitor", "defaults", "show", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["selected_groups"] == ["runtime", "store"]
    assert payload["preference"]["source"] == "global"
    workspace_file = tmp_path / ".peerhub" / "config" / "monitor.toml"
    workspace_file.parent.mkdir(parents=True)
    workspace_file.write_text("schema_version = 1\n[monitor]\ndefault_groups = ['unknown']\n", encoding="utf-8")
    db = tmp_path / "absent" / "core.db"
    assert main(["--db", str(db), "monitor", "groups", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ERROR"
    assert result["preference"]["reason"] == "invalid_group"
    assert all(item["default_selected"] is None for item in result["groups"])
    assert not db.parent.exists()


@pytest.mark.parametrize("layer", ["workspace", "global"])
@pytest.mark.parametrize("groups", [["capacity"], ["peers", "capacity"], ["all"]])
@pytest.mark.parametrize("json_output", [False, True])
def test_snapshot_explicit_groups_override_invalid_defaults(tmp_path, monkeypatch, capsys, layer, groups, json_output):
    from peerhub.cli.monitor_preferences import GROUP_IDS
    from peerhub.extensions.observation import ObservationStore
    from tests.communication.harness.bridge import BridgeHarness

    monkeypatch.chdir(tmp_path)
    global_dir = tmp_path / "global"
    monkeypatch.setenv("PEERHUB_CONFIG_HOME", str(global_dir))
    config = (tmp_path / ".peerhub" / "config" if layer == "workspace" else global_dir) / "monitor.toml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("invalid = [", encoding="utf-8")
    db = BridgeHarness(tmp_path / "workspace").db_path
    ObservationStore(db)
    options = [option for group in groups for option in ("--group", group)]
    with patch("peerhub.extensions.quota_capture.refresh_quota", side_effect=AssertionError("probe")):
        assert _run(db, "--snapshot", *options, *(["--json"] if json_output else ["--view", "plain"])) == 0
    output = capsys.readouterr().out
    if json_output:
        payload = json.loads(output)
        assert payload["preference"] == {"status": "OK", "source": "command", "reason": None}
        assert payload["selected_groups"] == (list(GROUP_IDS) if groups == ["all"] else groups)
    else:
        assert "POOLS" in output
    with patch("peerhub.extensions.quota_capture.refresh_quota", side_effect=AssertionError("probe")):
        assert _run(db, "--snapshot", "--json") == 1
    assert json.loads(capsys.readouterr().out)["preference"]["source"] == layer


def test_monitor_repair_rebuilds_projections_idempotently_without_provider_calls(tmp_path, monkeypatch, capsys):
    from tests.communication.bridge.test_retained_sessions import _ProviderTelemetryRuntime
    from tests.communication.harness.bridge import BridgeHarness
    from tests.communication.bridge_helpers import bseed, sql

    h = BridgeHarness(tmp_path / "workspace")
    bseed(h)
    h.delivery_cycle("b", "s", _ProviderTelemetryRuntime())
    db = h.db_path
    with h.cs.transaction() as conn:
        conn.execute("UPDATE bridge_monitor_delivery_scope SET terminal_count=17")
    calls = []
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota",
                        lambda *args, **kwargs: calls.append("refresh") or {"status": "OK"})
    assert main(["--db", str(db), "monitor", "repair", "--json"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["status"] == "OK" and first["complete"] is True
    assert sql(h, "SELECT terminal_count,unknown_count FROM bridge_monitor_delivery_scope") == [(1, 1)]
    before = sql(h, "SELECT * FROM bridge_monitor_delivery_scope")
    assert main(["--db", str(db), "monitor", "repair", "--json"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["status"] == "OK"
    assert sql(h, "SELECT * FROM bridge_monitor_delivery_scope") == before
    assert calls == []


@pytest.mark.parametrize("option", ["--interval-seconds", "--cycles"])
def test_monitor_snapshot_rejects_explicit_watch_options_including_defaults(tmp_path, option):
    db = _db(tmp_path)
    value = "60" if option == "--interval-seconds" else "0"
    assert _run(db, "--snapshot", option, value) == 2


@pytest.mark.parametrize("command", [
    ["monitor", "--view", "rich"], ["monitor", "--view", "plain"], ["monitor", "--json"],
    ["diag", "--view", "rich"], ["diag", "--view", "plain"], ["diag", "--json"],
    ["diag", "quota"], ["diag", "quota", "--json"], ["diag", "health"],
])
def test_missing_store_refused_before_probes(tmp_path, capsys, command):
    db = tmp_path / "absent" / "core.db"
    with patch("peerhub.extensions.quota_capture.refresh_quota", side_effect=AssertionError("probe")):
        assert main(["--db", str(db), *command]) == 5
    cap = capsys.readouterr()
    message = f"database not found: {db}"
    assert message in cap.err
    assert "next: run from the main checkout" in cap.err
    assert "PEERHUB_DB" in cap.err
    assert f'peerhub --db "{db}" peer register --peer local --name local' in cap.err
    if "--json" in command:
        envelope = json.loads(cap.out)
        assert envelope["status"] == "UNAVAILABLE" and envelope["error"] == message
    assert not db.parent.exists()


@pytest.mark.parametrize("view", ["rich", "plain"])
@pytest.mark.parametrize("tty,vt", [(False, True), (True, False), (True, True)])
@pytest.mark.parametrize("ending", ["normal", "interrupt", "error"])
def test_terminal_capability_and_cleanup(tmp_path, monkeypatch, view, tty, vt, ending):
    import io
    from peerhub.cli import monitor

    class Output(io.StringIO):
        @property
        def encoding(self):
            return "utf-8"

        def isatty(self):
            return tty

    output = Output()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr("peerhub.cli.view.enable_vt", lambda: vt)
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setenv("NO_COLOR", "")
    clock = [0.0]
    monkeypatch.setattr(monitor, "monotonic", lambda: clock[0])

    def sleep(seconds):
        if ending == "interrupt":
            raise KeyboardInterrupt
        if ending == "error":
            raise RuntimeError("render loop failed")
        clock[0] += seconds

    monkeypatch.setattr(monitor, "sleep", sleep)
    calls = []
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota",
                        lambda *a, **kw: calls.append("refresh") or {"status": "OK"})
    code = _run(_db(tmp_path), "--probe", "--view", view, "--cycles", "2", "--interval-seconds", "2.5")
    if ending == "error":
        assert code == 1
    elif ending == "normal":
        assert code in (0, 4)
    else:
        assert code == 0
    out = output.getvalue()
    assert " [H" not in out
    if tty and vt:
        assert "\x1b[H\x1b[2J" in out and "\x1b[K" in out and "\x1b[J" in out
        assert out.endswith("\x1b[0m\x1b[?25h\r\n") and "\x1b[?25l" in out
        assert "\x1b[?1049h" not in out and "\x1b[?1049l" not in out
        assert "\x1b[31m" not in out
    else:
        assert "\x1b" not in out
    assert len(calls) == (2 if ending == "normal" else 1)
    assert out.count("PeerHub") >= ((3 if tty else 2) if ending == "normal" else 1)


@pytest.mark.parametrize("probe", [False, True])
def test_json_on_tty_emits_only_one_object_per_cycle(tmp_path, monkeypatch, probe):
    import io

    class Output(io.StringIO):
        def isatty(self):
            return True

    output = Output()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr("peerhub.cli.view.enable_vt", lambda: pytest.fail("JSON must not enable VT"))
    monkeypatch.setattr("peerhub.cli.monitor.sleep", lambda seconds: None)
    refreshes = []
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota",
                        lambda *a, **kw: refreshes.append(1) or {"status": "OK"})
    options = ["--probe"] if probe else ["--no-probe"]
    assert _run(_db(tmp_path), "--json", "--schema-version", "1.1", "--cycles", "3", "--collect-every", "2", *options) == 0
    lines = output.getvalue().splitlines()
    frames = [json.loads(line) for line in lines]
    assert len(lines) == 4 and len(refreshes) == (2 if probe else 0)
    assert all(frame["schema_version"] == "1.1" for frame in frames)
    assert frames[0]["phase"] == "initial" and frames[0]["refresh"]["status"] == "NOT_STARTED"
    assert [frame["cycle"] for frame in frames[1:]] == [0, 1, 2]
    assert all(frame["snapshot"]["schema_version"] == "1.0" for frame in frames)
    assert "\x1b" not in output.getvalue() and " [H" not in output.getvalue()


def test_grouped_watch_emits_initial_and_cycle_frames_without_changing_collect_cadence(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota",
                        lambda *a, **kw: calls.append(1) or {"status": "OK", "observations": [], "warnings": []})
    monkeypatch.setattr("peerhub.cli.monitor.sleep", lambda seconds: None)
    assert _run(_db(tmp_path), "--probe", "--json", "--group", "store", "--cycles", "3", "--collect-every", "2") == 0
    frames = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [frame["phase"] for frame in frames] == ["initial", "complete", "complete", "complete"]
    assert [frame["cycle"] for frame in frames] == [0, 0, 1, 2]
    assert [frame["refresh"].get("status") for frame in frames] == ["NOT_STARTED", "OK", None, "OK"]
    assert all(frame["snapshot"]["selected_groups"] == ["store"] for frame in frames)
    assert len(calls) == 2


def test_monitor_corrupt_preference_still_emits_json_error(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / ".peerhub" / "config" / "monitor.toml"
    config.parent.mkdir(parents=True)
    config.write_text("broken = [", encoding="utf-8")
    assert main(["--db", str(_db(tmp_path)), "monitor", "--json", "--cycles", "1"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "ERROR"


def test_monitor_grouped_watch_plain_has_no_terminal_controls_or_unicode(tmp_path, monkeypatch, capsys):
    import io

    class Pipe(io.StringIO):
        def isatty(self):
            return False

    output = Pipe()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr("peerhub.cli.monitor.sleep", lambda seconds: None)
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota",
                        lambda *a, **kw: {"status": "OK", "observations": [], "warnings": []})
    assert _run(_db(tmp_path), "--view", "rich", "--group", "store", "--cycles", "1") == 0
    assert "\x1b" not in output.getvalue()
    assert "·" not in output.getvalue()


@pytest.mark.parametrize(("tty", "vt"), [(False, True), (True, False), (True, True)])
def test_grouped_watch_respects_tty_vt_and_no_color(tmp_path, monkeypatch, tty, vt):
    import io

    class Output(io.StringIO):
        def isatty(self):
            return tty

    output = Output()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr("peerhub.cli.view.enable_vt", lambda: vt)
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setenv("NO_COLOR", "")
    monkeypatch.setattr("peerhub.cli.monitor.sleep", lambda seconds: None)
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota",
                        lambda *a, **kw: {"status": "OK", "observations": [], "warnings": []})
    assert _run(_db(tmp_path), "--group", "store", "--view", "rich", "--cycles", "1") == 0
    value = output.getvalue()
    assert ("\x1b[?25l" in value) is (tty and vt)
    assert "\x1b[1;36m" not in value


def test_grouped_tty_shows_refreshing_before_provider_call(tmp_path, monkeypatch):
    import io

    class TTY(io.StringIO):
        def isatty(self):
            return True

        @property
        def encoding(self):
            return "utf-8"

    output = TTY()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr("peerhub.cli.view.enable_vt", lambda: True)
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setattr("peerhub.cli.monitor.sleep", lambda seconds: None)

    def refresh(*args, **kwargs):
        assert "REFRESHING" in output.getvalue()
        return {"status": "OK", "observations": [], "warnings": []}

    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota", refresh)
    assert _run(_db(tmp_path), "--probe", "--group", "store", "--cycles", "1") in (0, 4)
    assert output.getvalue().endswith("\x1b[0m\x1b[?25h\r\n")


def test_redirected_snapshot_keeps_full_human_group_frame(tmp_path, capsys):
    db = _db(tmp_path)
    code = main(["--db", str(db), "monitor", "--snapshot", "--group", "all"])
    output = capsys.readouterr().out

    assert code in (0, 4)
    # #374: the compact frame is the verdict, line 2, RUNNING, the suggestion and POOLS; PEERS and the footer are --expand
    assert "\nPOOLS " in output and "\nRUNNING " in output and "suggest:" in output
    assert "PEERS" not in output and "delivery (sampled)" not in output
    assert "SESSIONS [" not in output and "EXECUTION [" not in output
    assert "groups omitted" not in output.lower()
    assert "\x1b" not in output
    assert output.endswith("\n") and not output.endswith("\n\n")


@pytest.mark.skipif(os.name == "nt", reason="POSIX PTY smoke; Windows TTY behavior is covered by ConPTY/manual tests")
def test_posix_pty_snapshot_obeys_physical_viewport(tmp_path):
    import fcntl
    import pty
    import re
    import struct
    import subprocess
    import sys
    import termios
    from pathlib import Path

    from peerhub.cli.monitor_view import _width

    db = _db(tmp_path)
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 8, 40, 0, 0))
    env = _child_env(tmp_path, TERM="xterm", NO_COLOR="1", COLUMNS="120", LINES="60")
    root = Path(__file__).resolve().parents[3]
    process = subprocess.Popen(
        [sys.executable, "-m", "peerhub", "--db", str(db), "monitor", "--snapshot", "--group", "all"],
        cwd=root, env=env, stdin=slave, stdout=slave, stderr=subprocess.PIPE,
    )
    os.close(slave)
    output = bytearray()
    try:
        while True:
            try:
                chunk = os.read(master, 4096)
            except OSError:
                break  # PTY master reports EIO once the child closes its slave.
            if not chunk:
                break
            output.extend(chunk)
        assert process.wait(timeout=30) in (0, 4)
    finally:
        os.close(master)
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    rendered = output.decode("utf-8", errors="replace")
    lines = rendered.rstrip("\r\n").splitlines()
    assert len(lines) <= 7
    # #374 H2: the compact frame has no "+N rows hidden" line; it keeps line 1 and the section headers (with totals)
    assert re.match(r"^(OK|ATTN)", lines[0]) and "rows hidden" not in rendered
    assert all(_width(line) < 40 for line in lines)
    assert "COLUMNS" not in rendered
    assert "\x1b" not in rendered


def test_tty_countdown_redraw_reuses_snapshot_without_database_or_provider_calls(tmp_path, monkeypatch):
    import io

    class TTY(io.StringIO):
        @property
        def encoding(self):
            return "utf-8"

        def isatty(self):
            return True

        def fileno(self):
            return 25

    from peerhub.cli import monitor
    from peerhub.extensions.monitor_read_model import render_monitor_frame as real_render
    output = TTY()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr("peerhub.cli.view.enable_vt", lambda: True)
    monkeypatch.setenv("TERM", "xterm")
    import os
    geometry_calls = []
    sizes = iter([os.terminal_size((80, 24)), os.terminal_size((45, 8))] * 4)
    real_get_terminal_size = os.get_terminal_size

    def geometry(_fd):
        if _fd == 25:
            geometry_calls.append(1)
            return next(sizes)
        return real_get_terminal_size(_fd)

    monkeypatch.setattr("peerhub.cli.view.os.get_terminal_size", geometry)
    now = [0.0]
    reads = []
    refreshes = []

    def render(db, groups, **kw):
        reads.append((db, tuple(groups)))
        return real_render(db, groups)

    def refresh(*args, **kwargs):
        refreshes.append(1)
        return {"status": "OK", "observations": [], "warnings": []}

    monkeypatch.setattr("peerhub.extensions.monitor_read_model.render_monitor_frame", render)
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota", refresh)
    monkeypatch.setattr(monitor, "monotonic", lambda: now[0])
    monkeypatch.setattr(monitor, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))

    assert main(["--db", str(_db(tmp_path)), "monitor", "--probe", "--cycles", "2", "--interval-seconds", "2",
                 "--group", "store"]) in (0, 4)
    assert len(reads) == 3  # initial frame plus two completed collection cycles
    assert len(refreshes) == 2
    assert "refresh in 1s" in output.getvalue()
    assert len(geometry_calls) >= 4


def test_monitor_legacy_watch_preserves_legacy_dashboard_contract(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("peerhub.cli.monitor.sleep", lambda seconds: None)
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota",
                        lambda *a, **kw: {"status": "OK", "observations": [], "warnings": []})
    db = _db(tmp_path)
    assert main(["--db", str(db), "monitor", "--json", "--schema-version", "1.1", "--cycles", "1"]) in (0, 4)
    frames = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(frames) == 2
    assert all(frame["schema_version"] == "1.1" for frame in frames)


def test_monitor_emits_committed_snapshot_before_calling_provider(tmp_path, monkeypatch, capsys):
    events = []
    from peerhub.extensions.diag import ReadonlyDiag
    original = ReadonlyDiag.render

    def tracked(self, *args, **kwargs):
        events.append("snapshot")
        return original(self, *args, **kwargs)

    def refresh(*args, **kwargs):
        events.append("probe")
        return {"status": "OK", "observations": [], "warnings": []}

    with patch.object(ReadonlyDiag, "render", tracked), \
         patch("peerhub.extensions.quota_capture.refresh_quota", refresh), \
         patch("peerhub.cli.monitor.sleep", lambda seconds: None):
        assert _run(_db(tmp_path), "--probe", "--json", "--schema-version", "1.1", "--cycles", "1") == 0
    assert events == ["snapshot", "probe", "snapshot"]
    frames = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert frames[0]["phase"] == "initial" and frames[0]["refresh"]["status"] == "NOT_STARTED"
    assert frames[1]["phase"] == "complete"
    assert frames[0]["snapshot"].keys() == frames[1]["snapshot"].keys()


@pytest.mark.parametrize(("diag_status", "error", "refresh_status", "expected"), [
    ("PARTIAL", None, "OK", 4),
    ("FAILED", "SchemaVersionError: unsupported schema", "OK", 6),
    ("FAILED", "sqlite3.DatabaseError: damaged", "OK", 4),
    ("OK", None, "PARTIAL", 1),
])
def test_monitor_exit_code_reports_diagnostic_failure_before_refresh_status(
    tmp_path, monkeypatch, diag_status, error, refresh_status, expected
):
    from peerhub.extensions.diag import DiagnosticReport

    empty = DiagnosticReport(diag_status, 0.0, {}, {}, error)
    monkeypatch.setattr("peerhub.extensions.diag.ReadonlyDiag.render", lambda *a, **kw: empty)
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota",
                        lambda *a, **kw: {"status": refresh_status, "observations": [], "warnings": []})
    assert _run(_db(tmp_path), "--probe", "--json", "--schema-version", "1.1", "--cycles", "1") == expected


def test_monitor_skips_elapsed_monotonic_slots_after_slow_probe(tmp_path, monkeypatch):
    from peerhub.cli import monitor
    clock = [0.0]
    sleeps = []
    monkeypatch.setattr(monitor, "monotonic", lambda: clock[0])

    def slow_probe(*args, **kwargs):
        clock[0] += 8.0
        return {"status": "OK", "observations": [], "warnings": []}

    with patch("peerhub.extensions.quota_capture.refresh_quota", slow_probe), \
         patch("peerhub.cli.monitor.sleep", lambda seconds: sleeps.append(seconds)):
        assert _run(_db(tmp_path), "--probe", "--cycles", "2", "--interval-seconds", "5") in (0, 4)
    assert sleeps == []


def test_render_exception_restores_cursor(tmp_path, monkeypatch):
    import io
    from peerhub.cli import view

    class Output(io.StringIO):
        def isatty(self):
            return True

    output = Output()
    monkeypatch.setattr("sys.stdout", output)
    monkeypatch.setattr(view, "enable_vt", lambda: True)
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setattr("peerhub.cli.monitor.sleep", lambda seconds: None)
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota", lambda *a, **kw: {"status": "OK"})
    original = view.format_frame
    calls = []

    def render(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("render failed")
        return original(*args, **kwargs)

    # Use the snapshot loop to reach the second render without a real clock.
    monkeypatch.setattr(view, "format_frame", render)
    from peerhub.extensions.diag_watch import dashboard_snapshots
    monkeypatch.setattr("peerhub.extensions.diag_watch.dashboard_snapshots",
                        lambda path, **kw: dashboard_snapshots(path, **kw, sleep=lambda seconds: None))
    assert main(["--db", str(_db(tmp_path)), "diag", "--live", "--cycles", "2"]) == 1
    assert len(calls) == 2
    assert output.getvalue().endswith("\x1b[0m\x1b[?25h")


def _db(tmp_path):
    """A real M1 database so the read-only dashboard renders for real (only refresh_quota is faked)."""
    from peerhub.core.store import CoreStore
    from peerhub.extensions.observation import ObservationStore
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "m.db"
    CoreStore(path)
    ObservationStore(path)
    return path


def _spy_render(calls):
    from peerhub.extensions.diag import ReadonlyDiag
    original = ReadonlyDiag.render

    def render(self, *a, **kw):
        calls.append("snapshot")
        return original(self, *a, **kw)
    return render


def _spy_group_frame(calls):
    from peerhub.extensions.monitor_read_model import render_monitor_frame

    def render(db, groups, **kw):
        calls.append("snapshot")
        return render_monitor_frame(db, groups, **kw)
    return render


def _run(db, *args):
    try:
        return main(["--db", str(db), "monitor", *args])
    except SystemExit as exc:
        return exc.code


@pytest.mark.parametrize("args", [
    ["--interval-seconds", "0"],
    ["--interval-seconds", "-1"],
    ["--cycles", "-1"],
    ["--timeout-seconds", "0"],
    ["--collect-every", "0"],
])
def test_invalid_args_rejected_before_creation(tmp_path, capsys, args):
    calls = []
    def fake_refresh(*a, **kw):
        calls.append(kw)
        return {"status": "OK"}
    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh):
        assert _run(tmp_path / "m.db", *args) == 2
    assert calls == []
    assert not (tmp_path / "m.db").exists()


@pytest.mark.parametrize("option", ["--interval-seconds", "--timeout-seconds"])
@pytest.mark.parametrize("value", ["inf", "-inf", "nan", "0", "-0.1"])
@pytest.mark.parametrize("schema", ["1.1", "2.0"])
def test_nonfinite_monitor_timing_rejected_before_collection(tmp_path, monkeypatch, capsys, option, value, schema):
    db = _db(tmp_path)
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota",
                        lambda *a, **kw: pytest.fail("collection before validation"))
    monkeypatch.setattr("peerhub.cli.monitor.sleep", lambda seconds: pytest.fail("sleep before validation"))
    assert _run(db, "--json", "--schema-version", schema, "--cycles", "1", f"{option}={value}") == 2
    assert option in capsys.readouterr().err


def test_sleep_called_count_minus_1_times(tmp_path, capsys):
    # Deterministic clock: time advances only through sleep, so a slow runner cannot shrink the sleep (release-matrix flake, v0.24.6).
    calls = []
    sleeps = []
    clock = [1000.0]

    def fake_sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
    def fake_refresh(*a, **kw):
        calls.append("refresh")
        return {"status": "OK", "observations": [], "warnings": []}
    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh), \
         patch("peerhub.extensions.monitor_read_model.render_monitor_frame", _spy_group_frame(calls)), \
         patch("peerhub.cli.monitor.sleep", fake_sleep), patch("peerhub.cli.monitor.monotonic", lambda: clock[0]):
        assert _run(_db(tmp_path), "--probe", "--cycles", "3", "--interval-seconds", "42") in (0, 4)
    assert sleeps == pytest.approx([42.0, 42.0], abs=1e-9)
    assert calls == ["refresh", "snapshot", "refresh", "snapshot", "refresh", "snapshot"]


def test_collect_every_honoured(tmp_path, capsys):
    calls = []
    def fake_refresh(*a, **kw):
        calls.append("refresh")
        return {"status": "OK", "observations": [], "warnings": []}
    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh), \
         patch("peerhub.extensions.monitor_read_model.render_monitor_frame", _spy_group_frame(calls)), \
         patch("peerhub.cli.monitor.sleep", lambda s: None):
        assert _run(_db(tmp_path), "--probe", "--cycles", "4", "--collect-every", "2") in (0, 4)
    assert calls == ["refresh", "snapshot", "snapshot", "refresh", "snapshot", "snapshot"]


def test_guard_stop_after_first_violation(tmp_path, capsys):
    calls = []
    def fake_refresh(*a, **kw):
        calls.append("refresh")
        return {
            "status": "PARTIAL",
            "observations": [{"payload": {"evidence_ref": "agy_usage_consumed_tokens"}}],
            "warnings": []
        }
    
    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh), \
         patch("peerhub.extensions.monitor_read_model.render_monitor_frame", _spy_group_frame(calls)), \
         patch("peerhub.cli.monitor.sleep", lambda s: None):
        assert _run(_db(tmp_path), "--probe", "--cycles", "5") == 1
    assert calls == ["refresh"]
    err = capsys.readouterr().err
    assert "agy /usage consumed model tokens" in err


@pytest.mark.parametrize("kind, command", [("quota", "/usage"), ("account", "/credits")])
def test_guard_stop_message_names_the_command_that_consumed_tokens(tmp_path, capsys, kind, command):
    """A /credits violation (account observation) must stop watch mode and say /credits, not /usage."""
    calls = []
    def fake_refresh(*a, **kw):
        calls.append("refresh")
        return {"status": "PARTIAL", "observations": [{"kind": kind, "payload": {"evidence_ref": "agy_usage_consumed_tokens"}}], "warnings": []}

    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh), \
         patch("peerhub.extensions.monitor_read_model.render_monitor_frame", _spy_group_frame(calls)), \
         patch("peerhub.cli.monitor.sleep", lambda s: None):
        assert _run(_db(tmp_path), "--probe", "--cycles", "5") == 1
    assert calls == ["refresh"]
    err = capsys.readouterr().err
    assert f"agy {command} consumed model tokens" in err
    other = "/credits" if command == "/usage" else "/usage"
    assert f"agy {other} consumed" not in err


def test_guard_stop_message_for_schema_11_names_credits(tmp_path, capsys):
    def fake_refresh(*a, **kw):
        return {"status": "PARTIAL", "observations": [{"kind": "account", "payload": {"evidence_ref": "agy_usage_consumed_tokens"}}], "warnings": []}

    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh), patch("peerhub.cli.monitor_schema11.sleep", lambda s: None, create=True):
        assert _run(_db(tmp_path), "--probe", "--json", "--schema-version", "1.1", "--cycles", "3") == 1
    assert "agy /credits consumed model tokens" in capsys.readouterr().err


def test_guard_continue_with_flag(tmp_path, capsys):
    calls = []
    def fake_refresh(*a, **kw):
        calls.append("refresh")
        return {
            "status": "PARTIAL",
            "observations": [{"payload": {"evidence_ref": "agy_usage_consumed_tokens"}}],
            "warnings": []
        }
    
    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh), \
         patch("peerhub.extensions.monitor_read_model.render_monitor_frame", _spy_group_frame(calls)), \
         patch("peerhub.cli.monitor.sleep", lambda s: None):
        assert _run(_db(tmp_path), "--probe", "--cycles", "2", "--allow-agy-token-use") == 4
    assert calls == ["refresh", "snapshot", "refresh", "snapshot"]


def test_keyboard_interrupt_exit_0(tmp_path, capsys):
    calls = []

    def fake_refresh(*a, **kw):
        return {"status": "OK", "observations": [], "warnings": []}

    def sleep(*a):
        raise KeyboardInterrupt
    
    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh), \
         patch("peerhub.extensions.diag.ReadonlyDiag.render", _spy_render(calls)), \
         patch("peerhub.cli.monitor.sleep", sleep):
        assert _run(_db(tmp_path), "--cycles", "0") in (0, 4)


def test_json_frames_parse_and_carry_stable_keys(tmp_path, capsys):
    calls = []

    def fake_refresh(*a, **kw):
        return {"status": "OK", "observations": [{"payload": {}}, {"payload": {}}], "warnings": ["w1"]}
    
    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh), \
         patch("peerhub.extensions.diag.ReadonlyDiag.render", _spy_render(calls)), \
         patch("peerhub.cli.monitor.sleep", lambda s: None):
        assert _run(_db(tmp_path), "--probe", "--cycles", "2", "--collect-every", "2", "--json", "--schema-version", "1.1") == 0
    
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 3
    initial = json.loads(lines[0])
    assert initial["phase"] == "initial" and initial["refresh"]["status"] == "NOT_STARTED"
    f0 = json.loads(lines[1])
    assert f0["cycle"] == 0
    assert set(f0["refresh"].keys()) == {"status", "warnings", "observations"}
    assert f0["refresh"]["observations"] == 2
    assert "snapshot" in f0
    assert f0["snapshot"]["threats"]["state"] == "unknown"
    assert f0["snapshot"]["threats"]["fresh_pools"] == 0
    
    f1 = json.loads(lines[2])
    assert f1["cycle"] == 1
    assert set(f1["refresh"].keys()) == {"skipped"}
    assert f1["refresh"]["skipped"] is True


def test_snapshot_step_performs_no_writes(tmp_path, capsys):
    import hashlib
    from peerhub.core.store import CoreStore
    db_path = tmp_path / "db.sqlite"
    CoreStore(db_path)
    
    refreshes = []

    def fake_refresh(db, *a, **kw):
        from peerhub.core.store import CoreStore
        from peerhub.extensions.observation import ObservationStore
        refreshes.append(1)
        CoreStore(db)
        ObservationStore(db)
        return {"status": "OK", "observations": [{"payload": {}}], "warnings": []}

    hashes = []
    def record_hash(*args):
        hashes.append(hashlib.sha256(db_path.read_bytes()).hexdigest())

    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh), \
         patch("peerhub.cli.monitor.sleep", record_hash):
        assert _run(db_path, "--probe", "--cycles", "3", "--collect-every", "2") in (0, 4)

    # cycle 0 = refresh+snapshot, cycle 1 = snapshot only, cycle 2 = refresh+snapshot: the two sleeps bracket the snapshot-only cycle
    assert len(refreshes) == 2
    assert len(hashes) == 2
    assert hashes[0] == hashes[1]


def test_monitor_forwards_system_dir(tmp_path):
    from pathlib import Path
    calls = []

    def fake_refresh(db, peers, sys_dir=None, deadline_sec=15.0, cx_pools=None, cc_pools=None, ag_pools=None):
        assert cx_pools is not None and cc_pools is not None and ag_pools is not None
        calls.append(sys_dir)
        return {"status": "OK", "observations": [], "warnings": []}

    sys_path = tmp_path / "custom_sys"
    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh):
        assert _run(_db(tmp_path), "--probe", "--cycles", "1", "--system-dir", str(sys_path)) in (0, 4)

    assert calls == [Path(sys_path)]


@pytest.mark.parametrize("extra", [(), ("--json",), ("--json", "--schema-version", "1.1")])
def test_monitor_refresh_injects_the_same_profile_pools_as_observation_refresh(tmp_path, extra, capsys):
    """P4: monitor's refresh path declares the cx/cc/ag pools exactly like `observation refresh` (one shared helper)."""
    from peerhub.extensions.adapters.profiles import list_profiles

    def profiles(root, *, include_meta=False):
        if include_meta:
            return list_profiles(root, include_meta=True)
        return [{"profile": name, "fields": {"pools": {"value": pools}}} for name, pools in (
            ("cx.effort", ["X-5H", "X-7D"]), ("cc.fable", ["F-7D"]), ("cc.standard", ["C-5H", "C-7D"]),
            ("ag.standard", ["G-5H", "G-7D"]), ("ag.argon", ["A-7D"]))]
    seen = []

    def refresh(*args, **kwargs):
        seen.append({key: kwargs.get(key) for key in ("cx_pools", "cc_pools", "ag_pools")})
        return {"status": "OK", "observations": [], "warnings": []}

    db = _db(tmp_path)
    with patch("peerhub.extensions.adapters.profiles.list_profiles", profiles), \
         patch("peerhub.extensions.quota_capture.refresh_quota", refresh):
        main(["--db", str(db), "monitor", "--probe", "--cycles", "1", *extra])
        main(["--db", str(db), "observation", "refresh"])
    capsys.readouterr()
    expected = {"cx_pools": ["X-5H", "X-7D"], "cc_pools": ["C-5H", "C-7D", "F-7D"], "ag_pools": ["A-7D", "G-5H", "G-7D"]}
    assert seen == [expected, expected]  # monitor and observation refresh pass identical pools


def test_monitor_json_carries_projection_per_item(tmp_path, capsys):
    import time
    import uuid
    from peerhub.extensions.observation import ObservationStore
    from peerhub.extensions.observation_model import EvidenceState, Observation, ResourcePool

    db = _db(tmp_path)
    obs_store = ObservationStore(db)
    obs_store.register_resource_pool(ResourcePool(resource_pool_id="pool-1", provider="cx", kind="QUOTA"))
    t_meas = time.time() - 10.0
    meas_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t_meas))
    start_t = t_meas - 7200.0
    reset_t = t_meas + 10800.0
    obs_store.persist(Observation(
        observation_id=uuid.uuid4().hex,
        subject_ref="cx",
        resource_pool_ref="pool-1",
        kind="quota",
        source="cx-cli",
        observed_at=meas_iso,
        captured_at=meas_iso,
        state=EvidenceState.MEASURED,
        payload={"remaining_fraction": 0.20, "window_started_at": start_t, "resets_at": reset_t},
    ))

    def fake_refresh(db, *a, **kw):
        return {"status": "OK", "observations": [], "warnings": []}

    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh):
        assert _run(db, "--cycles", "1", "--json", "--schema-version", "1.1") == 0

    out =capsys.readouterr().out
    line = next(l for l in out.splitlines() if l.strip() and json.loads(l).get("phase") == "complete")
    frame = json.loads(line)
    assert frame["store_selection"] == {"path": str(db), "source": "explicit"}
    items = frame["snapshot"]["sections"]["observations"]["data"]["items"]
    assert len(items) >= 1
    item = next(i for i in items if i.get("resource_pool_ref") == "pool-1")
    assert "projection" in item
    projection = item["projection"]
    assert set(projection) == {"pace_pt", "projected_at_reset", "state", "exhaustion_at", "measured_at", "anchor_basis", "reason"}
    assert projection["state"] == "threat"
    assert projection["anchor_basis"] == "reset_minus_duration_inferred"
    assert projection["exhaustion_at"] < reset_t
    assert projection["pace_pt"] == pytest.approx(40, abs=0.01)


def test_monitor_json_privacy_scrubs_sentinel_ids_and_payloads(tmp_path, capsys):
    import hashlib
    import time
    import uuid
    from peerhub.extensions.observation import ObservationStore
    from peerhub.extensions.observation_model import EvidenceState, Observation, ResourcePool

    sentinel_credit_id = "RateLimitResetCredit_SECRET_TOKEN_SENTINEL_MONITOR_888"
    sentinel_id_hash = hashlib.sha256(sentinel_credit_id.encode()).hexdigest()[:12]
    sentinel_credit_secret = "SENTINEL_MONITOR_CREDIT_SECRET"
    sentinel_activity_secret = "SENTINEL_MONITOR_ACTIVITY_SECRET"
    sentinel_quota_secret = "SENTINEL_MONITOR_QUOTA_SECRET"

    db = _db(tmp_path / "mon_privacy")
    obs_store = ObservationStore(db)
    obs_store.register_resource_pool(ResourcePool(resource_pool_id="credit:cx:reset", provider="cx", kind="ACCOUNT"))
    obs_store.register_resource_pool(ResourcePool(
        resource_pool_id="pool-1", provider="cx", kind="QUOTA",
        metadata={"private_detail": "SENTINEL_MONITOR_POOL_METADATA_LEAK"},
    ))
    now = time.time()
    now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))

    obs_store.persist(Observation(
        observation_id=uuid.uuid4().hex,
        subject_ref="cx",
        resource_pool_ref="credit:cx:reset",
        kind="reset_credit",
        source="codex_app_server",
        observed_at=now_iso,
        captured_at=now_iso,
        state=EvidenceState.MEASURED,
        payload={
            "evidence_ref": "probe:cx:reset-credits",
            "available_count": 1,
            "nearest_expires_at": now + 7200,
            "leak": sentinel_credit_secret,
            "credits": [{
                "id_ref": sentinel_credit_id,
                "reset_type": "codexRateLimits",
                "status": "available",
                "granted_at": now - 3600,
                "expires_at": now + 7200,
                "title": "Weekly reset",
            }],
        },
    ))
    obs_store.persist(Observation(
        observation_id=uuid.uuid4().hex,
        subject_ref="cx",
        resource_pool_ref=None,
        kind="activity",
        source="session_bridge.ask",
        observed_at=now_iso,
        captured_at=now_iso,
        state=EvidenceState.MEASURED,
        payload={
            "status": "delivered",
            "operation_elapsed_seconds": 2.5,
            "prompt_text": sentinel_activity_secret,
            "reason": {"private_detail": "SENTINEL_MONITOR_NESTED_ACTIVITY_LEAK"},
        },
    ))
    obs_store.persist(Observation(
        observation_id=uuid.uuid4().hex,
        subject_ref="cx",
        resource_pool_ref="pool-1",
        kind="quota",
        source="cx-cli",
        observed_at=now_iso,
        captured_at=now_iso,
        state=EvidenceState.MEASURED,
        payload={
            "remaining_fraction": 0.5,
            "window_started_at": now - 3600,
            "resets_at": now + 14400,
            "internal_probe_details": sentinel_quota_secret,
        },
    ))

    with patch("peerhub.extensions.quota_capture.refresh_quota", lambda *a, **kw: {"status": "OK", "observations": [], "warnings": []}):
        assert _run(db, "--cycles", "1", "--json", "--schema-version", "1.1") == 0

    raw_out = capsys.readouterr().out
    assert sentinel_credit_id not in raw_out
    assert sentinel_credit_secret not in raw_out
    assert sentinel_activity_secret not in raw_out
    assert sentinel_quota_secret not in raw_out
    assert "SENTINEL" not in raw_out
    assert sentinel_id_hash in raw_out
    completed = next(json.loads(line) for line in raw_out.splitlines() if json.loads(line).get("phase") == "complete")
    items = completed["snapshot"]["sections"]["observations"]["data"]["items"]
    by_kind = {item["kind"]: item for item in items}
    assert set(by_kind) == {"reset_credit", "activity", "quota"}
    assert by_kind["reset_credit"]["payload"]["credits"][0]["id_hash"] == sentinel_id_hash
    assert by_kind["activity"]["payload"] == {"status": "delivered", "operation_elapsed_seconds": 2.5}
    assert by_kind["quota"]["payload"]["remaining_fraction"] == 0.5


@pytest.mark.parametrize("layout", ["--compact", "--expand", "--show-quota-notes"])
def test_monitor_layout_rejects_json(tmp_path, layout):
    db = _db(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main(["--db", str(db), "monitor", layout, "--json"])
    assert exc.value.code == 2


def test_monitor_compact_and_expand_are_mutually_exclusive(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["monitor", "--compact", "--expand"])
    assert exc.value.code == 2
    assert "not allowed with argument --compact" in capsys.readouterr().err


@pytest.mark.parametrize("mode", ["--snapshot", "--cycles"])
def test_monitor_explicit_compact_matches_default(tmp_path, monkeypatch, capsys, mode):
    from peerhub.cli import monitor
    from tests.communication.diag.test_monitor_plain_golden import full_payload

    db = _db(tmp_path)
    monkeypatch.setattr("peerhub.extensions.monitor_read_model.render_monitor_frame", lambda *a, **kw: full_payload())
    monkeypatch.setattr(monitor, "refresh_with_profile_pools",
                        lambda *a, **kw: {"status": "OK", "observations": [], "warnings": []})
    args = ["--db", str(db), "monitor", "--view", "plain", mode, *(["1"] if mode == "--cycles" else [])]
    assert main(args) == 0
    default = capsys.readouterr().out
    assert main([*args, "--compact"]) == 0
    assert capsys.readouterr().out == default


@pytest.mark.parametrize("mode", ["--snapshot", "--cycles"])
@pytest.mark.parametrize("expand", [False, True])
def test_monitor_quota_notes_option_reaches_human_renderer(tmp_path, monkeypatch, capsys, mode, expand):
    from peerhub.cli import monitor
    from tests.communication.diag.test_monitor_plain_golden import full_payload

    db = _db(tmp_path)
    monkeypatch.setattr("peerhub.extensions.monitor_read_model.render_monitor_frame", lambda *a, **kw: full_payload())
    monkeypatch.setattr(monitor, "refresh_with_profile_pools",
                        lambda *a, **kw: {"status": "OK", "observations": [], "warnings": []})
    seen = []
    monkeypatch.setattr(monitor, "_render_human_frame",
                        lambda payload, **kw: seen.append(kw["show_quota_notes"]) or "monitor")
    args = ["--db", str(db), "monitor", mode, *(["1"] if mode == "--cycles" else []),
            *(["--expand"] if expand else ["--compact"])]
    assert main(args) == 0
    assert seen and not any(seen)
    seen.clear()
    assert main([*args, "--show-quota-notes"]) == 0
    assert seen and all(seen)


def test_monitor_expand_snapshot_human_renders_all_rows(tmp_path, capsys):
    import sqlite3
    from peerhub.core.store import CoreStore
    from peerhub.extensions.bridge import ClaimStore, Bridge
    db = _db(tmp_path)
    cs = CoreStore(db)
    Bridge(cs, ClaimStore(db, "gen"))
    conn = sqlite3.connect(str(db))
    # Insert 4 session history rows
    for i in range(1, 5):
        conn.execute(
            "INSERT INTO bridge_session_history(session_handle,stream_id,peer_id,runtime_kind,external_session_id,adapter_fingerprint,session_generation,"
            "state,resumable,last_seen,created_at,binding) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"sess_{i:04x}", f"stream_{i}", "cx", "codex", f"ext_{i}", "fp", i, "ACTIVE", 1, 1000.0, 1000.0, '{"profile":"cx.standard"}'),
        )
    conn.commit()
    conn.close()

    # Compact default never exposes handles (#374: the sessions count moved to --expand with the footer).
    code = main(["--db", str(db), "monitor", "--snapshot", "--group", "sessions", "--view", "plain"])
    assert code == 0
    out = capsys.readouterr().out
    assert out.startswith("OK? (") and "sessions >=" not in out
    assert "sess_0001" not in out

    # Snapshot with --expand: all 4 rows rendered with handle, no not shown
    code = main(["--db", str(db), "monitor", "--snapshot", "--expand", "--group", "sessions", "--view", "plain"])
    assert code == 0
    out = capsys.readouterr().out
    assert "not shown" not in out
    assert "sess_0001" in out
    assert "sess_0004" in out


def test_monitor_expand_help_wording(capsys):
    """--expand help wording states loaded detail rows render without default caps."""
    with pytest.raises(SystemExit):
        main(["monitor", "--help"])
    out = capsys.readouterr().out
    assert "--expand" in out
    assert "--compact" in out
    assert "--show-quota-notes" in out
    clean_out = " ".join(out.split())
    assert "Render detailed grouped sections with bars, pace and forecasts" in clean_out


# ---- P6 (#376 s3): a store that never had a quota refresh is not an error for the human monitor ---------------------
def _never_refreshed_db(tmp_path):
    """A real store with one ask and no observation tables: no quota refresh ever ran on it."""
    from tests.communication.diag.test_monitor_activity import _bridge_store  # pyright: ignore[reportPrivateUsage]
    return _bridge_store(tmp_path).db_path


def _no_refresh(monkeypatch):
    """The watch's refresh is faked above the store, so the observation tables stay as they are. The frame width is
    pinned: a pipe takes it from COLUMNS, and the lines these tests compare must not wrap in a narrow environment."""
    monkeypatch.setattr("peerhub.cli.monitor.refresh_with_profile_pools",
                        lambda *a, **kw: {"status": "OK", "observations": [], "warnings": []})
    monkeypatch.setattr("peerhub.cli.monitor.sleep", lambda seconds: None)
    monkeypatch.setenv("COLUMNS", "100")


@pytest.mark.parametrize("args", [("--snapshot",), ("--snapshot", "--group", "capacity"), ("--snapshot", "--expand"),
                                  ("--cycles", "1"), ("--cycles", "2", "--expand")])
def test_human_monitor_exits_0_on_a_store_that_never_had_a_quota_refresh(tmp_path, capsys, monkeypatch, args):
    _no_refresh(monkeypatch)
    assert _run(_never_refreshed_db(tmp_path), "--view", "plain", *args) == 0
    out, err = capsys.readouterr()
    assert "diag_unavailable" not in err and "could not be read" not in err
    assert out.startswith("OK? (") and "quota, accounts, facts not read" in out.splitlines()[0]
    if "--expand" in args:
        assert "source capacity: no quota evidence" in out and "Quota capacity: no observations" in out
        assert "QUOTA [ERROR]" not in out and "database read failed" not in out
    else:
        assert "\nPOOLS 0\nno quota evidence; next: peerhub observation refresh\n" in out


@pytest.mark.parametrize("args", [("--snapshot", "--group", "capacity"), ("--snapshot",), ("--cycles", "1"),
                                  ("--cycles", "1", "--group", "capacity")])
def test_json_monitor_agrees_on_a_store_that_never_had_a_quota_refresh(tmp_path, capsys, monkeypatch, args):
    # 0.24.18 (#376 P6): no quota evidence, not an error, in the JSON too
    _no_refresh(monkeypatch)
    assert _run(_never_refreshed_db(tmp_path), "--json", *args) == 0
    out, err = capsys.readouterr()
    assert "diag_unavailable" not in err and "could not be read" not in err
    frames = [json.loads(line) for line in out.splitlines()]
    for payload in (frame.get("snapshot", frame) for frame in frames):
        assert payload["status"] == "OK" and payload["errors"] == {}
        assert payload["groups"]["capacity"]["status"] == "OK"
        assert payload["groups"]["capacity"]["data"] == {"observations": [], "reset_credits": [], "sample_size": 0,
                                                         "truncated": False}
        assert payload["alert_sources"]["capacity"] == "no_evidence"
        assert payload["alert_sources"]["reasons"] == {"capacity": "no quota evidence"}
        assert payload["alerts"] == [] and payload["min_quota"] is None


def test_json_monitor_on_damaged_observation_tables_stays_an_error(tmp_path, capsys):
    import sqlite3

    db = _db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE observations")  # two observation tables remain: not "never refreshed"
    assert _run(db, "--snapshot", "--group", "capacity", "--json") == 4
    out, err = capsys.readouterr()
    payload = json.loads(out)
    assert payload["groups"]["capacity"]["status"] == "ERROR" and payload["errors"] == {"capacity": "OperationalError"}
    assert payload["alert_sources"]["capacity"] == "error" and "[diag_unavailable]" in err


@pytest.mark.parametrize("args", [("--snapshot", "--group", "capacity"), ("--cycles", "1", "--group", "capacity")])
def test_damaged_observation_tables_stay_an_error_for_the_human_monitor(tmp_path, capsys, monkeypatch, args):
    import sqlite3

    _no_refresh(monkeypatch)
    db = _db(tmp_path)
    with sqlite3.connect(db) as conn:  # one observation table is missing among existing ones: not "never refreshed"
        conn.execute("DROP TABLE observation_rollups")
    assert _run(db, "--view", "plain", *args) == 4
    out, err = capsys.readouterr()
    assert "[diag_unavailable] Monitor evidence could not be read; next: peerhub diag health" in err
    assert out.startswith("ATTN 1? | store: capacity: OperationalError") and "\nDB:" in out  # a long path wraps after `DB:`


def test_an_incompatible_schema_keeps_its_own_message_and_code_for_the_human_monitor(tmp_path, capsys):
    import sqlite3

    db = _never_refreshed_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version=999")
    assert _run(db, "--snapshot", "--view", "plain") == 6
    assert "[schema_version_error] Unsupported database schema" in capsys.readouterr().err


def test_a_corrupt_store_keeps_its_own_message_for_the_human_monitor(tmp_path, capsys):
    db = tmp_path / "broken.db"
    db.write_bytes(b"this is not a database" * 64)
    assert _run(db, "--snapshot", "--view", "plain") == 1
    out, err = capsys.readouterr()
    assert "file is not a database" in err and "no quota evidence" not in out


def test_a_permission_failure_keeps_its_own_message_for_the_human_monitor(tmp_path, capsys, monkeypatch):
    def denied(*a, **kw):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("peerhub.extensions.monitor_read_model.render_monitor_frame", denied)
    assert _run(_never_refreshed_db(tmp_path), "--snapshot", "--view", "plain") == 1
    out, err = capsys.readouterr()
    assert "Permission denied" in err and "no quota evidence" not in out


# ---- P9 (#376 s3): every redraw between two reads is the last payload aged by the time since its read ----------------
@pytest.mark.parametrize("expand", [False, True])  # one rule in both frames: line 1 follows the aged rows
def test_every_tty_redraw_ages_the_one_payload_so_line_1_and_pools_agree(tmp_path, monkeypatch, expand):
    import io

    from peerhub.cli import monitor
    from peerhub.extensions.monitor_read_model import render_monitor_frame
    from peerhub.extensions.observation_model import EvidenceState, Observation, epoch_to_iso
    from tests.communication.harness.observation import T0, ObservationHarness

    h = ObservationHarness(tmp_path / "workspace")
    h.register_pool({"schema_version": "1.0", "resource_pool_id": "quota:cx:X-7D", "provider": "openai", "kind": "QUOTA",
                     "metadata": {}})
    h.obs.persist(Observation(
        observation_id="week", subject_ref="cx", resource_pool_ref="quota:cx:X-7D", kind="quota", source="fixture",
        observed_at=epoch_to_iso(T0), captured_at=epoch_to_iso(T0), state=EvidenceState.MEASURED,
        payload={"remaining_fraction": 0.12, "resets_at": T0 + 86400}))

    class TTY(io.StringIO):
        encoding = "utf-8"

        def isatty(self):
            return True

    clock = [1000.0]
    reads: list[float] = []
    frames: list[dict] = []
    render = monitor._render_human_frame

    def read(db, groups, **kw):  # the store's clock follows the monitor's; the evidence is 200 s old at the first read
        reads.append(clock[0])
        return render_monitor_frame(db, groups, read_at=T0 + 200 + (clock[0] - 1000.0), **kw)

    def sleep(seconds):
        clock[0] += seconds

    def spy(payload, **kw):
        text = render(payload, **kw)
        frames.append({"state": payload.get("refresh_state"), "elapsed": payload.get("display_elapsed_seconds"),
                       "next": payload.get("next_refresh_in"), "read": len(reads), "text": text})
        return text

    monkeypatch.setattr("sys.stdout", TTY())
    monkeypatch.setattr("peerhub.cli.view.enable_vt", lambda: True)
    monkeypatch.setattr("peerhub.cli.view.FrameOutput.geometry", staticmethod(lambda: (100, 30)))
    monkeypatch.setattr("peerhub.extensions.monitor_read_model.render_monitor_frame", read)
    monkeypatch.setattr(monitor, "refresh_with_profile_pools",
                        lambda *a, **kw: {"status": "OK", "observations": [], "warnings": []})
    monkeypatch.setattr(monitor, "monotonic", lambda: clock[0])
    monkeypatch.setattr(monitor, "sleep", sleep)
    monkeypatch.setattr(monitor, "_render_human_frame", spy)
    assert main(["--db", str(h.db_path), "monitor", "--probe", "--group", "capacity", "--view", "plain", "--cycles", "2",
                 "--interval-seconds", "150", *(["--expand"] if expand else [])]) == 0
    assert len(reads) == 3  # the initial frame and two cycles
    countdown = [frame for frame in frames if frame["state"] == "REFRESH OK" and frame["next"]]  # not the frames of a read
    refreshing = [frame for frame in frames if frame["state"] == "REFRESHING"]
    assert len(countdown) >= 140 and len(refreshing) == 2 and len(frames) == len(countdown) + len(refreshing) + 3
    for frame in frames:  # one payload, one moment: line 1 names the pool with the state its POOLS row shows
        lines = frame["text"].splitlines()
        if expand:  # the QUOTA table of --expand has no display state (a stale row has no REM); line 1 is ok or aged
            assert any(" X-7D " in line for line in lines[2:]), frame
            assert lines[0].endswith(("min quota 12% cx X-7D ok", "min quota 12% cx X-7D aged")), frame
            continue
        state = next(line for line in lines[2:] if " cx X-7D " in line).split()[2]
        assert state in ("ok", "aged") and lines[0].endswith(f"min quota 12% cx X-7D {state}"), frame
    # the pool passes its TTL (300 s) during the countdown: both lines turn to `aged` in the same redraw
    states = [frame["text"].splitlines()[0].rsplit(" ", 1)[1] for frame in countdown if frame["read"] == 2]
    assert states[0] == "ok" and states[-1] == "aged" and states == sorted(states, reverse=True)
    assert all(frame["elapsed"] is not None for frame in countdown + refreshing)
    # the REFRESHING frame is that payload at its age then, not the payload as it was read 150 s earlier
    last = refreshing[-1]
    assert last["elapsed"] == pytest.approx(150.0) and last["text"].splitlines()[0].endswith("cx X-7D aged")

