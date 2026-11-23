"""Offline coverage of the monitor header, local links and activity cadence."""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args

import pytest

from peerhub import __version__
from peerhub.cli import monitor, monitor_preferences, monitor_refresh, monitor_view, support, view
from peerhub.extensions import monitor_read_model
from peerhub.extensions.pool_health import PoolState
from tests.communication.diag.test_monitor_plain_golden import _payload
from tests.communication.diag.test_monitor_pool_health import frame, reading
from tests.communication.diag.test_monitor_tree import frame_for

DOC_URL = "https://example.test/docs/monitor.md"
OSC_LINK = re.compile(r"\x1b\]8;;([^\x1b]+)\x1b\\([^\x1b]+)\x1b\]8;;\x1b\\")
MONITOR_DOC = Path(__file__).resolve().parents[3] / monitor_view.GUIDE_DOC


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
    assert monitor_view._ANSI.sub("", linked[0]) == base[0]
    assert OSC_LINK.findall(linked[1]) == [(DOC_URL, monitor_view.GUIDE_TEXT)]
    assert view.visible_len(linked[1]) == width - 1
    assert ("refresh failed" if live else "PeerHub Monitor") in linked[1]
    assert linked[2:] == base[2:len(linked)]


@pytest.mark.parametrize("expand", [False, True])
@pytest.mark.parametrize("style", ["rich", "plain"])
def test_existing_db_path_has_a_link_with_unchanged_visible_text(monkeypatch: pytest.MonkeyPatch,
                                                            capsys: pytest.CaptureFixture[str], expand: bool,
                                                            style: str) -> None:
    db = Path(__file__).resolve()
    payload = {"groups": {}, "store_selection": {"path": str(db), "source": "env"}}
    monkeypatch.setattr(view.FrameOutput, "geometry", staticmethod(lambda: (80, 50)))
    monkeypatch.setattr(view.FrameOutput, "width", staticmethod(lambda: 80))
    monkeypatch.setattr(view, "enable_vt", lambda: True)

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
    linked_path_lines = [monitor_view._ANSI.sub("", line) for line in linked.splitlines() if db.as_uri() in line]
    assert linked_path_lines and all(line in disabled.splitlines() for line in linked_path_lines)
    assert all(view.visible_len(line) < 80 for line in linked_path_lines)
    missing = db.with_name("missing.db")
    payload["store_selection"]["path"] = str(missing)
    assert all(url != missing.as_uri() for url, _ in OSC_LINK.findall(snapshot(tty=True)))


def test_defaults_show_links_only_the_existing_config_file(monkeypatch: pytest.MonkeyPatch,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    config = Path(__file__).resolve().parents[3] / "pyproject.toml"
    monkeypatch.setattr(monitor_preferences, "_preference_paths", lambda *a: (config, config))
    monkeypatch.setattr(monitor_preferences, "_decode", lambda *a: {
        "status": "OK", "source": "workspace", "reason": None, "selected_groups": [],
    })
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr(view, "enable_vt", lambda: True)
    args = argparse.Namespace(monitor_action="defaults", defaults_action="show", json=False)
    assert monitor_preferences.run_preferences(args) == 0
    linked = capsys.readouterr().out
    assert OSC_LINK.findall(linked) == [(config.as_uri(), "workspace")]
    monkeypatch.setenv("PEERHUB_NO_LINKS", "1")
    assert monitor_preferences.run_preferences(args) == 0
    assert capsys.readouterr().out == monitor_view._ANSI.sub("", linked)


@pytest.mark.parametrize("expand", [False, True])
def test_header_quota_state_links_to_its_documented_vocabulary(expand: bool) -> None:
    payload = _payload([], [])
    payload["min_quota"] = {"left_pct": 68, "pool": "C-7D", "state": "ok"}
    linked = monitor_view.render_grouped_frame(payload, links=True, expand=expand)
    [(url, label)] = OSC_LINK.findall(linked.splitlines()[0])
    base, anchor = url.split("#")
    assert (base, label) == (monitor_view.guide_link_url({}), payload["min_quota"]["state"])
    assert f'id="{anchor}"' in MONITOR_DOC.read_text(encoding="utf-8")


def test_every_pool_state_link_targets_an_anchor_in_the_monitor_doc() -> None:
    pool, state = "G-7D", "ok"
    display = frame(reading("ag", pool, .5, 0))
    screen = monitor_view.render_grouped_frame(display, links=True, expand=True)
    [(url, _), *_] = [(url, label) for url, label in OSC_LINK.findall(screen) if label == state]
    base = url.split("#")[0]
    assert base.endswith(f"/blob/v{__version__}/{monitor_view.GUIDE_DOC}")
    doc = MONITOR_DOC.read_text(encoding="utf-8")
    anchors = set()
    for state in get_args(PoolState):
        [(url, label)] = OSC_LINK.findall(monitor_view.link_pool_state(base, state))
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
    base = monitor_view.guide_link_url({})
    assert base is not None
    target = monitor_view.link_pool_state(base, state)
    assert all(target in line for line in linked_rows)
    assert [monitor_view._ANSI.sub("", line) for line in linked_rows] == disabled_rows
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
    assert OSC_LINK.findall(linked).count((config.as_uri(), profile)) == (4 if expand else 2)
    linked_rows = [line for line in linked.splitlines() if f"claude: {monitor_view.osc_link(config.as_uri(), profile)}" in line]
    assert len(linked_rows) == (2 if expand else 1)
    assert all(monitor_view._ANSI.sub("", line) in plain.splitlines() for line in linked_rows)
    assert next(line for line in plain.splitlines() if line.startswith("suggest:")) == next(
        monitor_view._ANSI.sub("", line) for line in linked.splitlines() if line.startswith("suggest:")
    )


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
    full_interval = max(interval, support.MONITOR_TICK_SECONDS)
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
                 "age_seconds": support.MONITOR_QUOTA_MAX_AGE_SECONDS - full_interval + now}]

    monkeypatch.setattr(monitor, "sleep", sleep)
    monkeypatch.setattr(monitor, "monotonic", lambda: now)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setattr(view, "enable_vt", lambda: True)
    monkeypatch.setattr(view.FrameOutput, "write", lambda self, frame: frames.append(frame))
    monkeypatch.setattr("peerhub.cli.store_select.report_missing_store", lambda *a, **kw: False)
    monkeypatch.setattr(monitor_read_model, "render_monitor_frame", read)
    monkeypatch.setattr(monitor_refresh, "read_quota_readings", read_quota)
    monkeypatch.setattr(monitor_refresh, "required_quota_pools", lambda *a: {"cc": {pool_ref}})
    monkeypatch.setattr(monitor_refresh.RefreshJob, "start", probe)
    monkeypatch.setattr(monitor, "_cached_profiles", lambda *a: [])
    monkeypatch.setattr(monitor, "monitor_auto_probe", lambda args: True)
    args = argparse.Namespace(db=__file__, db_source="env", json=False, view="plain", cycles=0,
                              interval=interval, collect_every=1, peers=["cc"], probe=False, no_probe=False,
                              allow_agy_token_use=False)
    selected = ["activity", "capacity"]
    assert monitor._run_monitor_grouped_watch(args, selected, {}) == 0
    assert [at for at, groups in reads if groups == tuple(selected)] == quota_reads == [0, full_interval]
    assert [at for at, groups in reads if groups == ("activity",)] == [
        i * cadence for i in range(1, int(full_interval / cadence) + 1) if i * cadence < full_interval
    ]
    assert probes == [full_interval]
    assert any("RUNNING 1" in frame for frame in frames)


@pytest.mark.parametrize("expand", [False, True])
@pytest.mark.parametrize("style,tty", [("rich", True), ("plain", False)])
def test_guide_trailer_when_snapshot_has_no_guide_link(monkeypatch: pytest.MonkeyPatch,
                                                      capsys: pytest.CaptureFixture[str], expand: bool,
                                                      style: str, tty: bool) -> None:
    monkeypatch.setattr(view.FrameOutput, "geometry", staticmethod(lambda: (120, 50)))
    monkeypatch.setattr(view.FrameOutput, "width", staticmethod(lambda: 120))
    monkeypatch.setattr(view, "enable_vt", lambda: False)
    monkeypatch.setattr("sys.stdout.isatty", lambda: tty)
    monitor._print_grouped_snapshot(_payload([], []), style, expand)
    screen = capsys.readouterr().out
    assert OSC_LINK.findall(screen) == []
    assert screen.splitlines()[-1] == f"{monitor_view.GUIDE_TEXT}: {DOC_URL}"
