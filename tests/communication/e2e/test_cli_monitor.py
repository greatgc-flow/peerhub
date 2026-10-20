"""Tests for peerhub monitor command."""

import json
import os
from unittest.mock import patch

import pytest

from peerhub.cli.app import main


def test_monitor_groups_is_read_only_and_does_not_require_a_store(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    db = tmp_path / "absent" / "core.db"
    with patch("peerhub.extensions.quota_capture.refresh_quota", side_effect=AssertionError("probe")):
        assert main(["--db", str(db), "monitor", "groups", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "2.0"
    assert [group["id"] for group in payload["groups"]] == [
        "overview", "peers", "execution", "sessions", "usage", "context", "capacity", "runtime", "coverage", "store"
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
    assert calls == [(["overview", "peers", "execution", "sessions", "usage", "context", "capacity", "coverage"], "packaged")]
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
                "overview", "peers", "execution", "sessions", "usage", "context", "capacity", "coverage"
            ]
            assert frame["snapshot"]["available_groups"] == list(GROUP_IDS)
            assert {"sessions", "execution"} <= set(frame["snapshot"]["groups"])
    else:
        assert "SESSIONS [" in output and "EXECUTION [" in output
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
    assert main(["--db", str(db), "monitor", "--json", "--schema-version", "1.1", "--cycles", "1"]) == 0
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
                        lambda db, groups: render_monitor_frame(db, groups, read_at=T0 + 120))

    assert main(["--db", str(h.db_path), "monitor", "--snapshot", "--group", "capacity"]) in (0, 4)
    assert "RESET-IN" in output.getvalue()
    assert "4h58m" in output.getvalue()


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
    env = dict(os.environ, TERM="xterm", NO_COLOR="1", COLUMNS="120", LINES="24")
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
        assert "RESET-IN" in rendered
        assert "4h58m" in rendered


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
    assert "SESSIONS [" in rendered and "EXECUTION [" in rendered
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
    assert "EXECUTION [" in output.getvalue()


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
        "overview", "peers", "execution", "sessions", "usage", "context", "capacity", "runtime", "coverage", "store"
    ]


def test_monitor_snapshot_plain_view_renders_group_heading(tmp_path, capsys):
    db = _db(tmp_path)
    assert _run(db, "--snapshot", "--group", "store", "--view", "plain") == 0
    output = capsys.readouterr().out
    assert "STORE [OK]" in output
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
        assert "QUOTA" in output
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
    assert f"Create one explicitly: peerhub --db {db} peer register --peer <id> --name <name>" in cap.err
    if "--json" in command or "health" in command:
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
    code = _run(_db(tmp_path), "--view", view, "--cycles", "2", "--interval-seconds", "2.5")
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
        assert out.endswith("\x1b[0m\x1b[?25h") and "\x1b[?25l" in out
        assert "\x1b[31m" not in out
    else:
        assert "\x1b" not in out
    assert len(calls) == (2 if ending == "normal" else 1)
    assert out.count("PeerHub") >= (3 if ending == "normal" else 1)


def test_json_on_tty_emits_only_one_object_per_cycle(tmp_path, monkeypatch):
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
    assert _run(_db(tmp_path), "--json", "--schema-version", "1.1", "--cycles", "3", "--collect-every", "2") == 0
    lines = output.getvalue().splitlines()
    frames = [json.loads(line) for line in lines]
    assert len(lines) == 4 and len(refreshes) == 2
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
    assert _run(_db(tmp_path), "--json", "--group", "store", "--cycles", "3", "--collect-every", "2") == 0
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
    assert _run(_db(tmp_path), "--group", "store", "--cycles", "1") in (0, 4)
    assert output.getvalue().endswith("\x1b[0m\x1b[?25h")


def test_redirected_snapshot_keeps_full_human_group_frame(tmp_path, capsys):
    db = _db(tmp_path)
    code = main(["--db", str(db), "monitor", "--snapshot", "--group", "all"])
    output = capsys.readouterr().out

    assert code in (0, 4)
    assert all((group.upper() in output if group != "capacity" else ("QUOTA" in output or "CAPACITY" in output)) for group in
               ("overview", "execution", "sessions", "usage", "context", "capacity", "runtime", "coverage", "store"))
    assert "groups omitted" not in output.lower()
    assert "No scope evidence" in output and "Schema 4" in output
    assert "\x1b" not in output
    assert output.endswith("\n") and not output.endswith("\n\n")


@pytest.mark.skipif(os.name == "nt", reason="POSIX PTY smoke; Windows TTY behavior is covered by ConPTY/manual tests")
def test_posix_pty_snapshot_obeys_physical_viewport(tmp_path):
    import fcntl
    import pty
    import struct
    import subprocess
    import sys
    import termios
    from pathlib import Path

    db = _db(tmp_path)
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 8, 40, 0, 0))
    env = os.environ.copy()
    env.update({"TERM": "xterm", "NO_COLOR": "1", "COLUMNS": "120", "LINES": "60"})
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
    assert "8g hidden" in rendered
    assert "--expand" in rendered
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

    def render(db, groups):
        reads.append((db, tuple(groups)))
        return real_render(db, groups)

    def refresh(*args, **kwargs):
        refreshes.append(1)
        return {"status": "OK", "observations": [], "warnings": []}

    monkeypatch.setattr("peerhub.extensions.monitor_read_model.render_monitor_frame", render)
    monkeypatch.setattr("peerhub.extensions.quota_capture.refresh_quota", refresh)
    monkeypatch.setattr(monitor, "monotonic", lambda: now[0])
    monkeypatch.setattr(monitor, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))

    assert main(["--db", str(_db(tmp_path)), "monitor", "--cycles", "2", "--interval-seconds", "2",
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
        assert _run(_db(tmp_path), "--json", "--schema-version", "1.1", "--cycles", "1") == 0
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
    assert _run(_db(tmp_path), "--json", "--schema-version", "1.1", "--cycles", "1") == expected


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
        assert _run(_db(tmp_path), "--cycles", "2", "--interval-seconds", "5") in (0, 4)
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

    def render(db, groups):
        calls.append("snapshot")
        return render_monitor_frame(db, groups)
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
    calls = []
    sleeps = []
    def fake_refresh(*a, **kw):
        calls.append("refresh")
        return {"status": "OK", "observations": [], "warnings": []}
    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh), \
         patch("peerhub.extensions.monitor_read_model.render_monitor_frame", _spy_group_frame(calls)), \
         patch("peerhub.cli.monitor.sleep", lambda s: sleeps.append(s)):
        assert _run(_db(tmp_path), "--cycles", "3", "--interval-seconds", "42") in (0, 4)
    assert sleeps == pytest.approx([42.0, 42.0], abs=0.2)
    assert calls == ["snapshot", "refresh", "snapshot", "refresh", "snapshot", "refresh", "snapshot"]


def test_collect_every_honoured(tmp_path, capsys):
    calls = []
    def fake_refresh(*a, **kw):
        calls.append("refresh")
        return {"status": "OK", "observations": [], "warnings": []}
    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh), \
         patch("peerhub.extensions.monitor_read_model.render_monitor_frame", _spy_group_frame(calls)), \
         patch("peerhub.cli.monitor.sleep", lambda s: None):
        assert _run(_db(tmp_path), "--cycles", "4", "--collect-every", "2") in (0, 4)
    assert calls == ["snapshot", "refresh", "snapshot", "snapshot", "refresh", "snapshot", "snapshot"]


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
        assert _run(_db(tmp_path), "--cycles", "5") == 1
    assert calls == ["snapshot", "refresh"]
    err = capsys.readouterr().err
    assert "agy /usage consumed model tokens" in err


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
        assert _run(_db(tmp_path), "--cycles", "2", "--allow-agy-token-use") == 4
    assert calls == ["snapshot", "refresh", "snapshot", "refresh", "snapshot"]


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
        assert _run(_db(tmp_path), "--cycles", "2", "--collect-every", "2", "--json", "--schema-version", "1.1") == 0
    
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
    
    def fake_refresh(db, *a, **kw):
        from peerhub.core.store import CoreStore
        from peerhub.extensions.observation import ObservationStore
        CoreStore(db)
        ObservationStore(db)
        return {"status": "OK", "observations": [{"payload": {}}], "warnings": []}

    hashes = []
    def record_hash(*args):
        hashes.append(hashlib.sha256(db_path.read_bytes()).hexdigest())

    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh), \
         patch("peerhub.cli.monitor.sleep", record_hash):
        assert _run(db_path, "--cycles", "3", "--collect-every", "2") in (0, 4)

    # cycle 0 = refresh+snapshot, cycle 1 = snapshot only, cycle 2 = refresh+snapshot: the two sleeps bracket the snapshot-only cycle
    assert len(hashes) == 2
    assert hashes[0] == hashes[1]


def test_monitor_forwards_system_dir(tmp_path):
    from pathlib import Path
    calls = []

    def fake_refresh(db, peers, sys_dir=None, deadline_sec=15.0):
        calls.append(sys_dir)
        return {"status": "OK", "observations": [], "warnings": []}

    sys_path = tmp_path / "custom_sys"
    with patch("peerhub.extensions.quota_capture.refresh_quota", fake_refresh):
        assert _run(_db(tmp_path), "--cycles", "1", "--system-dir", str(sys_path)) in (0, 4)

    assert calls == [Path(sys_path)]


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


def test_monitor_expand_rejects_json(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(SystemExit) as exc:
        main(["--db", str(db), "monitor", "--expand", "--json"])
    assert exc.value.code == 2


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

    # Snapshot default (expand=False): capped at 2 rows
    code = main(["--db", str(db), "monitor", "--snapshot", "--group", "sessions", "--view", "plain"])
    assert code == 0
    out = capsys.readouterr().out
    assert "+2 sessions not shown" in out
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
    clean_out = " ".join(out.split())
    assert "Render all loaded detail rows instead of capping at default limits" in clean_out

