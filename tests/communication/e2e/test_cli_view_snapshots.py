"""Live A2c acceptance frames and independent arithmetic fixtures; no providers."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from peerhub.cli.view import advance_report, format_frame, render_view, use_color, visible_len
from peerhub.extensions.diag import DiagnosticReport, Section
from peerhub.extensions.diag_quota import projection as item_projection

TZ = timezone(timedelta(hours=9))
NOW = datetime.fromisoformat("2026-10-12T08:52:46+09:00").timestamp()
STORE = r"D:\PkgDev\workspace\peerhub\.peerhub\core.db"
INPUTS = [
    ("ag", "3P-5H", 5 * 3600, 0, 4 * 3600 + 59 * 60),
    ("ag", "G-5H", 5 * 3600, 0, 4 * 3600 + 59 * 60),
    ("ag", "3P-7D", 7 * 86400, 74, 86400 + 18 * 60),
    ("ag", "G-7D", 7 * 86400, 46, 3 * 86400 + 20 * 3600),
    ("cc", "C-5H", 5 * 3600, 5, 3 * 3600 + 55 * 60),
    ("cc", "C-7D", 7 * 86400, 80, 86400 + 8 * 3600),
    ("cx", "X-5H", 5 * 3600, 1, 4 * 3600 + 58 * 60),
    ("cx", "X-7D", 7 * 86400, 4, 6 * 86400 + 16 * 3600),
]


def owner_report():
    items = [{"kind": "quota", "subject_ref": peer, "resource_pool_ref": pool,
              "state": "MEASURED", "age_seconds": None,
              "observed_at": "2026-10-11T23:52:46Z",
              "payload": {"remaining_fraction": 1 - used / 100,
                          "window_started_at": NOW + remaining - span,
                          "resets_at": NOW + remaining}}
             for peer, pool, span, used, remaining in reversed(INPUTS)]
    items += [{"kind": "reset_credit", "subject_ref": "cx", "state": "MEASURED",
               "age_seconds": None, "payload": {"available_count": 2,
               "nearest_expires_at": datetime.fromisoformat("2026-10-29T18:52:46+00:00").timestamp()}},
              {"kind": "quota", "subject_ref": "cc", "state": "ERROR",
               "payload": {"reason": "claude_usage_timeout"}}]
    return DiagnosticReport("OK", NOW, {}, {
        "observations": Section("observations", "OK", {"items": items}),
        "resource_pools": Section("resource_pools", "OK", {"pools": []}),
    })


LIVE_NOW = 1791769344.0
# Sanitized committed evidence behind screen_live.txt, pinned at 10:42:24 +0900.
LIVE_ITEMS = json.loads(r'''[
    {
        "kind": "activity",
        "subject_ref": "ag",
        "resource_pool_ref": null,
        "state": "STALE",
        "age_seconds": 3787.802342,
        "observed_at": "2026-10-12T00:39:16.197658+00:00",
        "payload": {
            "operation_elapsed_seconds": 843.5264541999786,
            "status": "delivered"
        }
    },
    {
        "kind": "quota",
        "subject_ref": "ag",
        "resource_pool_ref": "quota:ag:3P-5H",
        "state": "MEASURED",
        "age_seconds": 59.0,
        "observed_at": "2026-10-12T01:41:25.000000Z",
        "payload": {
            "remaining_fraction": 1.0,
            "resets_at": 1791787290,
            "window_started_at": 1791769290
        }
    },
    {
        "kind": "quota",
        "subject_ref": "ag",
        "resource_pool_ref": "quota:ag:3P-7D",
        "state": "MEASURED",
        "age_seconds": 59.0,
        "observed_at": "2026-10-12T01:41:25.000000Z",
        "payload": {
            "remaining_fraction": 0.2643887996673584,
            "resets_at": 1791936627,
            "window_started_at": 1791331827
        }
    },
    {
        "kind": "quota",
        "subject_ref": "ag",
        "resource_pool_ref": "quota:ag:G-5H",
        "state": "MEASURED",
        "age_seconds": 59.0,
        "observed_at": "2026-10-12T01:41:25.000000Z",
        "payload": {
            "remaining_fraction": 0.8355798125267029,
            "resets_at": 1791779154,
            "window_started_at": 1791761154
        }
    },
    {
        "kind": "quota",
        "subject_ref": "ag",
        "resource_pool_ref": "quota:ag:G-7D",
        "state": "MEASURED",
        "age_seconds": 59.0,
        "observed_at": "2026-10-12T01:41:25.000000Z",
        "payload": {
            "remaining_fraction": 0.512014627456665,
            "resets_at": 1791944779,
            "window_started_at": 1791339979
        }
    },
    {
        "kind": "quota",
        "subject_ref": "cc",
        "resource_pool_ref": "quota:cc:C-5H",
        "state": "MEASURED",
        "age_seconds": 73.0,
        "observed_at": "2026-10-12T01:41:11.000000Z",
        "payload": {
            "remaining_fraction": 0.76,
            "resets_at": 1791778200,
            "window_started_at": 1791760200
        }
    },
    {
        "kind": "quota",
        "subject_ref": "cc",
        "resource_pool_ref": "quota:cc:C-7D",
        "state": "MEASURED",
        "age_seconds": 73.0,
        "observed_at": "2026-10-12T01:41:11.000000Z",
        "payload": {
            "remaining_fraction": 0.17000000000000004,
            "resets_at": 1791896400,
            "window_started_at": 1791291600
        }
    },
    {
        "kind": "activity",
        "subject_ref": "cx",
        "resource_pool_ref": null,
        "state": "MEASURED",
        "age_seconds": 107.057892,
        "observed_at": "2026-10-12T01:40:36.942108+00:00",
        "payload": {
            "operation_elapsed_seconds": 214.30533840000862,
            "status": "delivered"
        }
    },
    {
        "kind": "quota",
        "subject_ref": "cx",
        "resource_pool_ref": "quota:cx:X-5H",
        "state": "MEASURED",
        "age_seconds": 77.0,
        "observed_at": "2026-10-12T01:41:07.000000Z",
        "payload": {
            "remaining_fraction": 0.27,
            "resets_at": 1791778805,
            "window_started_at": 1791760805
        }
    },
    {
        "kind": "quota",
        "subject_ref": "cx",
        "resource_pool_ref": "quota:cx:X-7D",
        "state": "MEASURED",
        "age_seconds": 77.0,
        "observed_at": "2026-10-12T01:41:07.000000Z",
        "payload": {
            "remaining_fraction": 0.84,
            "resets_at": 1792297127,
            "window_started_at": 1791692327
        }
    },
    {
        "kind": "reset_credit",
        "subject_ref": "cx",
        "resource_pool_ref": "credit:cx:reset",
        "state": "MEASURED",
        "age_seconds": 77.0,
        "observed_at": "2026-10-12T01:41:07.000000Z",
        "payload": {
            "available_count": 2,
            "nearest_expires_at": 1793299966
        }
    }
]''')


def live_report():
    import copy
    return DiagnosticReport("OK", LIVE_NOW, {}, {
        "observations": Section("observations", "OK", {"items": copy.deepcopy(LIVE_ITEMS)}),
    })


def live_frame(width, **kwargs):
    return render_view(live_report(), width=width, unicode=False, tz=TZ,
                       store_path=STORE, store_source="explicit", **kwargs)


def frame(report, width, **kwargs):
    return render_view(report, width=width, unicode=False, tz=TZ, store_path=STORE,
                       store_source="explicit", cycle=1, next_refresh_in=60,
                       refresh={"probes": [{"peer": "ag", "status": "OK"},
                                          {"peer": "cx", "status": "OK"}]}, **kwargs)


@pytest.mark.parametrize("width", [80, 100, 120])
def test_owner_screen_snapshot(width):
    out = live_frame(width)
    assert out == SNAPSHOTS[width]
    assert max(map(visible_len, out.splitlines())) <= width
    assert len(out.splitlines()) == 16
    assert out.splitlines()[-1].endswith("Ctrl-C quit")
    assert "\x1b" not in out and " [H [2J" not in out
    assert "ETA:" not in out and "(probe)" not in out


@pytest.mark.parametrize("width", [80, 100, 120])
def test_live_rich_snapshot(width):
    out = render_view(live_report(), width=width, unicode=True, tz=TZ,
                      store_path=STORE, store_source="explicit")
    assert out == RICH_SNAPSHOTS[width]
    assert max(map(visible_len, out.splitlines())) <= width
    assert len(out.splitlines()) == 16
    assert out.splitlines()[-1].endswith("Ctrl-C quit") and "\x1b" not in out
    assert "▲ exh" in out and "○ not enough data" in out


@pytest.mark.parametrize("width", [80, 100, 120])
def test_forecast_and_freshness_columns_align_at_every_width(width):
    out = live_frame(width)
    lines = out.splitlines()
    header = next(line for line in lines if line.startswith("PEER"))
    forecast_column, fresh_column = header.index("FORECAST"), header.index("FRESH")
    rows = [line for line in lines if line.startswith(("ag ", "cc ", "cx "))]
    assert len(rows) == 8
    assert [line.split()[1] for line in rows[:4]] == ["3P-5H", "3P-7D", "G-5H", "G-7D"]
    assert all(line[forecast_column] in "!=:?" for line in rows)
    assert all(line[fresh_column] == "*" for line in rows)
    assert "WIN" not in header and "PACE(pt)" in header
    assert "(cond)" not in out and "51m" in out and "50m52s" not in out


def test_header_health_and_monitor_schedule_and_absent_asks():
    from dataclasses import replace
    rep = replace(owner_report(), status="PARTIAL")
    diag = render_view(rep, unicode=False, width=100, tz=TZ)
    assert diag.startswith("PeerHub PARTIAL ")
    assert "c-" not in diag and "next" not in diag.splitlines()[0]
    assert "Asks" not in diag and "counts unavailable" not in diag
    monitor = render_view(rep, unicode=False, width=100, tz=TZ, cycle=7, next_refresh_in=180)
    assert "c7 next: 3m" in monitor.splitlines()[0]


@pytest.mark.parametrize("value", ["", "1"])
def test_no_color_keeps_rich_state_glyphs(monkeypatch, value):
    monkeypatch.setenv("NO_COLOR", value)
    out = render_view(live_report(), width=100, unicode=True, color=True, tz=TZ,
                      store_path=STORE, store_source="explicit")
    assert out == RICH_SNAPSHOTS[100]


@pytest.mark.parametrize("state,remaining,offset,plain,rich", [
    ("MEASURED", 0.2, 0, "! exhausts", "▲ exhausts"),
    ("MEASURED", 0.8, 0, "= on pace", "● on pace"),
    ("MEASURED", 0.99, 0, ": not enough data", "○ not enough data"),
    ("MEASURED", 0, 0, "x exhausted", "✖ exhausted"),
    ("UNKNOWN", 0.2, 0, "? unknown", "◌ unknown"),
    ("STALE", 0.2, 0, "~ stale", "△ stale"),
    ("MEASURED", 0.2, 86400, "/ expired", "⊘ expired"),
])
def test_all_states_have_style_specific_glyph_and_word(state, remaining, offset, plain, rich):
    item = {"kind": "quota", "subject_ref": "cx", "resource_pool_ref": "X-5H",
            "state": state, "age_seconds": 45, "observed_at": datetime.fromtimestamp(NOW, timezone.utc).isoformat(),
            "payload": {"remaining_fraction": remaining, "window_started_at": NOW - 9000,
                        "resets_at": NOW + 9000}}
    rep = DiagnosticReport("OK", NOW + offset, {}, {
        "observations": Section("observations", "OK", {"items": [item]}),
    })
    ascii_frame = render_view(rep, width=100, unicode=False, tz=TZ)
    rich_frame = render_view(rep, width=100, unicode=True, tz=TZ)
    assert plain in ascii_frame and rich in rich_frame
    assert ("~45s" if state == "STALE" else "*45s") in ascii_frame
    assert ("○45s" if state == "STALE" else "●45s") in rich_frame


@pytest.mark.parametrize("value", ["", "1"])
def test_no_color_presence_preserves_glyphs_and_words(monkeypatch, value):
    monkeypatch.setenv("NO_COLOR", value)
    assert live_frame(100, color=True) == SNAPSHOTS[100]


@pytest.mark.parametrize("width", [80, 100, 120])
def test_all_alerts_retained_with_dated_etas_and_long_path(width):
    rep = owner_report()
    items = rep.sections["observations"].data["items"]
    for item in items:
        if item.get("resource_pool_ref"):
            item["payload"].update(remaining_fraction=0.2, window_started_at=NOW - 3.5 * 86400,
                                   resets_at=NOW + 3.5 * 86400)
    items[0]["payload"]["remaining_fraction"] = 0
    items[-2]["payload"]["nearest_expires_at"] = NOW + 3600
    out = frame(rep, width)
    before_table = out.split("PEER", 1)[0]
    for peer, pool, *_ in INPUTS:
        assert f"{peer}/{pool}" in before_table
    alerts = " ".join(before_table.split())
    assert "7 threats - earliest" in alerts and "exhausted cx/X-7D;" in alerts
    assert "credit expiry<72h cx 09:52 in 1h00m" in alerts
    assert "x exhausted" in out and "Tue" in out
    assert max(map(visible_len, out.splitlines())) <= width


@pytest.mark.parametrize("used,elapsed,decision", [
    (0.05, 99.999, ": not enough data"), (0.05, 100, "= on pace"),
    (0.049999, 100, ": not enough data"), (0.050001, 100.001, "= on pace"),
    # 1 - (1 - .105) is below .105 in binary floating point. Use .525
    # at 50% elapsed for an exactly 105% projected reset boundary.
    (0.104999, 100, "= on pace"), (0.525, 500, "! exhausts"),
    (0.105001, 100, "! exhausts"), (0.01, 79, ": not enough data"),
])
def test_gate_transitions_keep_measurement_pace(used, elapsed, decision):
    rep = owner_report()
    item = rep.sections["observations"].data["items"][0]
    item["payload"] = {"remaining_fraction": 1 - used, "window_started_at": NOW - elapsed,
                       "resets_at": NOW - elapsed + (18000 if elapsed == 79 else 1000)}
    rep.sections["observations"].data["items"] = [item]
    projection = item_projection(item, NOW)
    out = frame(rep, 120)
    assert decision in out
    assert f"{projection['pace_pt']:+.1f}" in out
    later = advance_report(rep, NOW + 1)
    assert item_projection(later.sections["observations"].data["items"][0], NOW + 1) == projection


def test_projection_fields_match_plain_and_rich_decisions():
    rep = owner_report()
    for item in rep.sections["observations"].data["items"]:
        if not item.get("resource_pool_ref"):
            continue
        projection = item_projection(item, NOW)
        assert projection["anchor_basis"] == "reset_minus_duration_inferred"
        if projection["state"] == "insufficient":
            assert projection["reason"] is not None
        else:
            assert projection["reason"] is None
        single = DiagnosticReport("OK", NOW, {}, {
            "observations": Section("observations", "OK", {"items": [item]}),
        })
        plain = format_frame(single, "plain", width=120)
        rich = render_view(single, color=False, unicode=True, width=120)
        label = ": not enough data" if projection["state"] == "insufficient" else "= on pace"
        assert label in plain
        assert label.replace(":", "○").replace("=", "●") in rich
        if projection["pace_pt"] is not None:
            assert f"{projection['pace_pt']:+.1f}" in plain
            assert f"{projection['pace_pt']:+.1f}" in rich


def test_plain_is_ascii_without_escapes_even_if_color_requested():
    out = format_frame(owner_report(), "plain", color=True, unicode=True, width=80)
    assert out.isascii() and "\x1b" not in out and " [H [2J" not in out
    assert max(map(visible_len, out.splitlines())) <= 80


def test_redirected_rich_output_has_no_escapes_or_cursor_artifacts():
    out = frame(owner_report(), 100, color=use_color(False, {"FORCE_COLOR": "1"}))
    assert "\x1b" not in out and " [H [2J" not in out


@pytest.mark.parametrize("width", [80, 100, 120])
def test_colour_width_and_palette(monkeypatch, width):
    import re

    monkeypatch.delenv("NO_COLOR", raising=False)
    out = frame(owner_report(), width, color=True)
    assert max(map(visible_len, out.splitlines())) <= width
    assert set(re.findall(r"\x1b\[([0-9;]+)m", out)) <= {"0", "31", "33", "36"}


@pytest.mark.parametrize("width", [80, 100, 120])
def test_eta_over_twenty_four_hours_retains_date_and_condition(width):
    rep = owner_report()
    item = rep.sections["observations"].data["items"][0]
    # 70% used at halfway through seven days exhausts in 1.5 days.
    item["payload"].update(remaining_fraction=0.3, window_started_at=NOW - 3.5 * 86400,
                           resets_at=NOW + 3.5 * 86400)
    rep.sections["observations"].data["items"] = [item]
    out = frame(rep, width)
    assert ("! exh Tue 20:52 (1d12h)" if width < 100 else "! exhausts Tue 20:52 (in 1d12h)") in out
    assert "Threats: 1 threat - earliest cx/X-7D Tue 20:52" in out
    assert max(map(visible_len, out.splitlines())) <= width


@pytest.mark.parametrize("width", [80, 100, 120])
@pytest.mark.parametrize("remaining,elapsed,forecast", [
    (0.2, 0.5, "! exhausts Tue 05:52 (in 21h00m)"),
    (0.01, 0.8, "! exhausts 10:14 (in 1h21m)"),
])
def test_eta_calendar_day_controls_date_below_twenty_four_hours(width, remaining, elapsed, forecast):
    rep = owner_report()
    item = rep.sections["observations"].data["items"][0]
    item["payload"].update(remaining_fraction=remaining,
                           window_started_at=NOW - elapsed * 7 * 86400,
                           resets_at=NOW + (1 - elapsed) * 7 * 86400)
    rep.sections["observations"].data["items"] = [item]
    out = frame(rep, width)
    if width < 100:
        forecast = forecast.replace("exhausts", "exh").replace("(in ", "(")
    assert forecast in out
    assert max(map(visible_len, out.splitlines())) <= width


def test_fresh_early_exhaustion_appears_in_b_and_c_without_support():
    rep = owner_report()
    item = rep.sections["observations"].data["items"][0]
    item["payload"].update(remaining_fraction=0, window_started_at=NOW - 1,
                           resets_at=NOW + 17999)
    rep.sections["observations"].data["items"] = [item]
    out = frame(rep, 100)
    assert "exhausted cx/X-7D" in out and "x exhausted" in out
    assert ": not enough data" not in out


def test_age_ttl_and_reset_expiry_on_redraw_without_mutating_snapshot():
    rep = owner_report()
    item = rep.sections["observations"].data["items"][0]
    item.update(age_seconds=0, ttl_seconds=10)
    later = advance_report(rep, NOW + 10)
    updated = later.sections["observations"].data["items"][0]
    assert item["state"] == "MEASURED" and updated["state"] == "STALE"
    assert item_projection(updated, later.read_at)["state"] == "stale"
    assert item_projection(updated, item["payload"]["resets_at"])["state"] == "expired"


def test_active_peers_fit_twenty_lines_at_120_columns():
    rep = owner_report()
    rep.sections["activity"] = Section("activity", "OK", {
        "interval_seconds": 3600,
        "peers": [{"peer": peer, "running": 1, "asks": 5, "ok": 3,
                   "uncertain": 1, "failed": 1, "last_status": "delivered",
                   "last_age_seconds": 54, "last_seconds": 214} for peer in ("ag", "cc", "cx")],
    })
    out = frame(rep, 120)
    assert len(out.splitlines()) == 17
    assert max(map(visible_len, out.splitlines())) <= 120
    assert out.count("Asks ") == 3 and "delivered 54s ago, took 4m" in out


@pytest.mark.parametrize("state,decision", [
    ("STALE", "~ stale"), ("UNKNOWN", "? unknown"),
    ("UNAVAILABLE", "? unknown"), ("ERROR", "? unknown"),
])
def test_evidence_precedence_in_inline_forecasts(state, decision):
    rep = owner_report()
    item = rep.sections["observations"].data["items"][0]
    item["state"] = state
    item["payload"]["remaining_fraction"] = 0
    rep.sections["observations"].data["items"] = [item]
    out = frame(rep, 100)
    assert decision in out and "x exhausted" not in out


def test_invalid_anchor_hides_pace_and_unicode_freshness_is_not_colour_only():
    rep = owner_report()
    item = rep.sections["observations"].data["items"][0]
    item["payload"]["window_started_at"] = NOW + 1
    item["age_seconds"] = 2
    rep.sections["observations"].data["items"] = [item]
    out = render_view(rep, width=80, color=False, unicode=True, tz=TZ)
    assert "◌ unknown" in out and "●2s" in out
    row = next(line for line in out.splitlines() if line.startswith("cx "))
    assert "?" in row and "\x1b" not in out


def test_registered_unknown_pools_keep_short_before_long_order():
    from peerhub.cli.view import _quota_rows

    pools = [{"resource_pool_id": pool, "provider": peer, "kind": "QUOTA"}
             for peer, pool, *_ in reversed(INPUTS)]
    rows = _quota_rows([], pools, NOW)
    assert [(row.peer, row.pool) for row in rows] == sorted((peer, pool) for peer, pool, *_ in INPUTS)
    assert all(row.projection["state"] == "unknown" for row in rows)


@pytest.mark.parametrize("states,summary_state,wording", [
    (["STALE", "STALE"], "unknown", "Threats: unknown - evidence is stale (oldest 10m); refresh"),
    (["UNKNOWN", "UNKNOWN"], "unknown", "Threats: unknown - evidence is missing or invalid; refresh"),
    (["STALE", "UNKNOWN"], "unknown", "Threats: unknown - evidence is stale (oldest 10m); refresh"),
    (["MEASURED", "STALE"], "clear", "Threats: No threats; 1 pools stale"),
    (["MEASURED", "MEASURED"], "clear", "Threats: No threats"),
])
def test_threat_summary_discloses_coverage_in_plain_rich_and_json(states, summary_state, wording):
    from peerhub.cli.view import dashboard_data
    rep = owner_report()
    items = rep.sections["observations"].data["items"][:2]
    for item, state in zip(items, states):
        item.update(state=state, age_seconds=600 if state == "STALE" else 12)
        item["payload"].update(remaining_fraction=0.9, window_started_at=NOW - 9000, resets_at=NOW + 9000)
    rep.sections["observations"].data["items"] = items
    data = dashboard_data(rep)
    assert data["threats"]["state"] == summary_state
    assert data["threats"]["fresh_pools"] == states.count("MEASURED")
    assert data["threats"]["stale_pools"] == states.count("STALE")
    assert data["threats"]["unknown_pools"] == states.count("UNKNOWN")
    for unicode in (False, True):
        out = render_view(rep, width=120, unicode=unicode, tz=TZ)
        assert wording in out
        if summary_state == "unknown":
            assert "No threats" not in out
    plain = render_view(rep, width=120, unicode=False, tz=TZ)
    assert ("~10m" in plain) == ("STALE" in states)
    assert ":10m" not in plain


@pytest.mark.parametrize("seconds,expected", [(119, "1m59s"), (120, "2m"), (3052, "51m"), (214, "4m"), (97200, "1d03h")])
def test_countdowns_round_without_seconds_from_two_minutes(seconds, expected):
    from peerhub.cli.view import human_duration
    assert human_duration(seconds) == expected


@pytest.mark.parametrize("seconds,expected", [(12, "0m"), (59, "1m"), (119, "2m"), (3052, "51m")])
def test_forecast_countdowns_never_use_seconds(seconds, expected):
    from peerhub.cli.view import _forecast_duration
    assert _forecast_duration(seconds) == expected




SNAPSHOTS: dict[int, str] = {
    80: """PeerHub OK 2026-10-12 10:42+0900 .../.peerhub/core.db (explicit)
Threats: 2 threats - earliest cx/X-5H 11:33 (in 51m); cc/C-7D
PEER POOL  USAGE-bar  USED PACE(pt) RESET FORECAST                        FRESH
ag   3P-5H [........]   0%        ? 4h59m : not enough data               *59s
ag   3P-7D [######..]  74%     +1.2 1d22h = on pace                       *59s
ag   G-5H  [#.......]  16%    -28.7 2h44m = on pace                       *59s
ag   G-7D  [####....]  49%    -22.2 2d00h = on pace                       *59s
cc   C-5H  [##......]  24%    -26.4 2h28m = on pace                       *1m13s
cc   C-7D  [#######.]  83%     +4.0 1d11h ! exh Tue 13:51 (1d03h)         *1m13s
cx   X-5H  [######..]  73%    +26.0 2h38m ! exh 11:33 (51m)               *1m17s
cx   X-7D  [#.......]  16%     +3.3 6d02h : not enough data               *1m17s
Credits cx:2 available; expiry Fri 10-30 03:52 (in 17d17h)
Asks ag delivered 1h03m ago, took 14m
Asks cx delivered 1m47s ago, took 4m
Probes ag:OK cc:OK cx:OK
PACE +=faster than even | forecasts: conditional window-average | Ctrl-C quit""",
    100: """PeerHub OK 2026-10-12 10:42:24+0900 .../.peerhub/core.db (explicit)
Threats: 2 threats - earliest cx/X-5H 11:33 (in 51m); cc/C-7D
PEER POOL  USAGE-bar    USED PACE(pt) RESET FORECAST                                          FRESH
ag   3P-5H [..........]   0%        ? 4h59m : not enough data                                 *59s
ag   3P-7D [#######...]  74%     +1.2 1d22h = on pace                                         *59s
ag   G-5H  [##........]  16%    -28.7 2h44m = on pace                                         *59s
ag   G-7D  [#####.....]  49%    -22.2 2d00h = on pace                                         *59s
cc   C-5H  [##........]  24%    -26.4 2h28m = on pace                                         *1m13s
cc   C-7D  [########..]  83%     +4.0 1d11h ! exhausts Tue 13:51 (in 1d03h)                   *1m13s
cx   X-5H  [#######...]  73%    +26.0 2h38m ! exhausts 11:33 (in 51m)                         *1m17s
cx   X-7D  [##........]  16%     +3.3 6d02h : not enough data                                 *1m17s
Credits cx:2 available; expiry Fri 10-30 03:52 (in 17d17h)
Asks ag delivered 1h03m ago, took 14m
Asks cx delivered 1m47s ago, took 4m
Probes ag:OK cc:OK cx:OK
PACE + = faster than even | forecasts are conditional window-average | Ctrl-C quit""",
    120: """PeerHub OK 2026-10-12 10:42:24+0900 .../.peerhub/core.db (explicit)
Threats: 2 threats - earliest cx/X-5H 11:33 (in 51m); cc/C-7D
PEER POOL  USAGE-bar    USED PACE(pt) RESET FORECAST                                                              FRESH
ag   3P-5H [..........]   0%        ? 4h59m : not enough data                                                     *59s
ag   3P-7D [#######...]  74%     +1.2 1d22h = on pace                                                             *59s
ag   G-5H  [##........]  16%    -28.7 2h44m = on pace                                                             *59s
ag   G-7D  [#####.....]  49%    -22.2 2d00h = on pace                                                             *59s
cc   C-5H  [##........]  24%    -26.4 2h28m = on pace                                                             *1m13s
cc   C-7D  [########..]  83%     +4.0 1d11h ! exhausts Tue 13:51 (in 1d03h)                                       *1m13s
cx   X-5H  [#######...]  73%    +26.0 2h38m ! exhausts 11:33 (in 51m)                                             *1m17s
cx   X-7D  [##........]  16%     +3.3 6d02h : not enough data                                                     *1m17s
Credits cx:2 available; expiry Fri 10-30 03:52 (in 17d17h)
Asks ag delivered 1h03m ago, took 14m
Asks cx delivered 1m47s ago, took 4m
Probes ag:OK cc:OK cx:OK
PACE + = faster than even | forecasts are conditional window-average | Ctrl-C quit""",
}

RICH_SNAPSHOTS: dict[int, str] = {
    80: """PeerHub OK 2026-10-12 10:42+0900 .../.peerhub/core.db (explicit)
Threats: 2 threats - earliest cx/X-5H 11:33 (in 51m); cc/C-7D
PEER POOL  USAGE-bar  USED PACE(pt) RESET FORECAST                        FRESH
ag   3P-5H [░░░░░░░░]   0%        ? 4h59m ○ not enough data               ●59s
ag   3P-7D [██████░░]  74%     +1.2 1d22h ● on pace                       ●59s
ag   G-5H  [█░░░░░░░]  16%    -28.7 2h44m ● on pace                       ●59s
ag   G-7D  [████░░░░]  49%    -22.2 2d00h ● on pace                       ●59s
cc   C-5H  [██░░░░░░]  24%    -26.4 2h28m ● on pace                       ●1m13s
cc   C-7D  [███████░]  83%     +4.0 1d11h ▲ exh Tue 13:51 (1d03h)         ●1m13s
cx   X-5H  [██████░░]  73%    +26.0 2h38m ▲ exh 11:33 (51m)               ●1m17s
cx   X-7D  [█░░░░░░░]  16%     +3.3 6d02h ○ not enough data               ●1m17s
Credits cx:2 available; expiry Fri 10-30 03:52 (in 17d17h)
Asks ag delivered 1h03m ago, took 14m
Asks cx delivered 1m47s ago, took 4m
Probes ag:OK cc:OK cx:OK
PACE +=faster than even | forecasts: conditional window-average | Ctrl-C quit""",
    100: """PeerHub OK 2026-10-12 10:42:24+0900 .../.peerhub/core.db (explicit)
Threats: 2 threats - earliest cx/X-5H 11:33 (in 51m); cc/C-7D
PEER POOL  USAGE-bar    USED PACE(pt) RESET FORECAST                                          FRESH
ag   3P-5H [░░░░░░░░░░]   0%        ? 4h59m ○ not enough data                                 ●59s
ag   3P-7D [███████░░░]  74%     +1.2 1d22h ● on pace                                         ●59s
ag   G-5H  [██░░░░░░░░]  16%    -28.7 2h44m ● on pace                                         ●59s
ag   G-7D  [█████░░░░░]  49%    -22.2 2d00h ● on pace                                         ●59s
cc   C-5H  [██░░░░░░░░]  24%    -26.4 2h28m ● on pace                                         ●1m13s
cc   C-7D  [████████░░]  83%     +4.0 1d11h ▲ exhausts Tue 13:51 (in 1d03h)                   ●1m13s
cx   X-5H  [███████░░░]  73%    +26.0 2h38m ▲ exhausts 11:33 (in 51m)                         ●1m17s
cx   X-7D  [██░░░░░░░░]  16%     +3.3 6d02h ○ not enough data                                 ●1m17s
Credits cx:2 available; expiry Fri 10-30 03:52 (in 17d17h)
Asks ag delivered 1h03m ago, took 14m
Asks cx delivered 1m47s ago, took 4m
Probes ag:OK cc:OK cx:OK
PACE + = faster than even | forecasts are conditional window-average | Ctrl-C quit""",
    120: """PeerHub OK 2026-10-12 10:42:24+0900 .../.peerhub/core.db (explicit)
Threats: 2 threats - earliest cx/X-5H 11:33 (in 51m); cc/C-7D
PEER POOL  USAGE-bar    USED PACE(pt) RESET FORECAST                                                              FRESH
ag   3P-5H [░░░░░░░░░░]   0%        ? 4h59m ○ not enough data                                                     ●59s
ag   3P-7D [███████░░░]  74%     +1.2 1d22h ● on pace                                                             ●59s
ag   G-5H  [██░░░░░░░░]  16%    -28.7 2h44m ● on pace                                                             ●59s
ag   G-7D  [█████░░░░░]  49%    -22.2 2d00h ● on pace                                                             ●59s
cc   C-5H  [██░░░░░░░░]  24%    -26.4 2h28m ● on pace                                                             ●1m13s
cc   C-7D  [████████░░]  83%     +4.0 1d11h ▲ exhausts Tue 13:51 (in 1d03h)                                       ●1m13s
cx   X-5H  [███████░░░]  73%    +26.0 2h38m ▲ exhausts 11:33 (in 51m)                                             ●1m17s
cx   X-7D  [██░░░░░░░░]  16%     +3.3 6d02h ○ not enough data                                                     ●1m17s
Credits cx:2 available; expiry Fri 10-30 03:52 (in 17d17h)
Asks ag delivered 1h03m ago, took 14m
Asks cx delivered 1m47s ago, took 4m
Probes ag:OK cc:OK cx:OK
PACE + = faster than even | forecasts are conditional window-average | Ctrl-C quit""",
}
