"""Offline coverage of the monitor header, local links and activity cadence."""
from __future__ import annotations

import argparse
import re
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args

import pytest

from peerhub import __version__
from peerhub import _console
from peerhub.cli import monitor, monitor_layout, monitor_links, monitor_refresh, monitor_view
from peerhub.extensions import monitor_alerts, monitor_quota
from peerhub.extensions import monitor_read_model
from peerhub.extensions.monitor_queries import ACTIVITY_SCOPE_CAP
from peerhub.extensions.pool_health import PoolState
from tests.communication.diag.test_monitor_activity_view import NOW, ask, pool
from tests.communication.diag.test_monitor_plain_golden import _payload
from tests.communication.diag.test_monitor_pool_health import frame, reading
from tests.communication.diag.test_monitor_tree import frame_for


@pytest.mark.parametrize("expand", [False, True], ids=["compact", "expand"])
@pytest.mark.parametrize("view", ["plain", "rich"])
@pytest.mark.parametrize("with_evidence", [False, True], ids=["unknown", "informative"])
def test_billing_header_shows_only_informative_peers(expand: bool, view: str, with_evidence: bool) -> None:
    payload = _payload([], [pool("cc", "C-7D", .63)], _store_note="Store selection note")
    peers = payload["groups"]["peers"]["data"]["peers"]
    peers.append({"peer_id": "unknown", "billing": {"status": "OK", "observations": []}})
    if with_evidence:
        peers.extend([
            {"peer_id": "oa", "billing": {"status": "OK", "observations": [{"kind": "billing_balance"}]}},
            {"peer_id": "ca", "billing": {"status": "OK", "observations": [
                {"kind": "billing_usage", "status": "unreconciled"}]}},
            {"peer_id": "ga", "billing": {"status": "ERROR", "observations": []}},
        ])
    lines = monitor_view.render_grouped_frame(payload, expand=expand, view=view, width=118).splitlines()
    header_end = next(i for i, line in enumerate(lines) if "POOLS " in line)
    billing = [line for line in lines[:header_end] if " billing " in line]
    assert "billing unknown" not in "\n".join(lines)
    if with_evidence:
        assert billing == [lines[3 if expand else 2]]
        assert billing[0].replace(" · ", " | ") == "oa billing evidence | ca billing unreconciled | ga billing read error"
        if expand:
            assert lines[2] == "DB:env (/w/.peerhub/core.db)"
    else:
        assert billing == []

DOC_URL = "https://example.test/docs/monitor.md"
OSC_LINK = re.compile(r"\x1b\]8;;([^\x1b]+)\x1b\\([^\x1b]+)\x1b\]8;;\x1b\\")
MONITOR_DOC = Path(__file__).resolve().parents[3] / monitor_links.GUIDE_DOC


@pytest.fixture(autouse=True)
def _link_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("PEERHUB_NO_LINKS", raising=False)
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setenv("PEERHUB_GUIDE_URL", DOC_URL)


@pytest.mark.parametrize("width", [80, 100])
@pytest.mark.parametrize("expand", [False, True])
@pytest.mark.parametrize("style", ["rich", "plain"])
@pytest.mark.parametrize("live", [False, True])
def test_title_reserves_guide_at_right_edge(width: int, expand: bool, style: str, live: bool) -> None:
    payload = _payload([], [])
    payload["store_selection"] = {"source": "workspace-discovered-with-a-long-source-name"}
    payload["min_quota"] = {"left_pct": 68, "pool": "C-7D", "state": "ok"}
    if live:
        payload["_live_refresh"] = {"heartbeat": 0, "failed_ago": 180, "retry_in": 45, "next_in": 65, "age": 959}
    options = {"view": style, "unicode": style == "rich", "width": width, "expand": expand, "tty": True}
    base = monitor_view.render_grouped_frame(payload, **options).splitlines()
    linked = monitor_view.render_grouped_frame(payload, links=True, **options).splitlines()
    if style == "plain":
        assert linked == base and all("\x1b" not in line for line in linked)
        return
    assert _console.ANSI_ESCAPE.sub("", linked[0]) == base[0]
    assert OSC_LINK.findall(linked[1]) == [(DOC_URL, monitor_links.GUIDE_TEXT)]
    assert _console.visible_len(linked[1]) == width - 1
    assert ("refresh failed" if live else "PeerHub Monitor") in linked[1]
    assert linked[2:] == base[2:len(linked)]


@pytest.mark.parametrize("expand", [False, True])
@pytest.mark.parametrize("style", ["rich", "plain"])
def test_existing_db_path_has_a_link_with_unchanged_visible_text(monkeypatch: pytest.MonkeyPatch,
                                                            capsys: pytest.CaptureFixture[str], expand: bool,
                                                            style: str) -> None:
    db = Path(__file__).resolve()
    payload = {"groups": {}, "store_selection": {"path": str(db), "source": "env"}}
    monkeypatch.setattr(_console.FrameOutput, "geometry", staticmethod(lambda: (80, 50)))
    monkeypatch.setattr(_console.FrameOutput, "width", staticmethod(lambda: 80))
    monkeypatch.setattr(_console, "enable_vt", lambda: True)

    def snapshot(*, tty: bool, disabled: bool = False) -> str:
        monkeypatch.setattr("sys.stdout.isatty", lambda: tty)
        monkeypatch.setenv("PEERHUB_NO_LINKS", "1" if disabled else "0")
        monitor._print_grouped_snapshot(payload, style, expand)
        return capsys.readouterr().out

    linked = snapshot(tty=True)
    disabled = snapshot(tty=True, disabled=True)
    redirected = snapshot(tty=False)
    assert "\x1b]8" not in disabled and "\x1b]8" not in redirected
    if style == "plain":
        assert linked == disabled and "\x1b" not in linked
        return
    assert any(url == db.as_uri() for url, _ in OSC_LINK.findall(linked))
    linked_path_lines = [_console.ANSI_ESCAPE.sub("", line) for line in linked.splitlines() if db.as_uri() in line]
    assert linked_path_lines and all(line in disabled.splitlines() for line in linked_path_lines)
    assert all(_console.visible_len(line) < 80 for line in linked_path_lines)
    missing = db.with_name("missing.db")
    payload["store_selection"]["path"] = str(missing)
    assert all(url != missing.as_uri() for url, _ in OSC_LINK.findall(snapshot(tty=True)))


@pytest.mark.parametrize("expand", [False, True])
def test_header_quota_state_links_to_its_documented_vocabulary(expand: bool) -> None:
    payload = _payload([], [])
    payload["min_quota"] = {"left_pct": 68, "pool": "C-7D", "state": "ok"}
    linked = monitor_view.render_grouped_frame(payload, links=True, expand=expand)
    [(url, label)] = OSC_LINK.findall(linked.splitlines()[0])
    base, anchor = url.split("#")
    assert (base, label) == (monitor_links.guide_link_url({}), payload["min_quota"]["state"])
    assert f'id="{anchor}"' in MONITOR_DOC.read_text(encoding="utf-8")


def test_every_pool_state_link_targets_an_anchor_in_the_monitor_doc() -> None:
    pool, state = "G-7D", "ok"
    display = frame(reading("ag", pool, .5, 0))
    screen = monitor_view.render_grouped_frame(display, links=True, expand=True)
    [(url, _), *_] = [(url, label) for url, label in OSC_LINK.findall(screen) if label == state]
    base = url.split("#")[0]
    assert base.endswith(f"/blob/v{__version__}/{monitor_links.GUIDE_DOC}")
    doc = MONITOR_DOC.read_text(encoding="utf-8")
    anchors = set()
    for state in get_args(PoolState):
        [(url, label)] = OSC_LINK.findall(monitor_links.link_pool_state(base, state))
        target, anchor = url.split("#")
        assert target == base and label == state and f'id="{anchor}"' in doc
        anchors.add(anchor)
    assert len(anchors) == 9


@pytest.mark.parametrize("expand", [False, True])
@pytest.mark.parametrize("unicode", [False, True])
@pytest.mark.parametrize("opt_out", ["PEERHUB_NO_LINKS", "NO_COLOR"])
def test_pool_state_links_preserve_visible_table_cells(monkeypatch: pytest.MonkeyPatch,
                                                      expand: bool, unicode: bool, opt_out: str) -> None:
    pool, state = "G-7D", "ok"
    data = frame(reading("ag", pool, .5, 0))
    options = {"view": "rich", "width": 100, "expand": expand, "unicode": unicode, "tty": True}
    linked = monitor_view.render_grouped_frame(data, links=True, **options)
    monkeypatch.setenv(opt_out, "1")
    disabled = monitor_view.render_grouped_frame(data, links=True, **options)
    linked_rows = [line for line in linked.splitlines() if f"{pool}  " in line]
    disabled_rows = [line for line in disabled.splitlines() if f"{pool}  " in line]
    assert len(linked_rows) == (2 if expand else 1)
    base = monitor_links.guide_link_url({})
    assert base is not None
    target = monitor_links.link_pool_state(base, state)
    assert all(target in line for line in linked_rows)
    assert [_console.ANSI_ESCAPE.sub("", line) for line in linked_rows] == disabled_rows
    assert "\x1b]8" not in disabled and f"Guide: {DOC_URL}" in disabled


@pytest.mark.parametrize("expand", [False, True])
def test_displayed_profile_links_to_its_config_file(monkeypatch: pytest.MonkeyPatch, expand: bool) -> None:
    profile = "cc.effort"
    config = Path(__file__).resolve().parents[3] / "peerhub" / "config_data" / "model-defaults.toml"
    payload = frame_for({profile: ["C-7D"]})
    payload["store_selection"] = {"path": __file__, "source": "env"}
    payload["selected_groups"].append("peers")
    payload["groups"]["peers"] = {"status": "OK", "data": {"peers": [
        {"peer_id": "cc", "profile": {"name": profile, "source": "packaged"}},
    ]}}
    monkeypatch.setattr(monitor, "_cached_profiles", lambda *a: [{
        "profile": profile,
        "_definition_layer": "packaged",
        "fields": {"selection_mode": {"source": "packaged"}, "pools": {"value": ["C-7D"]}},
    }])
    options = {"view": "rich", "width": 100, "height": None, "color": False, "expand": expand}
    linked = monitor._render_human_frame(payload, links=True, **options)
    plain = monitor._render_human_frame(payload, **options)
    assert OSC_LINK.findall(linked).count((config.as_uri(), profile)) == (3 if expand else 1)
    linked_rows = [line for line in linked.splitlines() if f"claude: {monitor_links.osc_link(config.as_uri(), profile)}" in line]
    assert len(linked_rows) == (2 if expand else 1)
    assert all(_console.ANSI_ESCAPE.sub("", line) in plain.splitlines() for line in linked_rows)


@pytest.mark.parametrize("interval", [12, 2, 0.5])
def test_live_activity_reads_do_not_advance_quota_reads_or_probes(monkeypatch: pytest.MonkeyPatch,
                                                                   interval: float) -> None:
    now = 0.0
    reads: list[tuple[float, tuple[str, ...]]] = []
    quota_reads: list[float] = []
    probes: list[float] = []
    frames: list[str] = []
    cadence = min(monitor.RUNNING_REFRESH_SECONDS, interval)
    assert f"min({monitor.RUNNING_REFRESH_SECONDS} s, --interval-seconds)" in MONITOR_DOC.read_text(encoding="utf-8")
    full_interval = max(interval, monitor_refresh.MONITOR_TICK_SECONDS)
    until = full_interval + cadence
    pool_ref = "quota:cc:C-7D"

    def sleep(seconds: float) -> None:
        nonlocal now
        now += seconds
        if now >= until:
            raise KeyboardInterrupt

    def read(db: str, groups: list[str], **kwargs: Any) -> dict[str, Any]:
        reads.append((now, tuple(groups)))
        payload = _payload([], [])
        payload["groups"] = {group: payload["groups"][group] for group in groups}
        payload["groups"]["activity"]["data"]["counts"] = {"running": int(now >= cadence)}
        return payload

    def probe(*args: Any, **kwargs: Any) -> SimpleNamespace:
        probes.append(now)
        return SimpleNamespace(done=lambda: False, stop=lambda: None, peers=("cc",))

    def read_quota(db: str) -> list[dict[str, Any]]:
        quota_reads.append(now)
        return [{"kind": "quota", "subject_ref": "cc", "resource_pool_ref": pool_ref,
                 "stored_state": "MEASURED", "payload": {"remaining_fraction": 0.5},
                 "age_seconds": monitor_refresh.MONITOR_QUOTA_MAX_AGE_SECONDS - full_interval + now}]

    monkeypatch.setattr(monitor, "sleep", sleep)
    monkeypatch.setattr(monitor, "monotonic", lambda: now)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr(_console, "enable_vt", lambda: True)
    monkeypatch.setattr(_console.FrameOutput, "write", lambda self, frame: frames.append(frame))
    monkeypatch.setattr("peerhub.cli.store_select.report_missing_store", lambda *a, **kw: False)
    monkeypatch.setattr(monitor_read_model, "render_monitor_frame", read)
    monkeypatch.setattr(monitor_refresh, "read_quota_readings", read_quota)
    monkeypatch.setattr(monitor_refresh, "required_quota_pools", lambda *a: {"cc": {pool_ref}})
    monkeypatch.setattr(monitor_refresh.RefreshJob, "start", probe)
    monkeypatch.setattr(monitor, "_cached_profiles", lambda *a: [])
    monkeypatch.setattr(monitor, "monitor_auto_probe", lambda args: True)
    args = argparse.Namespace(db=__file__, db_source="env", json=False, view="plain", cycles=0,
                              interval=interval, peers=["cc"], no_probe=False,
                              allow_agy_token_use=False)
    selected = ["activity", "capacity"]
    assert monitor._run_monitor_grouped_watch(args, selected, {}) == 0
    assert [at for at, groups in reads if groups == tuple(selected)] == quota_reads == [0, full_interval]
    assert [at for at, groups in reads if groups == ("activity",)] == [
        i * cadence for i in range(1, int(full_interval / cadence) + 1) if i * cadence < full_interval
    ]
    assert probes == [full_interval]
    assert any("RUNNING 1" in frame for frame in frames)


@pytest.mark.parametrize("retained_alerts", [False, True])
@pytest.mark.parametrize("expand", [False, True])
def test_live_activity_refresh_updates_alerts_sources_and_recovers(monkeypatch: pytest.MonkeyPatch,
                                                                  retained_alerts: bool, expand: bool) -> None:
    now = 0.0
    reads: list[tuple[float, tuple[str, ...]]] = []
    quota_reads: list[float] = []
    snapshots: dict[float, dict[str, Any]] = {}
    frames: dict[float, str] = {}
    selected = ["activity", "capacity", "execution"]
    observations = [pool("cx", "X-7D", 0 if retained_alerts else .5)]
    persistent_errors = {"execution": "OperationalError"} if retained_alerts else {}
    persistent = monitor_alerts.build_alerts(
        asks=None, pools=monitor_quota.pool_states(observations),
        accounts=[{"peer_id": "cc", "logged_in": False}] if retained_alerts else [],
        facts_report={"status": "needs_recheck", "reasons": ["version drift"]} if retained_alerts else None,
        failures=list(persistent_errors.items()), now=NOW)
    original_render = monitor._render_human_frame

    def sleep(seconds: float) -> None:
        nonlocal now
        now += seconds
        if now >= 46:
            raise KeyboardInterrupt

    def read(db: str, groups: list[str], **kwargs: Any) -> dict[str, Any]:
        reads.append((now, tuple(groups)))
        phase = int(now // monitor.RUNNING_REFRESH_SECONDS)
        row = (ask(alive=False) if phase == 1 else ask("overdue", lease=-5) if phase == 2 else
               ask("empty", age=5, exact=True) if phase == 3 else ask("ok", age=1, exact=True))
        payload = _payload([row], observations, read_at=NOW + now)
        payload["groups"]["execution"] = {"status": "ERROR" if persistent_errors else "OK", "data": {}}
        payload["groups"] = {group: payload["groups"][group] for group in groups}
        errors = {"activity": "OperationalError"} if phase == 5 else {}
        if phase == 5:
            payload["groups"]["activity"] = {"status": "ERROR", "data": None}
        elif phase == 6:
            payload["groups"]["activity"]["data"]["reason"] = "generation unreadable"
        elif phase == 8:
            payload["groups"]["activity"]["data"]["scopes_total"] = ACTIVITY_SCOPE_CAP + 1
        payload["alert_sources"] = {key: "not_selected" if groups == ["activity"] else "read"
                                    for key in ("activity", "capacity", "accounts", "facts")}
        payload["alert_sources"]["activity"] = "error" if phase in (5, 6) else "truncated" if phase == 8 else "read"
        payload["alerts"] = monitor_alerts.build_alerts(
            asks=None if errors else [row], pools=None, accounts=None, facts_report=None,
            failures=list(errors.items()), now=NOW + now)
        if groups != ["activity"]:
            errors.update(persistent_errors)
            payload["alerts"] = persistent + payload["alerts"]
        payload.update(errors=errors, status="PARTIAL" if errors else "OK")
        return payload

    def render(payload: dict[str, Any], **kwargs: Any) -> str:
        snapshots[now] = deepcopy(payload)
        return original_render(payload, **kwargs)

    def read_quota(db: str) -> list[dict[str, Any]]:
        quota_reads.append(now)
        return []

    monkeypatch.setattr(monitor, "sleep", sleep)
    monkeypatch.setattr(monitor, "monotonic", lambda: now)
    monkeypatch.setattr(monitor, "_render_human_frame", render)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr(_console, "enable_vt", lambda: True)
    monkeypatch.setattr(_console.FrameOutput, "geometry", staticmethod(lambda: (160, 80)))
    monkeypatch.setattr(_console.FrameOutput, "write", lambda self, text: frames.update({now: text}))
    monkeypatch.setattr("peerhub.cli.store_select.report_missing_store", lambda *a, **kw: False)
    monkeypatch.setattr(monitor_read_model, "render_monitor_frame", read)
    monkeypatch.setattr(monitor_refresh, "read_quota_readings", read_quota)
    monkeypatch.setattr(monitor_refresh.RefreshJob, "start", lambda *a, **kw: pytest.fail("quota probe"))
    monkeypatch.setattr(monitor, "_cached_profiles", lambda *a: [])
    monkeypatch.setattr(monitor, "monitor_auto_probe", lambda args: False)
    args = argparse.Namespace(db=__file__, db_source="env", json=False, view="plain", cycles=0, expand=expand,
                              interval=60, peers=["cx"], no_probe=True,
                              allow_agy_token_use=False)
    assert monitor._run_monitor_grouped_watch(args, selected, {}) == 0
    assert [at for at, groups in reads if groups == tuple(selected)] == [0]
    assert quota_reads == []
    assert [at for at, groups in reads if groups == ("activity",)] == list(range(5, 46, 5))
    for at, kinds in ((0, []), (5, ["gone"]), (10, ["overdue"]), (15, ["empty"]), (20, []),
                      (25, ["store"]), (30, []), (35, []), (40, []), (45, [])):
        payload = snapshots[at]
        added = [alert for alert in payload["alerts"] if alert not in persistent]
        assert [alert["kind"] for alert in added] == kinds
        assert [alert for alert in payload["alerts"] if alert in persistent] == persistent
        assert payload["errors"] == {**persistent_errors, **({"activity": "OperationalError"} if at == 25 else {})}
        assert payload["status"] == ("PARTIAL" if persistent_errors or at == 25 else "OK")
        assert payload["alert_sources"] == {
            "activity": "error" if at in (25, 30) else "truncated" if at == 40 else "read",
            "capacity": "read", "accounts": "read", "facts": "read"}
        assert payload["read_at"] == NOW and payload["display_elapsed_seconds"] == at
        assert payload["groups"]["capacity"] == snapshots[0]["groups"]["capacity"]
        if not retained_alerts:
            expected = "ATTN 1" if added else "OK?" if at in (30, 40) else "OK"
            assert frames[at].startswith(expected)
    assert "gone" in frames[5] and "overdue" in frames[10] and "empty" in frames[15]
    assert "activity: OperationalError" in frames[25]


@pytest.mark.parametrize("expand", [False, True])
@pytest.mark.parametrize("style,tty", [("rich", True), ("plain", False), ("rich", False)])
def test_guide_trailer_when_snapshot_has_no_guide_link(monkeypatch: pytest.MonkeyPatch,
                                                      capsys: pytest.CaptureFixture[str], expand: bool,
                                                      style: str, tty: bool) -> None:
    monkeypatch.setattr(_console.FrameOutput, "geometry", staticmethod(lambda: (120, 50)))
    monkeypatch.setattr(_console.FrameOutput, "width", staticmethod(lambda: 120))
    monkeypatch.setattr(_console, "enable_vt", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: tty)
    monitor._print_grouped_snapshot(_payload([], []), style, expand)
    screen = capsys.readouterr().out
    assert OSC_LINK.findall(screen) == []
    assert screen.splitlines()[-1] == f"{monitor_links.GUIDE_TEXT}: {DOC_URL}"
