"""Fixed viewport regressions from the owner's ConPTY captures (D1-D7)."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

import pytest

from peerhub.cli.monitor_view import render_grouped_frame, _width

NOW = 1_794_522_324.0


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


def test_d1_expanded_rows_remain_under_their_own_header(frame: dict[str, Any]) -> None:
    screen = render_grouped_frame(frame, width=120, height=40, expand=True, unicode=False)
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
    assert "+" in session_block and "more" in session_block


@pytest.mark.parametrize("width,height", [(60, 30), (80, 24), (100, 40)])
def test_d2_peer_summaries_precede_optional_legends(frame: dict[str, Any], width: int, height: int) -> None:
    frame["configured_profiles"].extend([
        {"profile": "ag.deepthink", "fields": {"pools": {"value": ["3P-5H", "3P-7D"]}}},
        {"profile": "ag.argon", "fields": {"pools": {"value": ["A-7D"]}}},
        {"profile": "cc.fable", "fields": {"pools": {"value": ["F-7D"]}}}])
    screen = render_grouped_frame(frame, width=width, height=height, unicode=False)
    peers = screen.split("PEERS", 1)[1]
    for peer in ("ag", "cc", "cx"):
        assert any(line.startswith(peer + " ") for line in peers.splitlines()), screen
    assert len(screen.splitlines()) < height
    assert all(_width(line) < width for line in screen.splitlines())
    if width == 80:
        assert "rows hidden" in screen and "--expand" in screen


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
    screen = render_grouped_frame(frame, width=160, height=50, expand=True, unicode=False)
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
    from peerhub.cli.view import FrameOutput
    import sys

    monkeypatch.setattr(sys.stdout, "isatty", lambda: tty)
    monkeypatch.setattr(FrameOutput, "__init__", lambda self, **kw: (
        setattr(self, "vt", vt), setattr(self, "started", False),
        setattr(self, "original_console_mode", None))[-1])
    monkeypatch.setattr(FrameOutput, "geometry", staticmethod(lambda: (120, 40)))
    monkeypatch.setattr(FrameOutput, "width", staticmethod(lambda: 120))
    _print_grouped_snapshot(frame, "plain", expand=True)
    screen = capsys.readouterr().out
    assert screen.startswith("PeerHub Monitor")
    if tty:
        assert len(screen.splitlines()) < 40
        assert "rows hidden" in screen and "peerhub monitor --snapshot --group all --expand" in screen
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
    screen = render_grouped_frame(frame, width=120, height=40, expand=expand, unicode=False)
    row = next(line for line in screen.splitlines() if "X-30D" in line)
    assert "n/a" in row and "93%" not in row and "23195s" not in row
    assert "not reported by plan pro" in screen
    assert "next:" not in screen and "6h" not in screen
    if not expand:
        assert "quota age 8s" in screen
    assert frame == before


def test_d6_legend_explains_supported_threat_rule(frame: dict[str, Any]) -> None:
    cap = frame["groups"]["capacity"]["data"]
    cap["observations"] = [cap["observations"][1], cap["observations"][6]]
    cap["observations"][1].update(projection={"state": "threat", "pace_pt": 53}, projection_samples=2)
    frame["selected_groups"] = ["capacity"]
    screen = render_grouped_frame(frame, width=120, height=40, unicode=False)
    assert "fast >10% + threat (2+ samples)" in screen
    assert "ok" in next(line for line in screen.splitlines() if "3P-7D" in line).split()
    assert "fast" in next(line for line in screen.splitlines() if "C-7D" in line).split()


def test_d7_expanded_profiles_fit_at_120_columns(frame: dict[str, Any]) -> None:
    for suffix in ("effort", "deepthink"):
        frame["configured_profiles"].append({"profile": f"ag.{suffix}", "fields": {"pools": {"value": ["3P-7D"]}}})
    screen = render_grouped_frame(frame, width=120, height=40, expand=True, unicode=False)
    header = next(line for line in screen.splitlines() if line.startswith("PEER "))
    assert "EXHAUST-IN" in header and "PROFILES (configured)" in header, screen
    assert "ag.standard" in screen and all(_width(line) < 120 for line in screen.splitlines())


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
    screen = render_grouped_frame(frame, width=120, height=40, expand=expand, unicode=False)
    row = next(line for line in screen.splitlines() if "X-30D" in line)
    assert ("STALE:window" if expand else "aged") in row
    assert "not reported by plan" not in screen and "next:" in screen


def test_d5_omission_is_scoped_to_the_peer_when_pools_are_shared(frame: dict[str, Any]) -> None:
    cap = frame["groups"]["capacity"]["data"]
    old = cap["observations"][-1]
    old.update(state="STALE", age_seconds=23195, effective_at_us=int((NOW - 23195) * 1_000_000))
    cap["observations"].append({**old, "peer_id": "other"})
    cap["accounts"] = [{"peer_id": "cx", "plan": "pro", "state": "MEASURED"}]
    screen = render_grouped_frame(frame, width=120, height=40, expand=True, unicode=False)
    other_row = next(line for line in screen.splitlines() if line.startswith("other ") and "X-30D" in line)
    assert "STALE:window" in other_row and "n/a" not in other_row


@pytest.mark.parametrize("width,height", [(60, 30), (80, 24), (100, 40), (120, 40), (160, 50), (220, 60), (320, 70)])
@pytest.mark.parametrize("expand", [False, True])
def test_all_measured_viewports_preserve_bounds_and_disclose_cuts(
        frame: dict[str, Any], width: int, height: int, expand: bool) -> None:
    screen = render_grouped_frame(frame, width=width, height=height, expand=expand, unicode=False)
    assert screen.startswith("PeerHub Monitor") or screen.startswith("QUOTA")
    assert len(screen.splitlines()) < height
    assert all(_width(line) < width for line in screen.splitlines())
    if expand:
        assert "hidden" in screen or "groups omitted" in screen
        assert "--expand" in screen


@pytest.fixture
def round2_frame(frame: dict[str, Any]) -> dict[str, Any]:
    frame.update(refresh_state="REFRESH OK", next_refresh_in=589,
                 store_selection={"source": "env", "path": "D:/PkgDev/workspace/peerhub/.peerhub/core.db"})
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


@pytest.mark.parametrize("width,height", [(60, 30), (80, 24), (120, 40), (220, 60)])
@pytest.mark.parametrize("expand", [False, True])
@pytest.mark.parametrize("unicode_mode", [False, True])
def test_e1_e2_e4_e5_priority_and_whole_fields_at_fixed_viewports(
        round2_frame: dict[str, Any], width: int, height: int, expand: bool, unicode_mode: bool) -> None:
    screen = render_grouped_frame(round2_frame, width=width, height=height, expand=expand, unicode=unicode_mode)
    lines = screen.splitlines()
    assert lines[0].startswith("PeerHub Monitor"), screen
    assert len(lines) < height and all(_width(line) < width for line in lines)
    peer_block = screen.split("PEERS", 1)[1].split("── ", 1)[0].split("-- ", 1)[0]
    for peer in ("ag", "cc", "cx"):
        assert any(line.lstrip().startswith(peer + " ") for line in peer_block.splitlines()), screen
    for line in lines:
        if re.search(r" (?:\||·) ", line):
            assert "…" not in line and "~" not in line, line
    if expand or width <= 80:
        assert "rows hidden" in screen and "--expand" in screen, screen
    assert not re.search(r"\+\d+r\b|\d+g hidden", screen)
    if expand:
        peer_positions = [next(i for i, line in enumerate(lines) if line.lstrip().startswith(peer + " ")
                              and "quota:" in line) for peer in ("ag", "cc", "cx")]
        account_positions = [i for i, line in enumerate(lines) if "login " in line or line.startswith("ACCOUNTS")]
        assert not account_positions or max(peer_positions) < min(account_positions), screen


def test_e3_legend_pointer_target_prints_the_complete_legend(
        round2_frame: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    from peerhub.cli import main

    bounded = render_grouped_frame(round2_frame, width=80, height=24, unicode=False)
    assert "Legend: peerhub monitor --help" in bounded
    with pytest.raises(SystemExit) as result:
        main(["monitor", "--help"])
    assert result.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    full = render_grouped_frame(round2_frame, width=100, unicode=False)
    legend = " ".join(full.split("ok enough", 1)[1].split("PEERS", 1)[0].split())
    assert "ok enough " + legend in help_text


@pytest.mark.parametrize("width", [60, 80, 120])
def test_e2_accounts_drop_complete_fields(round2_frame: dict[str, Any], width: int) -> None:
    round2_frame["selected_groups"] = ["capacity"]
    screen = render_grouped_frame(round2_frame, width=width, height=40, expand=True)
    rows = [line for line in screen.splitlines() if "login ok" in line]
    assert len(rows) == 3
    for row in rows:
        assert "…" not in row and "~" not in row
        assert all(field in {"plan pro", "login token expiry n/a", "auth chatgpt", "age 4h26m [MEASURED]", "fields hidden"}
                   for field in row.split(" | ")[1:])


@pytest.mark.parametrize("width", [20, 32, 34, 35, 36, 60, 80])
def test_e2_group_rules_never_cut_a_trailing_field(width: int) -> None:
    from peerhub.cli.monitor_view import _section_rule

    line = _section_rule("QUOTA [OK] | measured remaining | soonest exhaust: ag G-7D 5% in 6h42m", width, True)
    assert _width(line) <= width
    if " | " in line:
        assert "…" not in line


def test_e2_field_omissions_are_announced_even_when_height_is_available(round2_frame: dict[str, Any]) -> None:
    for suffix in ("astra", "deepthink", "effort", "standard", "terra"):
        round2_frame["configured_profiles"].append({"profile": f"cx.{suffix}",
            "fields": {"pools": {"value": ["X-5H", "X-7D", "X-30D"]}}})
    screen = render_grouped_frame(round2_frame, width=220, height=60)
    assert "Fields hidden | --expand" in screen
    peer_line = next(line for line in screen.split("PEERS", 1)[1].splitlines() if line.startswith("cx "))
    assert "…" not in peer_line


def test_e4_80_columns_keep_plain_counts_and_the_complete_command(round2_frame: dict[str, Any]) -> None:
    screen = render_grouped_frame(round2_frame, width=80, height=24, expand=True)
    assert re.search(r"^\+\d+ rows hidden \(\d+ groups\) · peerhub monitor --snapshot --group all --expand$",
                     screen, re.MULTILINE), screen


def test_e1_peer_summaries_precede_accounts_without_quota_evidence(round2_frame: dict[str, Any]) -> None:
    round2_frame["groups"]["capacity"]["data"]["observations"] = []
    screen = render_grouped_frame(round2_frame, width=80, height=24, expand=True)
    peers = [screen.index(f"{peer}  quota:") for peer in ("ag", "cc", "cx")]
    assert max(peers) < screen.index("ACCOUNTS"), screen
