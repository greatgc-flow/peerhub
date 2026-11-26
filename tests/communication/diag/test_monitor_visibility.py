"""Fixed viewport regressions from the owner's ConPTY captures (D1-D7)."""
from __future__ import annotations

from copy import deepcopy
from collections import Counter
import re
from typing import Any

import pytest

from peerhub.cli.monitor_layout import cell_width
from peerhub.cli.monitor_links import GUIDE_TEXT
from peerhub.cli.monitor_legend import FRAME_SECTIONS
from peerhub.cli.monitor_layout import FRAME_TRAILING_ROWS
from peerhub.cli.monitor_view import render_grouped_frame
from peerhub.extensions.pool_health import PACE_GAP_BASELINE

NOW = 1_794_522_324.0


def _view(expand: bool) -> str:
    """The view these pins select: the grouped --expand layout pinned here is the plain view's (the rich --expand
    frame is pinned in test_monitor_expand_rich.py); a compact frame keeps the default rich view."""
    return "plain" if expand else "rich"


@pytest.fixture
def frame() -> dict[str, Any]:
    pools = {"ag": ["3P-5H", "3P-7D", "A-7D", "G-5H", "G-7D"],
             "cc": ["C-5H", "C-7D", "F-7D"], "cx": ["X-5H", "X-7D", "X-30D"]}
    observations = [{"peer_id": peer, "pool_ref": f"quota:{peer}:{pool}",
                     "kind": "quota", "source": f"probe:{peer}", "state": "MEASURED",
                     "stored_state": "MEASURED", "age_seconds": 8, "ttl_seconds": 300,
                     "effective_at_us": int((NOW - 8) * 1_000_000),
                     "measurements": {"remaining_fraction": .42},
                     "last_measurements": {"remaining_fraction": .42},
                     "resets_at": NOW + 9000, "reset_in_seconds": 9000,
                     "projection": {"state": "insufficient", "pace_pt": 54}}
                    for peer, names in pools.items() for pool in names]
    sessions = [{"peer_id": peer, "handle": f"sess_{peer}_{i}", "state": "ACTIVE", "generation": 1,
                 "configured_binding": {"profile": f"{peer}.standard", "model": "model", "effort": "high"},
                 "telemetry": {"usage": {"normalized": {"provider_total_tokens": 1000 + i,
                     "provider_input_tokens": 1000, "cached_input_tokens": 500, "output_tokens": i}}}}
                for i in range(50) for peer in pools]
    return {"status": "OK", "read_at": NOW,
            # #374: the read model always reports alert_sources; without them --expand adds `source alerts: not read`
            "alert_sources": {"activity": "read", "capacity": "read", "accounts": "read", "facts": "read"},
            "selected_groups": ["capacity", "peers", "execution", "sessions", "usage"],
            "configured_profiles": [{"profile": f"{p}.standard", "fields": {"pools": {"value": names}}}
                                    for p, names in pools.items()],
            "groups": {"capacity": {"status": "OK", "data": {"observations": observations}},
                       "peers": {"status": "OK", "data": {"peers": [
                           {"peer_id": p, "session": {"mode": "fresh", "generation": 1}} for p in pools]}},
                       "execution": {"status": "OK", "data": {"scopes": []}},
                       "sessions": {"status": "OK", "data": {"items": sessions}},
                       "usage": {"status": "OK", "data": {"sessions": [
                           {**s, "telemetry": s["telemetry"]["usage"]} for s in sessions]}}}}


def pools_block(screen: str) -> str:
    """The POOLS rows of a compact frame in either view (P21: the rich view draws the head as a rule, and the
    suggestion follows the rows)."""
    pools = re.escape(FRAME_SECTIONS["pools"])
    return re.split(rf"\n(?:── |-- )?{pools} ", screen, maxsplit=1)[1].split(f"\n{FRAME_SECTIONS['suggest']}", 1)[0]


def test_d1_expanded_rows_remain_under_their_own_header(frame: dict[str, Any]) -> None:
    screen = render_grouped_frame(frame, width=120, height=400, view="plain", expand=True, unicode=False)
    section = ""
    for line in screen.splitlines():
        if line.startswith("-- "):
            section = line.split()[1]
        if "quota:" in line or "latest session:" in line:
            assert section == "PEERS", screen
        if "sess_" in line:
            assert section == "SESSIONS", screen
    peer_block = screen.split("PEERS [", 1)[1].split("-- ", 1)[0]
    assert all(f"{peer} " in peer_block for peer in ("ag", "cc", "cx")), screen
    session_block = screen.split("SESSIONS [", 1)[1].split("-- ", 1)[0]
    assert "sess_cx_49" in session_block  # #412: a tall frame lists every session; a short one is cut from the bottom


@pytest.mark.parametrize("width,height", [(60, 30), (80, 24), (100, 40)])
def test_d2_compact_pools_name_every_peer_without_peers_lines(frame: dict[str, Any], width: int, height: int) -> None:
    # #374: the PEERS summaries and the legend left the compact frame; every peer still has its POOLS rows.
    frame["configured_profiles"].extend([
        {"profile": "ag.deepthink", "fields": {"pools": {"value": ["3P-5H", "3P-7D"]}}},
        {"profile": "ag.probe", "fields": {"pools": {"value": ["A-7D"]}}},
        {"profile": "cc.fable", "fields": {"pools": {"value": ["F-7D"]}}}])
    screen = render_grouped_frame(frame, width=width, height=height, unicode=False)
    pools = pools_block(screen)
    for peer in ("ag", "cc", "cx"):
        assert any(line.startswith(f" {peer} ") for line in pools.splitlines()), screen
    assert "PEERS" not in screen and "ok enough" not in screen and "rows hidden" not in screen
    assert len(screen.splitlines()) < height
    assert all(cell_width(line) < width for line in screen.splitlines())


def test_d3_bounded_usage_is_per_peer_and_session_usage_has_identity(frame: dict[str, Any]) -> None:
    frame["selected_groups"] = ["sessions", "usage"]
    frame["groups"]["sessions"]["data"]["items"] = frame["groups"]["sessions"]["data"]["items"][:3]
    usage = frame["groups"]["usage"]["data"]["sessions"]
    usage[0]["telemetry"] = {"normalized": {"provider_total_tokens": 100,
        "provider_input_tokens": 100, "cached_input_tokens": 100}}
    usage[3]["telemetry"] = {"reported": {"input_tokens": 900, "output_tokens": 0, "cached_input_tokens": 0}}
    # Only two measured ag sessions; the remaining 48 must be counted as unknown.
    for item in usage[6::3]:
        item["telemetry"] = {}
    screen = render_grouped_frame(frame, width=160, height=400, view="plain", expand=True, unicode=False)
    usage_block = screen.split("USAGE [", 1)[1]
    assert usage_block.count("provider-reported tokens:") == 3, screen
    assert "ag provider-reported tokens: 1,000 | sessions 2 with/48 without usage | cache 10%" in screen
    assert any("sess_ag_0" in line and "tok 1,000" in line and "cache 50%" in line
               for line in screen.splitlines()), screen


@pytest.mark.parametrize("tty,vt", [(True, True), (True, False), (False, False)])
def test_d4_snapshot_tty_is_bounded_pipe_is_unbounded(
        frame: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
        tty: bool, vt: bool) -> None:
    from peerhub.cli.monitor import _print_grouped_snapshot
    from peerhub._console import FrameOutput
    import sys

    monkeypatch.setattr(sys.stdout, "isatty", lambda: tty)
    monkeypatch.setattr(FrameOutput, "__init__", lambda self, **kw: (
        setattr(self, "vt", vt), setattr(self, "started", False),
        setattr(self, "original_console_mode", None))[-1])
    monkeypatch.setattr(FrameOutput, "geometry", staticmethod(lambda: (120, 40)))
    monkeypatch.setattr(FrameOutput, "width", staticmethod(lambda: 120))
    _print_grouped_snapshot(frame, "plain", expand=True)
    screen = capsys.readouterr().out
    assert screen.startswith("OK") and screen.splitlines()[1].startswith("PeerHub Monitor")  # #374: verdict first
    if tty:
        assert len(screen.splitlines()) < 40
        assert "rows hidden" in screen and "all rows" in screen
        assert "peerhub monitor --snapshot --group all --expand | more" in screen
    else:
        assert len(screen.splitlines()) > 150 and "sess_cx_49" in screen
        assert "rows hidden" not in screen
        assert "with/" not in screen and "tok 1,000" not in screen


@pytest.mark.parametrize("expand", [False, True])
def test_d5_successful_probe_retires_omitted_window_at_read_time(frame: dict[str, Any], expand: bool) -> None:
    cap = frame["groups"]["capacity"]["data"]
    cap["observations"] = [o for o in cap["observations"] if o["peer_id"] == "cx"]
    old = cap["observations"][-1]
    old.update(state="STALE", age_seconds=23195, effective_at_us=int((NOW - 23195) * 1_000_000))
    cap["accounts"] = [{"peer_id": "cx", "plan": "pro", "state": "MEASURED"}]
    frame["selected_groups"] = ["capacity"]
    frame["configured_profiles"] = [p for p in frame["configured_profiles"] if p["profile"].startswith("cx.")]
    before = deepcopy(frame)
    screen = render_grouped_frame(frame, width=120, height=40, view=_view(expand), expand=expand, unicode=False)
    if expand:  # #400: never a row; the QUOTA details keep the one note of a retired pool
        row = next(line for line in screen.splitlines() if "X-30D" in line)
        assert "n/a" in row and "93%" not in row and "23195s" not in row
        assert "not reported by plan pro" in screen
    assert ("(X-30D n/a)" in screen) == expand
    assert "next:" not in screen and "6h" not in screen
    if not expand:
        assert "quota age 8s" in screen
    assert frame == before


def test_d6_legend_explains_supported_threat_rule(frame: dict[str, Any]) -> None:
    cap = frame["groups"]["capacity"]["data"]
    cap["observations"] = [cap["observations"][1], cap["observations"][6]]
    from tests.communication.diag.test_monitor_activity_view import anchor_pool
    item = cap["observations"][1]
    item["resets_at"] = NOW + item["reset_in_seconds"]
    item.update(projection_samples=2, last_measurements=dict(item["measurements"]))
    anchor_pool(item, NOW, 25)  # C1 uses measurement anchors.
    frame["selected_groups"] = ["capacity"]
    screen = render_grouped_frame(frame, width=120, height=40, unicode=False)
    # #374: the legend prints at the end of the (unbounded) --expand frame, no longer in the compact frame
    assert "fast >10% + threat (2+ samples)" in render_grouped_frame(frame, width=120, unicode=False, view="plain", expand=True)
    assert "ok" in next(line for line in screen.splitlines() if "3P-7D" in line).split()
    assert "fast" in next(line for line in screen.splitlines() if "C-7D" in line).split()


def test_d7_expanded_profiles_fit_at_120_columns(frame: dict[str, Any]) -> None:
    for suffix in ("effort", "deepthink"):
        frame["configured_profiles"].append({"profile": f"ag.{suffix}", "fields": {"pools": {"value": ["3P-7D"]}}})
    screen = render_grouped_frame(frame, width=120, height=40, view="plain", expand=True, unicode=False)
    header = next(line for line in screen.splitlines() if line.startswith("PEER "))
    assert "EXH/RESET" in header and "FAMILY" in header, screen
    assert "ag.standard" in screen and all(cell_width(line) < 120 for line in screen.splitlines())


@pytest.mark.parametrize("case", ["failed", "partial", "other_source", "expired", "no_plan", "same_probe"])
@pytest.mark.parametrize("expand", [False, True])
def test_d5_does_not_infer_omission_without_successful_current_evidence(
        frame: dict[str, Any], case: str, expand: bool) -> None:
    cap = frame["groups"]["capacity"]["data"]
    cap["observations"] = [o for o in cap["observations"] if o["peer_id"] == "cx"]
    old = cap["observations"][-1]
    old.update(state="STALE", age_seconds=23195, effective_at_us=int((NOW - 23195) * 1_000_000))
    cap["accounts"] = [{"peer_id": "cx", "plan": "pro", "state": "MEASURED"}]
    current = cap["observations"][0]
    if case == "failed":
        cap["observations"].append({**current, "pool_ref": None, "state": "ERROR"})
    elif case == "partial":
        current["state"] = "UNKNOWN"
    elif case == "other_source":
        old["source"] = "another-source"
    elif case == "expired":
        for item in cap["observations"][:-1]:
            item.update(state="STALE", age_seconds=301)
    elif case == "no_plan":
        cap["accounts"] = []
    else:
        old["effective_at_us"] = current["effective_at_us"]
    frame["selected_groups"] = ["capacity"]
    screen = render_grouped_frame(frame, width=120, height=40, view=_view(expand), expand=expand, unicode=False)
    row = next(line for line in screen.splitlines() if "X-30D" in line and not (expand and line.startswith(" ")))
    assert "aged" in row  # C1 unifies both state words.  # --expand: the QUOTA row (a POOLS row starts with a space)
    assert "not reported by plan" not in screen and "next:" in screen


def test_d5_omission_is_scoped_to_the_peer_when_pools_are_shared(frame: dict[str, Any]) -> None:
    cap = frame["groups"]["capacity"]["data"]
    old = cap["observations"][-1]
    old.update(state="STALE", age_seconds=23195, effective_at_us=int((NOW - 23195) * 1_000_000))
    cap["observations"].append({**old, "peer_id": "other"})
    cap["accounts"] = [{"peer_id": "cx", "plan": "pro", "state": "MEASURED"}]
    screen = render_grouped_frame(frame, width=120, view="plain", expand=True, unicode=False)
    other_row = next(line for line in screen.splitlines() if line.startswith("other ") and "X-30D" in line)
    assert "aged" in other_row and "n/a" not in other_row  # C1


@pytest.mark.parametrize("width,height", [(60, 30), (80, 24), (100, 40), (120, 40), (160, 50), (220, 60), (320, 70)])
@pytest.mark.parametrize("expand", [False, True])
def test_all_measured_viewports_preserve_bounds_and_disclose_cuts(
        frame: dict[str, Any], width: int, height: int, expand: bool) -> None:
    screen = render_grouped_frame(frame, width=width, height=height, view=_view(expand), expand=expand, unicode=False)
    assert screen.startswith("OK")  # #374: line 1 is the verdict; the `PeerHub Monitor` header is line 2
    assert screen.splitlines()[1].startswith("PeerHub Monitor")
    assert len(screen.splitlines()) < height
    assert all(cell_width(line) < width for line in screen.splitlines())
    if expand:
        assert "hidden" in screen
        assert "--expand" in screen


@pytest.fixture
def round2_frame(frame: dict[str, Any]) -> dict[str, Any]:
    frame.update(refresh_state="REFRESH OK", next_refresh_in=589,
                 store_selection={"source": "env", "path": "C:/example/workspace/my-app/.peerhub/core.db"})
    cap = frame["groups"]["capacity"]["data"]
    cap["accounts"] = [{"peer_id": p, "plan": "pro", "plan_status": "reported", "logged_in": True,
                        "login_expiry_status": "n/a", "auth_method": "chatgpt", "age_seconds": 16000,
                        "state": "MEASURED"} for p in ("cc", "cx", "ag")]
    cap["reset_credits"] = [{"peer_id": "cx", "available_count": 0, "state": "MEASURED"}]
    for obs in cap["observations"]:
        if obs["pool_ref"].endswith(("C-5H", "X-5H", "X-30D")):
            obs.update(state="ABSENT", stored_state="ABSENT", measurements=None,
                       reason="not reported by provider; plan=pro")
        else:
            obs["projection"].update(state="threat", exhaustion_at=NOW + 24000)
    frame["configured_profiles"].extend([
        {"profile": "cc.deepthink", "fields": {"pools": {"value": ["C-5H", "C-7D"]}}},
        {"profile": "cc.effort", "fields": {"pools": {"value": ["C-5H", "C-7D"]}}},
        {"profile": "cc.fable", "fields": {"pools": {"value": ["F-7D"]}}}])
    for peer in frame["groups"]["peers"]["data"]["peers"]:
        p = peer["peer_id"]
        peer.update(quota={"state": "MEASURED", "pool_ref": "G-7D", "remaining_fraction": .42},
                    profile={"name": f"{p}.deepthink/claude-sonnet-5-5-high", "source": "packaged"})
    return frame


@pytest.mark.parametrize("expand", [False, True])
@pytest.mark.parametrize("unicode_mode", [False, True])
def test_peer_separators_at_100x40_and_data_priority_at_80x24(
        round2_frame: dict[str, Any], expand: bool, unicode_mode: bool) -> None:
    before = deepcopy(round2_frame)
    spacious = render_grouped_frame(round2_frame, width=100, height=40, view=_view(expand), expand=expand, unicode=unicode_mode)
    lines = spacious.splitlines()
    tight = render_grouped_frame(round2_frame, width=80, height=24, view=_view(expand), expand=expand, unicode=unicode_mode)
    assert "\n\n" not in tight
    if expand:  # #412: POOLS (every pool) is the top of the frame at any height; the QUOTA details follow while they fit
        for pool in ("3P-5H", "3P-7D", "A-7D", "G-5H", "G-7D", "C-7D", "F-7D", "X-7D"):  # C-5H and X-5H are n/a (#400)
            assert pool in tight
        assert "rows hidden" in tight
    else:  # #374 F5: no blank lines between peers; 5-hour rows that do not bind stay in --expand
        assert "\n\n" not in spacious
        for pool in ("3P-7D", "A-7D", "G-7D", "C-7D", "F-7D", "X-7D"):
            assert pool in tight
        assert all(pool not in tight for pool in ("3P-5H", "X-5H", "X-30D"))
    assert len(lines) < 40 and all(cell_width(line) < 100 for line in lines)
    assert len(tight.splitlines()) < 24 and all(cell_width(line) < 80 for line in tight.splitlines())
    assert round2_frame == before


@pytest.mark.parametrize("width,height", [(60, 30), (80, 24), (120, 40), (220, 60)])
@pytest.mark.parametrize("expand", [False, True])
@pytest.mark.parametrize("unicode_mode", [False, True])
def test_e1_e2_e4_e5_priority_and_whole_fields_at_fixed_viewports(
        round2_frame: dict[str, Any], width: int, height: int, expand: bool, unicode_mode: bool) -> None:
    screen = render_grouped_frame(round2_frame, width=width, height=height, view=_view(expand), expand=expand, unicode=unicode_mode)
    lines = screen.splitlines()
    assert lines[0].startswith("OK") and lines[1].startswith("PeerHub Monitor"), screen  # #374: verdict first
    assert len(lines) < height and all(cell_width(line) < width for line in lines)
    if not expand:  # #374: the compact frame names each peer in its POOLS rows; PEERS is --expand only
        assert "PEERS" not in screen
    for peer in ("ag", "cc", "cx"):  # #412: --expand starts with the same POOLS rows
        assert any(line.lstrip().startswith(peer + " ") for line in pools_block(screen).splitlines()), screen
    for line in lines[1:]:  # line 1 joins its fields with " | " and drops whole fields, never cuts them
        if re.search(r" (?:\||·) ", line):
            assert "…" not in line and "~" not in line, line
    if expand:
        assert "rows hidden" in screen and "--expand" in screen, screen
    assert not re.search(r"\+\d+r\b|\d+g hidden", screen)


def test_e3_legend_pointer_target_prints_the_complete_legend(
        round2_frame: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    from peerhub.cli import main

    # #374: the legend left the compact frame; `monitor --help` and the unbounded --expand frame print the same legend
    from peerhub.cli.monitor_legend import MONITOR_LEGEND

    bounded = render_grouped_frame(round2_frame, width=80, height=24, unicode=False)
    assert "Legend:" not in bounded and "ok enough" not in bounded
    with pytest.raises(SystemExit) as result:
        main(["monitor", "--help"])
    assert result.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    full = " ".join(render_grouped_frame(round2_frame, width=100, unicode=False, view="plain", expand=True).split())
    for line in MONITOR_LEGEND:
        assert " ".join(line.split()) in help_text and " ".join(line.split()) in full


@pytest.mark.parametrize("width", [60, 80, 120])
def test_e2_accounts_drop_complete_fields(round2_frame: dict[str, Any], width: int) -> None:
    round2_frame["selected_groups"] = ["capacity"]
    screen = render_grouped_frame(round2_frame, width=width, height=200, view="plain", expand=True)
    rows = [line for line in screen.splitlines() if "login ok" in line]
    assert len(rows) == 3
    for row in rows:
        assert "…" not in row and "~" not in row
        assert all(field in {"plan pro", "login token expiry n/a", "auth chatgpt", "age 4h26m [MEASURED]", "fields hidden"}
                   for field in row.split(" | ")[1:])
        if width < 120:
            assert "fields hidden" in row


@pytest.mark.parametrize("width", [20, 32, 34, 35, 36, 60, 80])
def test_e2_group_rules_disclose_trailing_field_cuts(width: int) -> None:
    from peerhub.cli.monitor_layout import section_rule

    line = section_rule("QUOTA [OK] | measured remaining | soonest exhaust: ag G-7D 5% in 6h42m", width, True)
    assert cell_width(line) <= width
    if width == 20:
        assert line.startswith("── QUOTA [OK]") and line.endswith("…")
    elif " | " in line:
        assert "…" not in line


def test_e2_field_omissions_are_announced_even_when_height_is_available(round2_frame: dict[str, Any]) -> None:
    for suffix in ("astra", "deepthink", "effort", "standard", "terra"):
        round2_frame["configured_profiles"].append({"profile": f"cx.{suffix}",
            "fields": {"pools": {"value": ["X-5H", "X-7D", "X-30D"]}}})
    # #374: the compact PEERS lines are --expand only, where a long peer line drops whole fields and says so
    screen = render_grouped_frame(round2_frame, width=220, height=60, view="plain", expand=True)
    peer_line = next(line for line in screen.split("PEERS", 1)[1].splitlines() if line.startswith("cx "))
    assert "…" not in peer_line
    compact = render_grouped_frame(round2_frame, width=60, height=60, view="plain", unicode=False)
    assert "Fields hidden | --expand" in compact.splitlines()  # line 2 lost fields at 60 columns
    rich = render_grouped_frame(round2_frame, width=60, height=60)  # P21: the rich view's separator
    assert "Fields hidden · --expand" in rich.splitlines()


def test_e4_80_columns_keep_plain_counts_and_the_complete_command(round2_frame: dict[str, Any]) -> None:
    screen = render_grouped_frame(round2_frame, width=80, height=24, view="plain", expand=True)
    assert re.search(r"^\+\d+ rows hidden", screen, re.MULTILINE), screen
    assert "all rows" in screen and "peerhub monitor --snapshot --group all --expand | more" in screen


def test_e1_peer_summaries_precede_accounts_without_quota_evidence(round2_frame: dict[str, Any]) -> None:
    round2_frame["groups"]["capacity"]["data"]["observations"] = []
    screen = render_grouped_frame(round2_frame, width=80, height=200, view="plain", expand=True)
    peers = [screen.index(f"{peer}  quota:") for peer in ("ag", "cc", "cx")]
    assert screen.index("ACCOUNTS") < min(peers), screen  # #412: the QUOTA section (its accounts too) precedes PEERS


def test_r3_bounded_latest_session_rows_name_their_peer(round2_frame: dict[str, Any]) -> None:
    round2_frame["selected_groups"] = ["peers"]
    peers = round2_frame["groups"]["peers"]["data"]["peers"]
    peers.append({**deepcopy(peers[0]), "peer_id": "worker-long-id"})
    screen = render_grouped_frame(round2_frame, width=120, height=40, view="plain", expand=True)
    rows = [row for row in screen.splitlines() if "-> latest session:" in row]
    assert len(rows) == 4
    assert [row.split()[0] for row in rows] == ["ag", "cc", "cx", "worker-long-id"]


@pytest.mark.parametrize("text,width", [
    ("cc login ok | plan pro | login token expiry n/a | auth chatgpt | age 4h26m [MEASURED]", 59),
    ("PeerHub Monitor | OK | REFRESH OK | refresh in 589s | 2026-11-12 22:25:24Z", 59),
])
def test_r3_every_dropped_field_has_a_note(text: str, width: int) -> None:
    from peerhub.cli.monitor_layout import fit_fields

    rendered = fit_fields(text, width, True)
    assert "fields hidden" in rendered and "…" not in rendered
    assert cell_width(rendered) <= width


def test_r3_compact_snapshot_without_vt_keeps_append_behaviour(
        frame: dict[str, Any], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    from peerhub.cli.monitor import _print_grouped_snapshot
    from peerhub._console import FrameOutput
    import sys

    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(FrameOutput, "__init__", lambda self, **kw: (
        setattr(self, "vt", False), setattr(self, "started", False),
        setattr(self, "original_console_mode", None))[-1])

    def geometry() -> tuple[int, int]:
        raise AssertionError("A compact snapshot without VT must keep append geometry")

    monkeypatch.setattr(FrameOutput, "geometry", staticmethod(geometry))
    monkeypatch.setattr(FrameOutput, "width", staticmethod(lambda: 120))
    _print_grouped_snapshot(frame, "plain")
    screen = capsys.readouterr().out
    assert len(screen.splitlines()) > 8 and "rows hidden" not in screen
    assert "suggest:" in screen and "\nPOOLS " in screen  # #374: no legend or PEERS lines in the compact frame


def _data_rows(screen: str) -> Counter[str]:
    """Count visible data, excluding headings, decoration and cut notices."""
    rows = []
    for raw in screen.splitlines():
        line = re.sub(r"\x1b\[[0-9;]*m", "", raw).strip()
        if (not line or line.startswith(("PeerHub Monitor", "DB:", "Coverage:", "STATE:", "── ", "-- ", "peerhub monitor",
                                         f"{GUIDE_TEXT}: "))
                or re.match(r"^\+\d+ (?:more|rows hidden|--expand|pools)", line)):
            continue
        rows.append(line)
    return Counter(rows)


@pytest.mark.parametrize("width", [60, 80, 120, 220])
def test_r3_height_3_through_60_sweep_has_exact_cuts_and_peer_priority(
        round2_frame: dict[str, Any], width: int) -> None:
    for peer_count in (3, 7):
        payload = deepcopy(round2_frame)
        peers = payload["groups"]["peers"]["data"]["peers"]
        peers.extend({**deepcopy(peers[0]), "peer_id": f"worker{i}"} for i in range(peer_count - 3))
        roomy = render_grouped_frame(payload, width=width, height=1000, view="plain", expand=True)
        whole = [line for line in roomy.splitlines() if not line.startswith(f"{GUIDE_TEXT}: ")]
        all_rows = _data_rows("\n".join(whole))
        # The verdict and title are the last lines to give way.
        tiny = render_grouped_frame(payload, width=width, height=3, view="plain", expand=True).splitlines()
        assert tiny == whole[:2]
        for height in range(4, 61):
            screen = render_grouped_frame(payload, width=width, height=height, view="plain", expand=True)
            lines = screen.splitlines()
            assert len(lines) < height and all(cell_width(line) < width for line in lines), (width, height, screen)
            shown = _data_rows(screen)
            assert not shown - all_rows, (width, height, shown - all_rows, screen)  # nothing is invented
            # #412: the frame is cut from the bottom; the count is exactly the lines left out (the coverage line aside)
            cut = next(((i, match) for i, line in enumerate(lines) if (match := re.match(r"\+(\d+) ", line))), None)
            if cut is None:  # the final pool row and its gap take priority over the hint
                assert len(lines) == height - FRAME_TRAILING_ROWS
                assert any(line.startswith(f"gap vs {PACE_GAP_BASELINE}") for line in lines)
            else:
                assert "--expand | more" in screen
                at, match = cut
                assert int(match[1]) == len(whole) - 1 - at, (width, height, screen)
                if "rows hidden" in lines[at]:
                    command = "peerhub monitor --snapshot --group all --expand | more"
                    if "all rows" in lines[at]:
                        assert command in screen
                    else:
                        short_hints = (command, "--expand | more")
                        assert any(re.fullmatch(rf"\+\d+ rows hidden (?:·|\|) {re.escape(hint)}", lines[at])
                                   for hint in short_hints)
            for line in lines:
                if "-> latest session:" in line:
                    assert line.split()[0] in {peer["peer_id"] for peer in peers}


@pytest.mark.parametrize("width", [80, 120])
def test_r3_colour_keeps_bounds_content_and_disclosures(
        round2_frame: dict[str, Any], width: int, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    # The plain view never paints; the colour of the rich --expand frame is pinned in test_monitor_expand_rich.py.
    coloured = render_grouped_frame(round2_frame, width=width, height=40, view="plain", expand=True, color=True)
    plain = render_grouped_frame(round2_frame, width=width, height=40, view="plain", expand=True, color=False)
    assert "\x1b[" not in coloured and coloured == plain
    assert plain.splitlines()[0] == "OK"
    assert all(cell_width(line) < width for line in coloured.splitlines())
    assert "all rows" in coloured and "--expand | more" in coloured


@pytest.mark.parametrize("width", [40, 45, 60, 80])
@pytest.mark.parametrize("expand", [False, True])
@pytest.mark.parametrize("seconds", [1, 589])
@pytest.mark.parametrize("refresh_state", ["REFRESH OK", "REFRESH WARN"])
def test_live_title_keeps_refresh_and_countdown_before_lower_priority_fields(
        round2_frame: dict[str, Any], width: int, expand: bool, seconds: int, refresh_state: str) -> None:
    round2_frame["next_refresh_in"] = seconds
    round2_frame["refresh_state"] = refresh_state
    round2_frame["store_selection"]["source"] = "long-source-label"
    screen = render_grouped_frame(round2_frame, width=width, height=24, view=_view(expand), expand=expand)
    title = screen.splitlines()[1]  # #374: line 1 is the verdict
    assert title.startswith(("PeerHub Monitor", "Monitor"))
    assert refresh_state in title and f"refresh in {seconds}s" in title, screen
    if not expand or width < 80:
        assert "fields hidden" in screen.lower() or "rows hidden" in screen.lower()
    assert "…" not in title and all(cell_width(line) < width for line in screen.splitlines())
    if width == 80 and (not expand or refresh_state == "REFRESH OK" or seconds == 1):
        assert "2026-11-12 22:25Z" in title
        assert "long-source-label" not in title and "quota age" not in title


@pytest.mark.parametrize("unicode_mode", [False, True])
@pytest.mark.parametrize("width", [35, 40, 53])
def test_usage_field_drop_has_a_cut_marker(frame: dict[str, Any], width: int, unicode_mode: bool) -> None:
    frame["selected_groups"] = ["usage"]
    screen = render_grouped_frame(frame, width=width, height=40, view="plain", expand=True, unicode=unicode_mode)
    row = next(line for line in screen.splitlines() if line.lstrip().startswith("ag provider-reported tokens:"))
    assert row.endswith(("fields hidden", "…" if unicode_mode else "~")), screen  # #412: a viewport names the fields it drops
    assert cell_width(row) < width


@pytest.mark.parametrize("view,unicode_mode,sep", [("plain", False, " | "), ("rich", True, " · ")])  # P21: the separator
def test_compact_title_uses_frame_disclosure_to_keep_timestamp(
        round2_frame: dict[str, Any], view: str, unicode_mode: bool, sep: str) -> None:
    round2_frame["next_refresh_in"] = None
    screen = render_grouped_frame(round2_frame, width=60, height=40, view=view, unicode=unicode_mode)
    title = screen.splitlines()[1]  # #374: line 1 is the verdict
    assert f"REFRESH OK{sep}2026-11-12 22:25Z" in title, screen
    assert "fields hidden" not in title
    assert f"Fields hidden{sep}--expand" in screen


@pytest.mark.parametrize("width", [20, 28, 35])
def test_live_title_falls_back_to_complete_countdown(round2_frame: dict[str, Any], width: int) -> None:
    round2_frame["refresh_state"] = "REFRESH WARN"
    title = render_grouped_frame(round2_frame, width=width, height=40).splitlines()[1]  # #374: line 2
    assert title == "refresh in 589s"


@pytest.mark.parametrize("width", [8, 12, 14])
def test_live_title_never_shows_partial_countdown_digits(round2_frame: dict[str, Any], width: int) -> None:
    title = render_grouped_frame(round2_frame, width=width, height=40).splitlines()[1]  # #374: line 2
    assert not re.search(r"\d", title)
    assert cell_width(title) < width


@pytest.mark.parametrize("height", [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 20])
def test_compact_small_heights_follow_the_single_removal_order(frame: dict[str, Any], height: int) -> None:
    # M9/G8 add notes to the POOLS stage. A short viewport still keeps the verdict and fitting rows.
    full = render_grouped_frame(frame, width=80, height=1000, view="plain", unicode=False).splitlines()
    lines = render_grouped_frame(frame, width=80, height=height, view="plain", unicode=False).splitlines()
    assert lines[:2] == full[:2] and len(lines) < height
    if any(line.startswith("suggest:") for line in lines):
        assert any(line.startswith("gap vs ") for line in lines)
    if any(line.startswith("gap vs ") for line in lines):
        assert any(line.startswith("POOLS ") for line in lines)


def test_compact_frame_at_80x24_discloses_line_2_cuts_and_keeps_every_pool(round2_frame: dict[str, Any]) -> None:
    screen = render_grouped_frame(round2_frame, width=80, height=24, view="plain", unicode=False)
    lines = screen.splitlines()
    assert lines[0].startswith("OK") and lines[1].startswith("PeerHub Monitor")
    assert lines[2] == "Fields hidden | --expand"  # line 2 lost DB and quota age at 80 columns
    pools = next(i for i, line in enumerate(lines) if line.startswith("POOLS "))
    # every 7-day pool; no cut, no overflow line (P21: the suggestion follows the rows)
    assert lines[pools].split()[1] == "6"
    assert lines.index(next(line for line in lines if line.startswith("gap vs "))) > pools
    assert any(line.startswith("suggest:") for line in lines)  # M9
    assert "Legend:" not in screen and "PEERS" not in screen and "rows hidden" not in screen


@pytest.mark.parametrize("sep", [" | ", " · "])
@pytest.mark.parametrize("unicode_mode", [False, True])
def test_painted_field_fitting_preserves_cell_bounds(sep: str, unicode_mode: bool) -> None:
    from peerhub.cli.monitor_layout import fit_fields

    plain = sep.join(["ag provider-reported tokens: 51,225", "sessions 50 with/0 without usage", "cache 50%"])
    painted = plain.replace("51,225", "\x1b[32m51,225\x1b[0m")
    for width in range(1, cell_width(plain) + 2):
        fitted = fit_fields(painted, width, unicode_mode, painted=True)
        assert cell_width(fitted) <= width
        assert re.sub(r"\x1b\[[0-9;]*m", "", fitted) == fit_fields(plain, width, unicode_mode)
        if width >= cell_width(plain):
            assert fitted == painted
        else:
            assert "\x1b" not in fitted


@pytest.mark.parametrize("invalid", ["1794522316000000", "bad", None, True, float("nan"), float("inf")])
@pytest.mark.parametrize("invalid_first", [False, True])
def test_plan_omission_ignores_invalid_timestamps(frame: dict[str, Any], invalid: object, invalid_first: bool) -> None:
    from peerhub.cli.monitor_quota_view import display_retired_windows

    cap = frame["groups"]["capacity"]["data"]
    old, current = [deepcopy(item) for item in cap["observations"] if item["peer_id"] == "cx"][:2]
    old.update(state="STALE", effective_at_us=int((NOW - 1000) * 1_000_000))
    current["effective_at_us"] = float(current["effective_at_us"])
    malformed = {**current, "effective_at_us": invalid, "state": "ERROR", "pool_ref": None}
    cap["observations"] = [malformed, old, current] if invalid_first else [old, current, malformed]
    cap["accounts"] = [{"peer_id": "cx", "plan": "pro", "state": "MEASURED"}]
    result = display_retired_windows(frame)
    rows = result["groups"]["capacity"]["data"]["observations"]
    assert next(row for row in rows if row["pool_ref"] == old["pool_ref"])["display_retired"] is True
    assert old["state"] == "STALE"


def test_expanded_spare_rows_favor_accounts_after_all_peer_summaries(round2_frame: dict[str, Any]) -> None:
    round2_frame["selected_groups"] = ["capacity", "peers"]
    cap = round2_frame["groups"]["capacity"]["data"]
    cap["observations"] = cap["observations"][:1]
    round2_frame["configured_profiles"] = []
    screen = render_grouped_frame(round2_frame, width=120, height=80, view="plain", expand=True, unicode=False)
    assert all(f"{peer}  quota:" in screen for peer in ("ag", "cc", "cx"))
    assert "cc login ok | plan pro" in screen  # #412: ACCOUNTS belong to QUOTA, which comes before PEERS
    assert screen.index("cc login ok") < screen.index("ag  quota:")


@pytest.mark.parametrize("unicode_mode", [False, True])
@pytest.mark.parametrize("width,height", [(40, 8), (34, 40), (40, 40), (54, 40), (32, 40), (20, 40), (12, 40)])
def test_no_quota_guidance_never_loses_command_silently(
        frame: dict[str, Any], width: int, height: int, unicode_mode: bool) -> None:
    frame["groups"]["capacity"]["data"]["observations"] = []
    frame["configured_profiles"] = []
    frame["store_selection"] = {
        "source": "explicit", "path": "/tmp/pytest-of-runner/pytest-0/test_posix_pty_snapshot_obeys_0/m.db"}
    screen = render_grouped_frame(frame, width=width, height=height, unicode=unicode_mode)
    lines = screen.splitlines()
    assert len(lines) < height and all(cell_width(line) < width for line in lines)
    if height == 8:  # #374 H2: the POOLS rows go before the header; `POOLS 0` still says there is no quota evidence
        head = "── POOLS 0 " + "─" * 28 if unicode_mode else "-- POOLS 0 " + "-" * 28  # P21: this view draws a rule
        assert head in lines and "rows hidden" not in screen, screen
        return
    row_index = next(i for i, line in enumerate(lines) if "no quota evidence".startswith(line.rstrip("…~")) and line)
    command = "next: peerhub observation refresh"
    guidance = lines[row_index + 1]
    if width >= 34:
        assert guidance == command, screen
    else:
        assert "next: peerhub observation refresh".startswith(guidance[:-1]) and guidance.endswith("…" if unicode_mode else "~"), screen
