"""Behavioural option proofs for the new public first-party extension commands."""
import json
import io
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from peerhub.cli.arguments import WORKSPACE_OPTIONS
from peerhub.cli.ask_cmd import ASK_QUERY_FILE_OPTIONS
from peerhub.extensions.observation_model import EvidenceState
from peerhub.core.store import CoreStore
from tests.communication.fakes import FakeRuntimeTarget
from tests.communication.e2e.test_cli_suggest import suggest_effect


def lead_isolated_env(tmp, base):
    """`base` for a peerhub run that must not read a real Claude folder: CLAUDE_CONFIG_DIR is an empty temporary
    folder and PEERHUB_LEAD_DIRS is removed (#376 test 18)."""
    empty = Path(tmp) / "lead-claude-config"
    empty.mkdir(exist_ok=True)
    env = {name: value for name, value in base.items() if name.upper() != "PEERHUB_LEAD_DIRS"}
    env["CLAUDE_CONFIG_DIR"] = str(empty)
    return env


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
    elif option in ASK_QUERY_FILE_OPTIONS:
        query = e.tmp / "query.txt"
        query.write_text("한글 prompt\n", encoding="utf-8")
        expected = query.read_text(encoding="utf-8")
        argv = ["ask", "cx", option, str(query), "--json"]
    elif option in WORKSPACE_OPTIONS:
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
        elif option in WORKSPACE_OPTIONS:
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


def ask_dry_run_effect(e):
    db = e.fresh()
    before = db.read_bytes()
    with patch("subprocess.Popen", side_effect=AssertionError("ask --dry-run must not spawn")), \
         patch("socket.socket", side_effect=AssertionError("ask --dry-run must not access the network")):
        preview = e.j(db, "ask", "cx", "hello", "--dry-run", "--request-id", "preview")
    assert preview["store_exists"] and not preview["stream_exists"]
    assert preview["request_id"] == "preview" and preview["session_mode"] == "fresh"
    assert db.read_bytes() == before


def diag_json(e):
    from peerhub.extensions.observation import ObservationStore
    db = e.fresh()
    ObservationStore(db)
    result = e.j(db, "diag", "--json")
    assert result["status"] == "OK" and "peers" in result["sections"]
    assert not e.run(db, "diag")[1].lstrip().startswith("{")


def seed_running_caller(db):
    import time
    from contextlib import closing

    from peerhub.extensions import bridge, bridge_claims, caller
    from tests.communication.diag.test_diag_running import add

    facts = {**caller.detect_caller(), "label": "shared-folder", "owner": "other-lead"}
    generation = db.parent / "workspace.generation"
    with closing(sqlite3.connect(db)) as conn, conn:
        for statement in bridge._DDL:  # pyright: ignore[reportPrivateUsage]
            conn.execute(statement)
        conn.execute(bridge_claims._DDL)  # pyright: ignore[reportPrivateUsage]
    add(db, 1, claim_expires=time.time() + 60, workspace_generation=(
        generation.read_text(encoding="utf-8") if generation.is_file() else "effect-proof"),
        evidence_rows=[("caller", "NOT_STARTED", json.dumps(facts))])
    return facts


def diag_plan(e):
    from peerhub.cli.view import PLAN_BUDGET_COLUMN
    from peerhub.core.load_limits import format_active_split
    from peerhub.extensions.caller import OWNER_ENV

    db = e.fresh()
    seed_view_pool(db)
    seed_running_caller(db)
    before = db.read_bytes()
    # --plan replaces the dashboard with the per-pool action table, read from stored observations only.
    with patch.dict(os.environ, {OWNER_ENV: "current-lead", "COLUMNS": "180"}), \
         patch("subprocess.Popen", side_effect=AssertionError("diag --plan must not spawn providers")):
        result = e.j(db, "diag", "--plan", "--json")
        code, table, err = e.run(db, "diag", "--plan")
        dash = e.j(db, "diag", "--json")
    assert result["schema_version"] == "1.0" and [p["pool"] for p in result["pools"]] == ["view-5H"]
    assert result["pools"][0]["action"] in {"spend", "hold", "rest", "blocked"} and "pools" not in dash
    counts = result["load"]["active_by_relation"]
    assert result["load"]["active"] == counts["other"] == 1
    assert format_active_split(counts).strip() in table
    assert code == 0 and not err and PLAN_BUDGET_COLUMN in table and PLAN_BUDGET_COLUMN not in e.run(db, "diag")[1]
    assert db.read_bytes() == before


def diag_plan_filter_effect(e, option):
    from peerhub.extensions.observation import ObservationStore
    from tests.communication.e2e.test_cli_diag_quota import seed

    db = e.fresh()
    seed_view_pool(db)
    baseline = e.j(db, "diag", "--plan")
    if option == "--peer":
        result = e.j(db, "diag", "--plan", option, baseline["pools"][0]["peer"] + "-other")
        assert baseline["pools"] and result["pools"] == []
        return

    alternate = e.fresh()
    ObservationStore(alternate)
    pool, peer, remaining = "alternate-5H", "alternate-peer", 0.25
    seed(alternate, pools=[(pool, "QUOTA")], rows=[
        (peer, "quota", pool, "MEASURED", 5, {"remaining_fraction": remaining}, "alternate-fixture"),
    ])
    before = (db.read_bytes(), alternate.read_bytes())
    result = e.j(db, "diag", "--plan", option, str(alternate))
    assert result["store_selection"] == baseline["store_selection"]
    assert [(row["peer"], row["pool"], row["left_pct"]) for row in result["pools"]] == [
        (peer, pool, remaining * 100),
    ]
    assert result["pools"] != baseline["pools"]
    assert (db.read_bytes(), alternate.read_bytes()) == before


def diag_quota_age_effect(e):
    db = e.fresh()
    seed_view_pool(db)
    baseline = e.j(db, "diag", "quota")
    items = [item for group in baseline["pools"] for item in group["items"]]
    cutoff = min(int(item["age_seconds"]) for item in items) - 1
    result = e.j(db, "diag", "quota", "--max-age-seconds", str(cutoff))
    assert result["pools"] == [] and result["hidden_count"] == len(items)


def readiness_json(e):
    from peerhub.extensions.observation import ObservationStore

    db = e.fresh()
    ObservationStore(db)
    before = db.read_bytes()
    # The command reads cached facts; --json changes its representation.
    with patch("subprocess.Popen", side_effect=AssertionError("readiness must not spawn providers")):
        result = e.j(db, "diag", "readiness", "--json")
        code, table, err = e.run(db, "diag", "readiness")
    assert result["schema_version"] == "1.0" and result["basis"] == "cached" and result["status"] == "OK"
    assert result["profiles"] and all(p["state"] == "UNKNOWN" for p in result["profiles"])
    assert code == 0 and not err and "PROFILE POOLS STATE LAST NEXT" in table
    assert not table.lstrip().startswith("{") and db.read_bytes() == before
    missing = e.tmp / "missing-readiness" / "core.db"
    assert e.j(missing, "diag", "readiness", "--json", code=5)["status"] == "UNAVAILABLE"
    assert not missing.parent.exists()


def running_json(e):
    from peerhub.extensions.caller import OWNER_ENV

    db = e.fresh()
    before = db.read_bytes()
    # The command reads stored lease facts; --json changes its representation.
    with patch("subprocess.Popen", side_effect=AssertionError("diag running must not spawn providers")):
        result = e.j(db, "diag", "running", "--json")
        code, table, err = e.run(db, "diag", "running")
    assert result["schema_version"] == "1.0" and result["status"] == "OK" and result["rows"] == [] and result["truncated"] is False
    assert code == 0 and not err and table.rstrip().endswith("(none)")
    assert not table.lstrip().startswith("{") and db.read_bytes() == before
    facts = seed_running_caller(db)
    before = db.read_bytes()
    with patch.dict(os.environ, {OWNER_ENV: "current-lead", "COLUMNS": "180"}):
        (row,) = e.j(db, "diag", "running")["rows"]
        code, table, err = e.run(db, "diag", "running")
    assert row["relation"] == "other" and row["caller"]["label"] == facts["label"]
    assert code == 0 and not err and f"label={facts['label']}" in table and row["relation"] in table
    assert db.read_bytes() == before
    missing = e.tmp / "missing-running" / "core.db"
    assert e.run(missing, "diag", "running", "--json")[0] == 5
    assert not missing.parent.exists()


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


def assert_view_glyph_effect(rich, plain, *, monitor=False):
    assert rich != plain
    if monitor:
        from peerhub.cli.monitor_layout import RICH_GLYPHS

        rules = tuple(f"{glyphs['rule'] * 2} POOLS " for glyphs in RICH_GLYPHS.values())
        assert any(line.startswith(rules) for line in rich.splitlines())
        assert any(line.startswith("POOLS ") for line in plain.splitlines())
        assert "\x1b" not in rich and "\x1b" not in plain
        return
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
            args = (["--cycles", "1", "--no-probe"] if command == "monitor" else ["--live", "--cycles", "1"])
            with patch("sys.stdout", output), patch("peerhub._console.enable_vt", lambda: vt), \
                    patch.dict("os.environ", {"TERM": "xterm"}):
                assert e.run(db, command, "--view", style, *args)[0] in (0, 4)
            text = output.getvalue()
            if vt:
                from peerhub._console import NO_AUTOWRAP
                wrap = NO_AUTOWRAP if command == "monitor" else ""  # row-anchored monitor frames clip, never wrap
                assert "\x1b[?25l" + wrap + "\x1b[H\x1b[2J" in text
                trailer = "\x1b[0m\x1b[?25h"
                if command == "monitor":
                    trailer += "\r\n"
                    assert "\x1b[?1049h" not in text and "\x1b[?1049l" not in text
                assert text.endswith(trailer)
            else:
                assert "\x1b" not in text
            assert " [H" not in text


def refresh_effect(e, option):
    db = e.fresh()
    captured = []
    agy_probes = []
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
    elif option == "--timeout-seconds":
        assert captured[0]["deadline_sec"] == 2
    else:
        assert captured[0]["deadline_sec"] == 15


def monitor_effect(e, option):
    db = e.fresh()
    if option == "--view":
        seed_view_pool(db)
        # Seed a binding 5-hour row so both styles have the same POOLS data to render.
        from tests.communication.e2e.test_cli_diag_quota import seed
        seed(db, pools=[("G-7D", "QUOTA"), ("G-5H", "QUOTA")], rows=[
            ("view-peer", "quota", "G-7D", "MEASURED", 6, {"remaining_fraction": 0.9}, "view-fixture"),
            ("view-peer", "quota", "G-5H", "MEASURED", 7, {"remaining_fraction": 0.2}, "view-fixture")])
    captured = []
    agy_probes = []
    def probe(**kw):
        captured.append(kw)
        from peerhub.extensions.observation_model import EvidenceState
        from types import SimpleNamespace
        ev = SimpleNamespace(state=EvidenceState.ABSENT, value=None, evidence_ref="test", source_tag="fake",
                             observed_at=100, captured_at=100)
        return [SimpleNamespace(evidence=ev)]
    def probe_agy(**kw):
        agy_probes.append(kw)
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
    elif option == "--schema-version":
        argv += ["--json", option, "1.1"]
    elif option == "--view":
        argv += [option, "rich"]
    argv.append("--probe")
    if option not in ("--view", "--schema-version"):
        # These option effects predate the grouped read model and concern the
        # collection loop itself. Keep the projection-independent 1.1 wire path
        # explicit; grouped human rendering has separate contract tests.
        argv[1:1] = ["--json", "--schema-version", "1.1"]
    sleeps = []
    fake_now = [0.0]

    def fake_sleep(seconds):
        sleeps.append(seconds)
        fake_now[0] += seconds

    with patch("peerhub.extensions.quota_probes.poll_claude_usage", probe), \
         patch("peerhub.extensions.quota_probes.poll_codex_usage", probe), \
         patch("peerhub.extensions.quota_probes.poll_agy_usage", probe_agy if option == "--allow-agy-token-use" else probe), \
         patch("peerhub.cli.monitor.monotonic", lambda: fake_now[0]), \
         patch("peerhub.cli.monitor.sleep", fake_sleep):
        code, out, err = e.run(db, *argv)
        if option == "--view":
            plain_code, plain, plain_err = e.run(db, "monitor", "--probe", "--cycles", "1", "--view", "plain")
            assert_vt_view_effect(e, db, "monitor")
    if option == "--view":
        assert code in (0, 4) and plain_code in (0, 4) and "refresh:" in err and "refresh:" in plain_err
        assert_view_glyph_effect(out, plain, monitor=True)
    elif option == "--interval-seconds":
        import pytest
        assert sleeps == pytest.approx([3.14], abs=0.1)
    elif option == "--cycles": assert len(sleeps) == 1
    elif option == "--peers": assert captured[0]["instance_id"] == "cx"
    elif option == "--system-dir":
        assert code in (0, 4) and captured
        assert all(call["sys_dir"] == e.tmp for call in captured)
    elif option == "--timeout-seconds": assert captured[0]["deadline_sec"] == 2
    elif option == "--collect-every": assert len(captured) == 3
    elif option == "--allow-agy-token-use":
        assert code in (1, 4) and len(agy_probes) == 2
        assert "stopping watch mode" not in err
    elif option == "--json": import json; assert "cycle" in json.loads(out.splitlines()[0])
    elif option == "--schema-version":
        import json
        assert json.loads(out.splitlines()[0])["schema_version"] == "1.1"
    elif option == "--probe":
        assert len(captured) == 3


def monitor_management_effect(e, argv, *, needs_bridge=False):
    import os
    from pathlib import Path
    from tests.communication.bridge_helpers import bseed
    from tests.communication.harness.bridge import BridgeHarness

    db = e.tmp / "monitor-management" / "core.db"
    env_dir = e.tmp / "monitor-config-home"
    environ = lead_isolated_env(e.tmp, {**os.environ, "PEERHUB_CONFIG_HOME": str(env_dir)})
    with patch.dict(os.environ, environ, clear=True), \
         patch("pathlib.Path.cwd", return_value=e.tmp):
        if needs_bridge:
            from tests.communication.bridge.test_retained_sessions import _ProviderTelemetryRuntime
            harness = BridgeHarness(db.parent)
            bseed(harness)
            harness.delivery_cycle("b", "s", _ProviderTelemetryRuntime())
        code, out, _ = e.run(db, *argv)
        assert code == 0
        if "--json" in argv:
            payload = json.loads(out)
            assert payload["status"] in ("OK", "PARTIAL")
        if not needs_bridge:
            assert not db.exists()


def monitor_groups_effect(e, _option):
    monitor_management_effect(e, ["monitor", "groups", "--json"])


def monitor_defaults_show_effect(e, _option):
    monitor_management_effect(e, ["monitor", "defaults", "show", "--json"])


def monitor_defaults_set_effect(e, option):
    args = ["monitor", "defaults", "set", "--group", "usage", "sessions", "--json"]
    if option == "--global":
        args.insert(3, "--global")
    monitor_management_effect(e, args)


def monitor_defaults_reset_effect(e, option):
    args = ["monitor", "defaults", "reset", "--json"]
    if option == "--global":
        args.insert(3, "--global")
    monitor_management_effect(e, args)


def monitor_snapshot_effect(e, option):
    monitor_management_effect(e, ["monitor", "--snapshot", "--group", "usage", "--json"], needs_bridge=True)


def monitor_no_probe_effect(e):
    db = e.fresh()
    argv = ["monitor", "--cycles", "1", "--group", "store", "--json"]
    with patch.dict(os.environ), patch("sys.stdout.isatty", return_value=True), \
         patch("peerhub.cli.monitor.refresh_monitor_quota", return_value=None) as refresh:
        os.environ.pop("PEERHUB_MONITOR_PROBE", None)
        code, baseline, _ = e.run(db, *argv)
        assert code == 0 and json.loads(baseline.splitlines()[-1])["phase"] == "complete"
        refresh.assert_called_once()
        code, selected, _ = e.run(db, *argv, "--no-probe")
        assert code == 0 and json.loads(selected.splitlines()[-1])["phase"] == "complete"
        refresh.assert_called_once()


def monitor_display_effect(e, option):
    from tests.communication.diag.test_monitor_plain_golden import full_payload

    db = e.fresh()
    before = db.read_bytes()
    args = ["monitor", "--snapshot", "--view", "plain"]
    payload = full_payload()
    if option == "--show-quota-notes":
        args.append("--expand")
        peers = payload["groups"]["peers"]["data"]["peers"]
        observations = payload["groups"]["capacity"]["data"]["observations"]
        weekly = next(item for item in observations if item["pool_ref"] == "quota:cx:X-7D")
        next(peer for peer in peers if peer["peer_id"] == "cx")["quota"] = {
            "pool_ref": weekly["pool_ref"], "state": weekly["state"],
            "remaining_fraction": weekly["measurements"]["remaining_fraction"],
        }
    with patch("peerhub.extensions.monitor_read_model.render_monitor_frame", return_value=payload):
        code, baseline, _ = e.run(db, *args)
        assert code == 0
        code, selected, _ = e.run(db, *args, option)
        assert code == 0
        if option == "--compact":
            assert selected == baseline
            assert e.run(db, *args, "--expand")[1] != selected
        else:
            # Expanded QUOTA explains unavailable windows by default. The flag adds the PEERS note.
            note = "plan prolite: no 30d window"
            assert note not in baseline and note in selected
    assert db.read_bytes() == before


def monitor_repair_effect(e, option):
    args = ["monitor", "repair", "--json"]
    if option == "--chunk-size":
        args += ["--chunk-size", "10"]
    monitor_management_effect(e, args, needs_bridge=True)


def profile_effect(e, command, option):
    workspace = e.tmp / "profiles-workspace"
    workspace.mkdir(exist_ok=True)
    argv = ["profile", command]
    if command == "show":
        argv.append("cx.standard")
    if option in WORKSPACE_OPTIONS:
        argv += [option, str(workspace)]
    elif option == "--format":
        argv += [option, "table"]
    code, out, _ = e.run(e.tmp / "unused-profile.db", *argv, *([] if option == "--format" else ["--json"]))
    assert code == 0 and not (e.tmp / "unused-profile.db").exists()
    if option == "--format":
        assert "PROFILE" in out if command == "list" else "FIELD" in out
    elif option == "profile":
        import json
        assert json.loads(out)["profile"] == "cx.standard"
    else:
        import json
        result = json.loads(out)
        assert result["schema_version"] == "1.0"
        if option in WORKSPACE_OPTIONS:
            assert result["workspace"] == str(workspace)


def session_inspection_effect(e, command, option):
    db = e.fresh(False)

    def target(*args, **kwargs):
        runtime = FakeRuntimeTarget()
        runtime.script_deliver(("started", "exec"), ("terminal", {"response": "ok"}))
        return runtime

    with patch("peerhub.extensions.ask.CliRuntimeTarget", target):
        for index in range(2):
            code, _, _ = e.run(db, "ask", "cx", f"prompt {index}", "--fresh", "--json")
            assert code == 0
    if command == "show":
        sessions = json.loads(e.run(db, "session", "list", "--json")[1])["items"]
        argv = ["session", "show", sessions[0]["handle"]]
    else:
        argv = ["session", "list"]
    if option == "--stream":
        argv += [option, "peer:cx:chat"]
    elif option == "--peer":
        argv += [option, "cx"]
    elif option == "--limit":
        argv += [option, "1"]
    elif option == "--cursor":
        first = json.loads(e.run(db, "session", "list", "--limit", "1", "--json")[1])
        argv += ["--limit", "1", option, first["next_cursor"]]
    elif option == "--format":
        argv += [option, "table"]
    code, out, _ = e.run(db, *argv, *([] if option == "--format" else ["--json"]))
    assert code == 0
    if option == "--format":
        assert "HANDLE" in out if command == "list" else "handle:" in out
    else:
        result = json.loads(out)
        rows = result.get("items", [])
        if option == "--stream":
            assert all(row["stream_id"] == "peer:cx:chat" for row in rows)
        elif option == "--peer":
            assert all(row["peer_id"] == "cx" for row in rows)
        elif option == "--limit":
            assert len(rows) == 1 and result["next_cursor"]
        elif option == "--cursor":
            assert len(rows) == 1 and rows[0]["generation"] < 2
        elif command == "list":
            assert len(rows) == 2 and all(row["peer_id"] == "cx" for row in rows)
        else:
            assert result["session"]["handle"] == argv[2]

def account_effect(e, action, option):
    db = e.fresh(False)
    home, root, other = e.tmp / "account-home", e.tmp / "account-root", e.tmp / "other-account-root"
    for directory in (home, root, other):
        directory.mkdir(exist_ok=True)
    isolated = {name: value for name, value in os.environ.items()
                if name.upper() not in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "PEERHUB_LEAD_DIRS")}  # ~/.claude is below `home`
    isolated.update(HOME=str(home), USERPROFILE=str(home))
    base = ["account", "bind", "--peer", "worker", "--provider", "cc", "--alias", "work", "--root", str(root), "--json"]
    with patch.dict(os.environ, isolated, clear=True), patch.object(Path, "home", return_value=home):
        if action == "bind":
            if option != "--json":
                base[base.index(option) + 1] = {"--peer": "worker-other", "--provider": "cx",
                                               "--alias": "other-label", "--root": str(other)}[option]
            result = e.j(db, *base)
            expected = {"--peer": ("peer_id", "worker-other"), "--provider": ("provider", "cx"),
                        "--alias": ("alias", "other-label")}
            if option in expected:
                field, value = expected[option]
                assert result[field] == value
            elif option == "--root":
                with sqlite3.connect(db) as conn:
                    assert conn.execute("SELECT config_root FROM account_bindings").fetchone()[0] == str(other)
            else:
                assert result["peer_id"] == "worker" and "config_root" not in result
                assert CoreStore(db).get_peer("worker").metadata == {"account_bound": True}
            return
        e.j(db, *base)
        argv = ["account", action] + (["--peer", "worker"] if action == "show" else [])
        if option == "--json":
            result = e.j(db, *argv, "--json")
            row = result[0] if action == "list" else result
            assert row["peer_id"] == "worker" and "config_root" not in row
            code, out, _ = e.run(db, *argv)
            assert code == 0 and "PEER PROVIDER ALIAS SLOT_ID" in out
        elif option == "--show-root":
            hidden = e.j(db, *argv, "--json")
            visible = e.j(db, *argv, "--json", "--show-root")
            assert "config_root" not in (hidden[0] if action == "list" else hidden)
            assert (visible[0] if action == "list" else visible)["config_root"] == str(root)
        else:
            base[base.index("--peer") + 1] = "worker-other"
            base[base.index("--root") + 1] = str(other)
            e.j(db, *base)
            argv[argv.index("--peer") + 1] = "worker-other"
            assert e.j(db, *argv, "--json")["peer_id"] == "worker-other"


def batch_effect(e, option):
    from peerhub.extensions import batch

    path = e.tmp / "batch.json"
    path.write_text(json.dumps({"tasks": [{"task_id": "a", "peer": "cx", "prompt": "review"}]}), encoding="utf-8")
    db = e.tmp / "unused-batch.db"
    argv = ["batch", "run", str(path), "--batch-id", "proof", "--max-parallel", "2", "--dry-run", "--json"]
    if option == "manifest":
        argv[2] = str(e.tmp / "absent.json")
        code, out, err = e.run(db, *argv)
        assert code == 1 and json.loads(out)["error"]["code"] == "invalid_manifest" and "[invalid_manifest]" in err
    elif option == "--json":
        code, out, err = e.run(db, *argv)
        assert code == 0 and json.loads(out)["dry_run"] and not err
        argv.remove("--json")
        code, out, err = e.run(db, *argv)
        assert code == 0 and "TASK PEER STREAM REQUEST_ID WRITABLE" in out and not err
    elif option == "--dry-run":
        def forbidden(*args, **kwargs):
            raise AssertionError("dry run executed")

        with patch.object(batch, "run_batch", forbidden):
            result = e.j(db, *argv)
        assert result["dry_run"] is True
        assert result["load"]["active_by_relation"] == dict.fromkeys(("mine", "other", "unknown"))
    elif option == "--responses":  # a dry run ran nothing, so it has no replies to show
        code, out, err = e.run(db, *argv, "--responses")
        assert code == 0 and json.loads(out)["dry_run"] and "response" not in out and not err
    else:
        first = e.j(db, *argv)
        argv[argv.index(option) + 1] = "other" if option == "--batch-id" else "3"
        second = e.j(db, *argv)
        if option == "--batch-id":
            assert first["batch_id"] != second["batch_id"]
            assert first["tasks"][0]["stream"] != second["tasks"][0]["stream"]
            assert first["tasks"][0]["request_id"] != second["tasks"][0]["request_id"]
        else:
            assert first["max_parallel"] == 2 and second["max_parallel"] == 3
    assert not db.exists()


def explain_effect(e, option):
    db = e.tmp / "unused-explain.db"
    if option == "--json":
        code, out, err = e.run(db, "explain", "ask_uncertain", "--json")
        assert code == 0 and not err and not db.exists()
        payload = json.loads(out)
        assert payload["code"] == "ask_uncertain" and "meaning" in payload
    else:
        from peerhub.cli.lessons import lesson_by_id, render_lesson

        topic = "caller-ownership"
        code1, out1, err1 = e.run(db, "explain", "ask_uncertain")
        code2, out2, err2 = e.run(db, "explain", topic)
        assert code1 == code2 == 0 and not err1 and not err2 and not db.exists()
        assert "ask_uncertain" in out1 and out1 != out2
        assert out2.strip() == render_lesson(lesson_by_id(topic)[0])


EFFECT = {f"ask {option}": (lambda e, option=option: ask_effect(e, option)) for option in (
    "peer", "prompt", *ASK_QUERY_FILE_OPTIONS, "--stream", "--request-id", "--author-peer", *WORKSPACE_OPTIONS, "--model",
    "--effort", "-p", "--profile", "--writable", "--timeout-seconds", "--silence-timeout-seconds",
    "--max-output-bytes", "--json")}
EFFECT.update({f"ask {option}": (lambda e, option=option: session_selector_effect(e, option))
               for option in ("--fresh", "--session")})
EFFECT.update({f"explain {option}": (lambda e, option=option: explain_effect(e, option))
               for option in ("code", "--json")})
EFFECT.update({f"batch run {option}": (lambda e, option=option: batch_effect(e, option))
               for option in ("manifest", "--batch-id", "--max-parallel", "--dry-run", "--json", "--responses")})
EFFECT.update({f"account {action} {option}": (lambda e, action=action, option=option: account_effect(e, action, option))
               for action, options in (("bind", ("--peer", "--provider", "--alias", "--root", "--json")),
                                       ("list", ("--json", "--show-root")),
                                       ("show", ("--peer", "--json", "--show-root"))) for option in options})
EFFECT["ask --dry-run"] = ask_dry_run_effect
EFFECT["diag --json"] = diag_json
EFFECT["diag --plan"] = diag_plan
EFFECT.update({f"diag {option}": (lambda e, option=option: diag_plan_filter_effect(e, option))
               for option in ("--peer", "--observation-db")})
EFFECT["diag quota --max-age-seconds"] = diag_quota_age_effect
EFFECT["diag readiness --json"] = readiness_json
EFFECT["diag running --json"] = running_json
EFFECT.update({f"diag {option}": (lambda e, option=option: diag_live(e, option))
               for option in ("--live", "--interval-seconds", "--cycles", "--view")})
EFFECT.update({f"observation refresh {option}": (lambda e, option=option: refresh_effect(e, option))
               for option in ("--peers", "--system-dir", "--timeout-seconds")})
EFFECT.update({f"monitor {option}": (lambda e, option=option: monitor_effect(e, option))
               for option in ("--interval-seconds", "--cycles", "--peers", "--system-dir", "--timeout-seconds", "--json", "--schema-version", "--allow-agy-token-use", "--collect-every", "--view", "--probe")})
EFFECT.update({f"suggest {option}": (lambda e, option=option: suggest_effect(e, option))
               for option in ("--tier", "--task", "--json", "--candidates")})
EFFECT.update({"monitor groups --json": lambda e: monitor_management_effect(e, ["monitor", "groups", "--json"]),
               "monitor defaults show --json": lambda e: monitor_management_effect(e, ["monitor", "defaults", "show", "--json"]),
               "monitor defaults set --global": lambda e: monitor_management_effect(e, ["monitor", "defaults", "set", "--global", "--group", "usage"]),
               "monitor defaults set --group": lambda e: monitor_management_effect(e, ["monitor", "defaults", "set", "--global", "--group", "usage"]),
               "monitor defaults set --json": lambda e: monitor_management_effect(e, ["monitor", "defaults", "set", "--global", "--group", "usage", "--json"]),
               "monitor defaults reset --global": lambda e: monitor_management_effect(e, ["monitor", "defaults", "reset", "--global"]),
               "monitor defaults reset --json": lambda e: monitor_management_effect(e, ["monitor", "defaults", "reset", "--global", "--json"]),
               "monitor --snapshot": lambda e: monitor_management_effect(e, ["monitor", "--snapshot", "--group", "usage", "--json"], needs_bridge=True),
               "monitor --no-probe": monitor_no_probe_effect,
               "monitor --group": lambda e: monitor_management_effect(e, ["monitor", "--snapshot", "--group", "usage", "--json"], needs_bridge=True),
               "monitor --expand": lambda e: monitor_management_effect(e, ["monitor", "--snapshot", "--expand", "--group", "usage", "--view", "plain"], needs_bridge=True),
               "monitor --compact": lambda e: monitor_display_effect(e, "--compact"),
               "monitor --show-quota-notes": lambda e: monitor_display_effect(e, "--show-quota-notes"),
               "monitor repair --chunk-size": lambda e: monitor_management_effect(e, ["monitor", "repair", "--chunk-size", "1", "--json"], needs_bridge=True),
               "monitor repair --json": lambda e: monitor_management_effect(e, ["monitor", "repair", "--json"], needs_bridge=True)})
EFFECT.update({f"profile {command} {option}": (lambda e, command=command, option=option: profile_effect(e, command, option))
               for command in ("list", "show") for option in (*WORKSPACE_OPTIONS, "--format")})
EFFECT["profile show profile"] = lambda e: profile_effect(e, "show", "profile")


def profile_store_workspace_effect(e, command, option):
    workspace = e.tmp / f"profiles-{command}"
    workspace.mkdir(exist_ok=True)
    result = e.j(e.fresh(), "profile", command, option, str(workspace), "--json")
    assert result["workspace"] == str(workspace)


def running_option_effect(e, option):
    value = {"--peer": "cx", "--limit": "5"}[option]
    result = e.j(e.fresh(), "diag", "running", option, value, "--json")
    assert result[option[2:]] == (value if option == "--peer" else int(value)) and result["rows"] == []


EFFECT.update({f"profile {command} {option}": (lambda e, command=command, option=option: profile_store_workspace_effect(e, command, option))
               for command in ("stats", "check") for option in WORKSPACE_OPTIONS})
EFFECT.update({"diag running --peer": lambda e: running_option_effect(e, "--peer"),
               "diag running --limit": lambda e: running_option_effect(e, "--limit")})
EFFECT.update({f"session list {option}": (lambda e, option=option: session_inspection_effect(e, "list", option))
               for option in ("--stream", "--peer", "--limit", "--cursor", "--format")})
EFFECT.update({"session show handle": lambda e: session_inspection_effect(e, "show", "handle"),
               "session show --format": lambda e: session_inspection_effect(e, "show", "--format")})
