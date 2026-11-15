"""#374 lane C: the activity view of the compact monitor frame, line 1 (the verdict) and the activity part of --expand."""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

import pytest

from peerhub.cli.monitor_view import render_grouped_frame, _width

NOW = 1_794_700_000.0  # 2026-11-14 23:46:40Z
SOURCES_READ = {"activity": "read", "capacity": "read", "accounts": "read", "facts": "read"}


def ask(state: str = "run", *, peer: str = "cc", profile: str | None = "cc.effort", name: str = "rank-374-r1/cc",
        lead: tuple[str, str, str] | None = ("peerhub", "cc", "1a2b3c4d"), elapsed: float | None = 41.0,
        alive: bool | None = True, alive_reason: str | None = None, ask_line: str | None = "IMPORTANCE RANKING, round 1",
        reply_line: str | None = None, note: str | None = None, age: float | None = None, exact: bool | None = None,
        superseded: bool = False, tokens: dict[str, int] | None = None, lease: float | None = 600.0,
        reasons: list[str] | None = None) -> dict[str, Any]:
    open_row = state in ("run", "overdue")
    ended = None if open_row else NOW - (age if age is not None else 120.0)
    return {"state": state, "superseded": superseded, "alive": alive if open_row else None,
            "alive_reason": alive_reason, "peer": peer, "profile": profile, "model": "model-x", "effort": "high",
            "stream": f"peer:{peer}:{name}", "name": name,
            "lead": None if lead is None else {"label": lead[0], "tool": lead[1], "id": lead[2]},
            "started_at": NOW - (elapsed or 0.0), "elapsed_s": elapsed, "ended_at": ended,
            "ended_exact": None if open_row else exact, "age_s": age if exact else None,
            "lease_left_s": lease if open_row else None, "tokens": tokens,
            "ask_line": ask_line, "reply_line": reply_line,
            "ask_line_long": None if ask_line is None else ask_line + " (long)",
            "reply_line_long": None if reply_line is None else reply_line + " (long)",
            "note": note, "reasons": reasons or []}


def pool(peer: str, name: str, remaining: float | None, *, fast: bool = False, pace: float | None = None,
         reset: float = 86400.0) -> dict[str, Any]:
    return {"observation_id": f"{peer}-{name}", "peer_id": peer, "pool_ref": f"quota:{peer}:{name}", "kind": "quota",
            "state": "MEASURED", "stored_state": "MEASURED", "age_seconds": 10, "ttl_seconds": 300,
            "measurements": {"remaining_fraction": remaining}, "last_measurements": {"remaining_fraction": remaining},
            "resets_at": NOW + reset, "projection_samples": 2 if fast else 0,
            "projection": {"state": "threat" if fast else "insufficient", "pace_pt": pace}}


def activity_data(asks: list[dict[str, Any]]) -> dict[str, Any]:
    running = [a for a in asks if a["state"] in ("run", "overdue") and not a["superseded"]]
    by_peer: dict[str, int] = {}
    for a in running:
        by_peer[a["peer"]] = by_peer.get(a["peer"], 0) + 1
    leads: dict[str, dict[str, Any]] = {}
    for a in asks:
        if a["lead"]:
            entry = leads.setdefault(a["lead"]["id"], {**a["lead"], "running": 0, "asks_24h": 0, "last_ask_at": None,
                                                       "session": None})
            entry["asks_24h"] += 1
            entry["running"] += a in running
            entry["last_ask_at"] = max(entry["last_ask_at"] or 0.0, a["started_at"])
    return {"asks": asks, "leads": list(leads.values()),
            "counts": {"running": len(running), "overdue": sum(a["state"] == "overdue" for a in running),
                       "by_peer": dict(sorted(by_peer.items())),
                       "leads": len({a["lead"]["id"] for a in running if a["lead"]})},
            "sample_size": len(asks), "truncated": False, "scopes_total": len(asks), "superseded_omitted": 0,
            "reason": None, "leads_read": {"state": "read", "unreadable": 0, "not_read": 0}}


@pytest.fixture
def frame() -> dict[str, Any]:
    asks = [ask(name="review-api", peer="cx", profile="cx.deepthink", lead=("QA", "cc", "9f9f9f9f"), elapsed=960,
                ask_line="Review phase four of the interface and every pin it moves"),
            ask(name="activity-374-cc", profile="cc.fable", elapsed=300,
                ask_line="ASSESSMENT (read-only ask in the repository at v0.24.15)"),
            ask(elapsed=41),
            ask("empty", peer="ag", profile="ag.standard", name="denied-postinstall", age=180, exact=True,
                ask_line=None, note="denied: command"),
            ask("ok", peer="ag", profile="ag.effort", name="rank-374-r1/ag", age=120, exact=True,
                reply_line="VERDICT: ALL CLOSED (C1-C5 100)"),
            ask("ok", profile="cc.fable", name="shipped15-cc2", age=1980, exact=True, reply_line="VERDICT: SHIPPED OK")]
    observations = [pool("ag", "3P-5H", .50), pool("ag", "3P-7D", .01, pace=71), pool("ag", "A-7D", .79, pace=14),
                    pool("ag", "G-5H", .40), pool("ag", "G-7D", .97, pace=-4), pool("cc", "C-5H", .90),
                    pool("cc", "C-7D", .63, pace=-20), pool("cc", "F-7D", .57, pace=-14),
                    pool("cx", "X-5H", 0.0, reset=3600), pool("cx", "X-7D", .42, fast=True, pace=31)]
    profiles = [{"profile": name, "fields": {"pools": {"value": pools}}} for name, pools in (
        ("ag.standard", ["G-5H", "G-7D"]), ("ag.argon", ["A-7D"]), ("ag.deepthink", ["3P-5H", "3P-7D"]),
        ("cc.effort", ["C-5H", "C-7D"]), ("cc.fable", ["F-7D"]), ("cx.effort", ["X-5H", "X-7D"]))]
    return {"schema_version": "2.0", "status": "OK", "read_at": NOW,
            "selected_groups": ["activity", "peers", "capacity"],
            "store_selection": {"path": "/w/.peerhub/core.db", "source": "env"},
            "_nearest_store_path": "/w/.peerhub/core.db", "configured_profiles": profiles,
            "groups": {"activity": {"status": "OK", "data": activity_data(asks)},
                       "peers": {"status": "OK", "data": {"peers": [{"peer_id": p} for p in ("ag", "cc", "cx")]}},
                       "capacity": {"status": "OK", "data": {"observations": observations, "accounts": [
                           {"peer_id": "cc", "plan": "max", "plan_status": "reported", "logged_in": True,
                            "state": "MEASURED", "age_seconds": 30}]}}},
            "errors": {}, "alert_sources": dict(SOURCES_READ), "alerts": [],
            "min_quota": {"pool": "ag 3P-7D", "left_pct": 1, "state": "low"}}


def lines_of(screen: str) -> list[str]:
    return screen.splitlines()


def section(lines: list[str], head: str) -> list[str]:
    start = next(i for i, line in enumerate(lines) if line.startswith(head))
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^(RUNNING|LEADS|RECENT|POOLS|suggest:)", lines[i])),
               len(lines))
    return lines[start:end]


@pytest.mark.parametrize("width", [100, 80])
@pytest.mark.parametrize("unicode_mode", [True, False])
def test_compact_frame_order_and_width_with_korean_text(frame, width, unicode_mode):
    frame["groups"]["activity"]["data"]["asks"][1]["ask_line"] = "평가 요청: 저장소의 활동 화면을 읽기 전용으로 검토해 주세요 그리고 결과를"
    frame["groups"]["activity"]["data"]["asks"][4]["reply_line"] = "결론: 모두 닫힘 (C1-C5 100)"
    screen = render_grouped_frame(frame, view="plain", unicode=unicode_mode, width=width, text_encoding="utf-8")
    lines = lines_of(screen)
    assert lines[0] == "OK | 3 running | 2 leads | min quota 1% ag 3P-7D low"
    assert lines[1] == "PeerHub Monitor 2026-11-14 23:46Z | DB:env | quota age 10s"
    heads = [line.split()[0] for line in lines if re.match(r"^(RUNNING|RECENT|POOLS|suggest:)", line)]
    assert heads == ["RUNNING", "RECENT", "suggest:", "POOLS"]
    assert lines[2] == "RUNNING 3 | cc 2 cx 1"
    assert all(_width(line) <= width - 1 for line in lines), screen
    korean = next(line for line in lines if "activity-374-cc" in line or "평가" in line or "?? ??" in line)
    assert korean.startswith(" ") and "cc.fable" in korean
    # A5: the plain view keeps the Korean text (the output encoding can encode it); its cut mark is `~`
    assert "평가 요청" in korean and ("…" if unicode_mode else "~") in korean
    assert "결론: 모두 닫힘" in screen
    if not unicode_mode:
        decoration = "".join(line for line in lines if "평가" not in line and "결론" not in line)
        assert decoration.isascii()  # the frame's own decoration stays ASCII
    for gone in ("ok enough", "PACE=", "BAR=", "PEERS", "ACCOUNTS", "facts ", "delivery", "Columns omitted",
                 "Legend:", "[####", "[████", "AGE", "PROFILES"):
        assert gone not in screen, gone


def test_plain_view_without_a_known_encoding_is_ascii(frame):
    frame["groups"]["activity"]["data"]["asks"][1]["ask_line"] = "평가 요청 …"
    screen = render_grouped_frame(frame, view="plain", unicode=False, width=100)
    assert screen.isascii() and "?? ?? ~" in screen  # the read model's cut mark becomes `~`, not `?`


@pytest.mark.parametrize("encoding,text,expected", [
    ("utf-8", "Ω±§ 한글 カナ 漢字 abc", "??? 한글 カナ 漢字 abc"),     # East Asian width Ambiguous becomes `?`
    ("cp949", "요청 😀 끝", "요청 ? 끝"),                             # cp949 cannot encode the emoji
    ("utf-8", "요청 😀 끝", "요청 😀 끝"),                           # a wide emoji that UTF-8 encodes stays
    ("ascii", "요청 abc", "?? abc"),                                 # an ASCII stdout keeps ASCII only
    ("utf-8", "é cafe​!", "e? cafe?!"),                   # combining and zero-width characters
    ("utf-8", "ก่า が", "ก่า が"),  # K2: Thai tone mark, Kana voicing
    ("utf-8", "\U0001f469‍\U0001f4bb ok", "\U0001f469‍\U0001f4bb ok"),  # K2: a ZWJ emoji pair keeps its joiner
])
@pytest.mark.parametrize("width", [100, 80])
def test_plain_view_keeps_encodable_non_ambiguous_text(frame, encoding, text, expected, width):
    frame["groups"]["activity"]["data"]["asks"][0]["ask_line"] = text
    screen = render_grouped_frame(frame, view="plain", unicode=False, width=width, text_encoding=encoding)
    row = next(line for line in lines_of(screen) if "review-api" in line)
    # a kept zero-width character never pushes a line over the width
    assert expected in row and all(_width(line) <= width - 1 for line in lines_of(screen))
    screen.encode(encoding)  # the frame can be written to that stream


def test_plain_expand_keeps_korean_and_fits_after_replacement(frame):
    asks = frame["groups"]["activity"]["data"]["asks"]
    asks[0].update(ask_line_long="검토 요청 " * 30 + "​" * 40, name="이름-1")
    frame["alerts"] = [{"code": "a1", "kind": "empty", "subject": "ag.standard 거부됨", "age_s": 60.0, "severity": "high"}]
    lines = lines_of(render_grouped_frame(frame, view="plain", unicode=False, width=80, expand=True,
                                          text_encoding="utf-8"))
    assert lines[0].startswith("ATTN 1 | empty: ag.standard 거부됨 1m")
    assert any("ask: 검토 요청" in line for line in lines) and any("name 이름-1" in line for line in lines)
    assert "empty: ag.standard 거부됨 | age 1m | high | a1" in lines
    assert all(_width(line) <= 79 for line in lines), [line for line in lines if _width(line) > 79]


def test_monitor_passes_the_output_encoding_as_the_text_capability(monkeypatch):
    import io
    import sys
    from peerhub.cli import monitor

    seen: dict[str, object] = {}
    monkeypatch.setattr("peerhub.cli.monitor_view.render_grouped_frame",
                        lambda payload, **kw: seen.update(kw) or "")
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(io.BytesIO(), encoding="cp949"))
    monitor._render_human_frame({"store_selection": {}, "groups": {}}, view="plain", width=100, height=None, color=False)
    assert seen["unicode"] is False and seen["text_encoding"] == "cp949"


def test_running_rows_order_cells_and_name_last(frame):
    lines = lines_of(render_grouped_frame(frame, view="plain", unicode=False, width=100))
    running = section(lines, "RUNNING")
    assert len(running) == 4  # the header and three rows; no flag column when no row has a flag
    first = running[1].split()
    assert first[:3] == ["16m", "cx.deepthink", "QA"] and first[-1] == "review-api"
    assert "Review phase four" in running[1]
    assert running[3].split()[:2] == ["41s", "cc.effort"] and running[3].endswith("rank-374-r1/cc")


@pytest.mark.parametrize("alive,state,flag", [(False, "run", "!gone"), (None, "run", "?"), (True, "overdue", "!overdue")])
def test_flag_column_appears_only_when_a_row_has_a_flag(frame, alive, state, flag):
    row = frame["groups"]["activity"]["data"]["asks"][2]
    row.update(alive=alive, state=state)
    lines = lines_of(render_grouped_frame(frame, view="plain", unicode=False, width=100))
    running = section(lines, "RUNNING")
    flagged = next(line for line in running if line.endswith("rank-374-r1/cc"))
    assert flagged.split()[0] == flag
    plain = running[1]
    assert plain[:1 + len(flag)].strip() == "" and plain.split()[0] == "16m"  # an empty flag cell, not a shifted row
    columns = {line.index("cc.") if "cc." in line else line.index("cx.") for line in running[1:]}
    assert len(columns) == 1  # the flag column aligns every row


def test_running_zero_is_printed_and_recent_is_omitted_without_ended_asks(frame):
    frame["groups"]["activity"]["data"] = activity_data([])
    frame["min_quota"] = None
    lines = lines_of(render_grouped_frame(frame, view="plain", unicode=False, width=100))
    assert lines[0] == "OK | 0 running | 0 leads"
    assert "RUNNING 0" in lines
    assert not any(line.startswith("RECENT") for line in lines)


def test_running_caps_at_five_rows_with_the_total_before_the_cap(frame):
    asks = [ask(name=f"task-{i}", elapsed=100 - i, peer="cc" if i % 2 else "cx", profile=None) for i in range(7)]
    frame["groups"]["activity"]["data"] = activity_data(asks)
    lines = lines_of(render_grouped_frame(frame, view="plain", unicode=False, width=100))
    running = section(lines, "RUNNING")
    assert running[0] == "RUNNING 7 | cc 3 cx 4"
    assert len(running) == 7 and running[-1] == "+2 more (--expand)"
    assert "cx" in running[1].split()  # the peer stands in for an unknown profile


def test_recent_shows_two_rows_state_age_and_note_without_reply(frame):
    data = frame["groups"]["activity"]["data"]
    data["asks"][3].update(ended_exact=False, age_s=None)
    lines = lines_of(render_grouped_frame(frame, view="plain", unicode=False, width=100))
    recent = section(lines, "RECENT")
    assert recent[0] == "RECENT 3"
    assert len(recent) == 3
    assert recent[1].split()[:3] == ["empty", "?", "ag.standard"]
    assert "(denied: command)" in recent[1] and recent[1].endswith("denied-postinstall")
    assert recent[2].split()[:3] == ["ok", "2m", "ag.effort"] and "VERDICT: ALL CLOSED" in recent[2]


def test_an_overdue_row_past_the_alert_window_is_an_ended_row(frame):
    # The read model ends an overdue row 30 minutes after its lease expired (ended_at set): RECENT, not RUNNING.
    asks = frame["groups"]["activity"]["data"]["asks"]
    late = ask("overdue", name="late-one", elapsed=4000.0, lease=-2000.0)
    late.update(ended_at=NOW - 200.0, ended_exact=False)
    frame["groups"]["activity"]["data"] = activity_data([*asks, late])
    frame["groups"]["activity"]["data"]["counts"]["running"] = 3  # what the read model counts
    lines = lines_of(render_grouped_frame(frame, view="plain", unicode=False, width=100))
    assert not any("late-one" in line for line in section(lines, "RUNNING"))
    assert section(lines, "RECENT")[0] == "RECENT 4"


def test_request_line_is_at_most_60_characters(frame):
    frame["groups"]["activity"]["data"]["asks"][0]["ask_line"] = "x" * 200
    lines = lines_of(render_grouped_frame(frame, view="plain", unicode=False, width=100))
    row = next(line for line in lines if "review-api" in line)
    assert max(len(word) for word in row.split()) <= 60


@pytest.mark.parametrize("width", [40, 52, 60])
def test_narrow_activity_rows_drop_columns_from_the_right(frame, width):
    lines = lines_of(render_grouped_frame(frame, view="plain", unicode=False, width=width))
    running = section(lines, "RUNNING")
    assert all(_width(line) <= width - 1 for line in running)
    assert all(line.split()[0] in {"16m", "5m", "41s"} for line in running[1:])
    assert "~" not in "".join(line.split()[1] for line in running[1:])


@pytest.mark.parametrize("kind,subject,age,expected", [
    ("store", "activity: OperationalError", None, "store: activity: OperationalError"),
    ("gone", "cc.effort rank-374-r1/cc", 41.0, "gone: cc.effort rank-374-r1/cc 41s"),
    ("overdue", "cx.deepthink review-api", 300.0, "overdue: cx.deepthink review-api 5m"),
    ("empty", "ag.standard denied-postinstall", 180.0, "empty: ag.standard denied-postinstall 3m"),
    ("error", "ag.effort a/b", 60.0, "error: ag.effort a/b 1m"),
    ("unsure", "cx.effort c", None, "unsure: cx.effort c"),
    ("login", "cx", None, "login: cx"),
    ("token_expired", "cc", 7200.0, "token_expired: cc 2h00m"),
    ("pool_out", "cx X-5H", None, "pool_out: cx X-5H"),
    ("token_expiring", "cc", None, "token_expiring: cc"),
    ("facts", "ag: version_drift", None, "facts: ag: version_drift"),
])
def test_every_alert_kind_is_named_on_line_1(frame, kind, subject, age, expected):
    frame["alerts"] = [{"code": "a1", "kind": kind, "subject": subject, "age_s": age, "severity": "high"}]
    line = lines_of(render_grouped_frame(frame, view="plain", unicode=False, width=100))[0]
    assert line == f"ATTN 1 | {expected} | 3 running | 2 leads | min quota 1% ag 3P-7D low"


def test_attn_total_names_two_alerts_in_order_and_counts_the_rest(frame):
    frame["alerts"] = [{"code": "a2", "kind": "gone", "subject": "cc.effort rank-374-r1/cc", "age_s": 41.0, "severity": "high"},
                       {"code": "a1", "kind": "empty", "subject": "ag.standard denied-postinstall", "age_s": 180.0,
                        "severity": "high"},
                       {"code": "a3", "kind": "pool_out", "subject": "cx X-5H", "age_s": None, "severity": "high"}]
    line = lines_of(render_grouped_frame(frame, view="plain", unicode=False, width=100))[0]
    assert line.startswith("ATTN 3 | gone: cc.effort rank-374-r1/cc 41s | empty: ag.standard denied-postinstall 3m | +1")


@pytest.mark.parametrize("width,expected", [
    (130, "ATTN 2 | gone: cc.effort rank-374-r1/cc 41s | pool_out: cx X-5H | 3 running | 2 leads | min quota 1% ag 3P-7D low"),
    (90, "ATTN 2 | gone: cc.effort rank-374-r1/cc 41s | pool_out: cx X-5H | 3 running | 2 leads"),
    (77, "ATTN 2 | gone: cc.effort rank-374-r1/cc 41s | pool_out: cx X-5H | 3 running"),
    (65, "ATTN 2 | gone: cc.effort rank-374-r1/cc 41s | pool_out: cx X-5H"),
    (50, "ATTN 2 | gone: cc.effort rank-374-r1/cc 41s | +1"),
    (30, "ATTN 2 | gone: cc.effor… | +1"),
])
def test_right_hand_fields_drop_before_alert_names(frame, width, expected):
    frame["alerts"] = [{"code": "a2", "kind": "gone", "subject": "cc.effort rank-374-r1/cc", "age_s": 41.0, "severity": "high"},
                       {"code": "a3", "kind": "pool_out", "subject": "cx X-5H", "age_s": None, "severity": "high"}]
    line = lines_of(render_grouped_frame(frame, view="plain", unicode=True, width=min(width, 100) if width <= 100 else 100))[0]
    if width > 100:  # the compact frame is never wider than 100 columns; the full line needs 113
        assert line == "ATTN 2 | gone: cc.effort rank-374-r1/cc 41s | pool_out: cx X-5H | 3 running | 2 leads"
    else:
        assert line == expected
        assert _width(line) <= width - 1


def test_agy_token_stop_is_one_more_alert_before_the_low_ones(frame):
    frame["refresh_state"] = "STOPPED · AGY TOKEN USE"
    frame["alerts"] = [{"code": "a5", "kind": "token_expiring", "subject": "cc", "age_s": None, "severity": "low"}]
    lines = lines_of(render_grouped_frame(frame, view="plain", unicode=False, width=100))
    assert lines[0].startswith("ATTN 2 | agy stop: next: --allow-agy-token-use to continue | token_expiring: cc")
    assert "STOPPED" in lines[1]
    assert sum("--allow-agy-token-use" in line for line in lines) == 1


@pytest.mark.parametrize("sources,expected", [
    ({"activity": "not_selected"}, "OK? (asks not read)"),
    ({"activity": "error"}, "OK? (asks read failed)"),
    ({"activity": "truncated"}, "OK? (asks partial)"),
    ({"capacity": "not_selected", "accounts": "not_selected", "facts": "not_selected"},
     "OK? (quota, accounts, facts not read)"),
    ({"activity": "not_selected", "accounts": "truncated"}, "OK? (asks not read; accounts partial)"),
    ({"activity": "not_selected", "hint": "peerhub monitor defaults reset"},
     "OK? (asks not read; peerhub monitor defaults reset)"),
    ({"activity": "not_selected", "capacity": "stale"}, "OK? (asks not read; quota stale)"),
])
def test_ok_needs_every_alert_source_read(frame, sources, expected):
    frame["alert_sources"].update(sources)
    frame["groups"].pop("activity")
    frame["selected_groups"] = ["capacity"]
    frame["min_quota"] = None
    assert lines_of(render_grouped_frame(frame, view="plain", unicode=False, width=100))[0] == expected


@pytest.mark.parametrize("low,expected", [
    ({"pool": "ag 3P-7D", "left_pct": 1, "state": "low", "aged": True}, "min quota 1% ag 3P-7D low (aged)"),
    ({"pool": "ag 3P-7D", "left_pct": 40, "state": "aged", "aged": True}, "min quota 40% ag 3P-7D aged"),
    ({"pool": "ag 3P-7D", "left_pct": 1, "state": "low", "aged": False}, "min quota 1% ag 3P-7D low"),
    ({"pool": "ag 3P-7D", "left_pct": 1, "state": "low"}, "min quota 1% ag 3P-7D low"),  # an older read model
])
def test_min_quota_says_when_its_pool_is_aged(frame, low, expected):
    # A6: `aged` is true when the pool's observation is stale; the state word is what POOLS shows
    frame["min_quota"] = low
    assert lines_of(render_grouped_frame(frame, view="plain", unicode=False, width=100))[0].endswith(expected)


@pytest.mark.parametrize("sources,head", [({}, "ATTN 1 |"), ({"activity": "not_selected"}, "ATTN 1? |"),
                                          ({"capacity": "stale"}, "ATTN 1? |"), ({"accounts": "error"}, "ATTN 1? |")])
def test_attn_carries_a_question_mark_when_a_source_was_not_read(frame, sources, head):
    # A7: the same question mark as `OK?`; --expand lists each source that was not read, with its reason
    frame["alert_sources"].update(sources)
    frame["alerts"] = [{"code": "a3", "kind": "pool_out", "subject": "cx X-5H", "age_s": None, "severity": "high"}]
    assert lines_of(render_grouped_frame(frame, view="plain", unicode=False, width=100))[0].startswith(head)
    expanded = lines_of(render_grouped_frame(frame, view="plain", unicode=False, width=120, expand=True))
    alerts = expanded[next(i for i, line in enumerate(expanded) if "ALERTS 1" in line):]
    alerts = alerts[1:next(i for i, line in enumerate(alerts) if i and line.startswith("-- "))]
    reasons = {"not_selected": "not selected", "stale": "stale", "error": "read failed"}
    assert alerts[0].startswith("pool_out: cx X-5H")
    assert alerts[1:] == [f"source {key}: {reasons[value]}" for key, value in sources.items()]


def test_expand_lists_unread_sources_without_alerts_and_the_hint(frame):
    frame["alert_sources"].update(activity="not_selected", hint="peerhub monitor defaults reset")
    expanded = render_grouped_frame(frame, view="plain", unicode=False, width=120, expand=True)
    assert expanded.startswith("OK? (asks not read; peerhub monitor defaults reset)")
    assert "ALERTS 0" in expanded and "source activity: not selected" in expanded
    assert "hint: peerhub monitor defaults reset" in expanded


def test_expand_says_alerts_were_not_read_when_the_frame_has_no_alert_sources(frame):
    # the SchemaVersionError payload of monitor.py carries no alert_sources: line 1 and --expand agree
    frame.pop("alert_sources")
    expanded = render_grouped_frame(frame, view="plain", unicode=False, width=120, expand=True)
    assert expanded.startswith("OK? (alerts not read)") and "source alerts: not read" in expanded.splitlines()


@pytest.mark.parametrize("reasons", [{"capacity": "no quota evidence"}, "garbage", None, {"capacity": 7}])
def test_expand_prints_the_read_model_reason_of_a_source_that_was_not_read(frame, reasons):
    # lane B fix 5: a store that never had a quota refresh reports `no_evidence` with a reason for capacity
    frame["alert_sources"] = {"activity": "read", "capacity": "no_evidence", "accounts": "no_evidence",
                              "facts": "no_evidence", **({"reasons": reasons} if reasons is not None else {})}
    lines = lines_of(render_grouped_frame(frame, view="plain", unicode=False, width=120, expand=True))
    assert lines[0].startswith("OK? (quota, accounts, facts not read)")  # line 1 stays as it is
    with_reason = reasons == {"capacity": "no quota evidence"}
    assert ("source capacity: no quota evidence" if with_reason else "source capacity: not read") in lines
    assert "source accounts: not read" in lines and "source facts: not read" in lines


def test_missing_alert_sources_never_claim_ok(frame):
    frame.pop("alert_sources")
    assert lines_of(render_grouped_frame(frame, view="plain", unicode=False, width=100))[0].startswith("OK? (alerts not read)")


def test_pools_classes_five_hour_rule_and_columns(frame):
    lines = lines_of(render_grouped_frame(frame, view="plain", unicode=False, width=100))
    pools = section(lines, "POOLS")
    assert pools[0].split() == ["POOLS", "8", "STATE", "LEFT", "RESET", "PACE", "FAMILY"]
    names = [re.search(r"\b((?:3P|[A-Z])-\d+[HD])\b", line)[1] for line in pools[1:]]
    # K3/L6: a 7-day pool takes the most severe class of itself and its shown 5-hour row: X-7D (fast) with X-5H out
    # is class out, so it comes first; then 3P-7D (low), G-7D (binding), the rest.
    assert names == ["X-7D", "X-5H", "3P-7D", "G-7D", "G-5H", "A-7D", "C-7D", "F-7D"]
    assert pools[3].split()[:5] == ["ag", "3P-7D", "low", "1%", "1d00h"]
    assert pools[3].split()[5:] == ["^+71", "claude-via-ag"]  # the direction glyph stays (ASCII here)
    assert pools[5].lstrip().startswith("`- G-5H")  # the binding 5-hour sub-row follows its 7-day pool
    assert pools[2].split()[1:4] == ["X-5H", "out", "0%"]
    assert "fast" in pools[1].split()
    assert "" not in pools and "3P-5H" not in "".join(pools) and "C-5H" not in "".join(pools)
    header = pools[0]
    assert header.index("STATE") < header.index("LEFT") < header.index("RESET") < header.index("PACE")


@pytest.mark.parametrize("child,other", [
    (0.05, .03),  # a low 5-hour row lifts its ok 7-day pool into class low: ag sorts before the low cc pool
    (0.0, 0.0),   # an out 5-hour row lifts it into class out, beside an out cc pool
])
def test_a_seven_day_pool_takes_the_most_severe_class_of_its_shown_five_hour_row(frame, child, other):
    # K3/L6: without this rule G-7D would be class binding and sort after the cc pool of class low or out.
    frame["groups"]["capacity"]["data"]["observations"] = [
        pool("ag", "G-7D", .50), pool("ag", "G-5H", child), pool("cc", "C-7D", other)]
    pools = section(lines_of(render_grouped_frame(frame, view="plain", unicode=False, width=100)), "POOLS")
    assert [re.search(r"\b((?:3P|[A-Z])-\d+[HD])\b", line)[1] for line in pools[1:]] == ["G-7D", "G-5H", "C-7D"]


def test_a_long_na_note_is_cut_instead_of_dropping_the_family_column(frame):
    na = pool("cx", "X-30D", None)
    na.update(state="ABSENT", stored_state="ABSENT", measurements=None, last_measurements=None,
              reason="not reported by provider; plan=" + "enterprise-" * 8)
    observations = frame["groups"]["capacity"]["data"]["observations"]
    observations[:] = [o for o in observations if o["peer_id"] != "cc"] + [na]  # 7 rows: within the cap of 8
    lines = lines_of(render_grouped_frame(frame, view="plain", unicode=False, width=80))
    pools = section(lines, "POOLS")
    assert pools[0].split()[-1] == "FAMILY"
    row = next(line for line in pools if "X-30D" in line)
    assert "codex; not reported by plan enter" in row and row.endswith("~") and _width(row) <= 79
    assert next(line for line in pools if "3P-7D" in line).endswith("claude-via-ag")  # short cells stay whole


def test_pools_cap_at_eight_rows_with_an_overflow_line(frame):
    observations = frame["groups"]["capacity"]["data"]["observations"]
    observations.extend(pool("zz", f"F-{i}D", .5) for i in (1, 2, 3))
    lines = lines_of(render_grouped_frame(frame, view="plain", unicode=False, width=100))
    pools = section(lines, "POOLS")
    assert pools[0].startswith("POOLS 11 ") and len(pools) == 10 and pools[-1] == "+3 pools (--expand)"


def test_suggestion_belongs_to_capacity_and_wraps_to_two_lines(frame):
    lines = lines_of(render_grouped_frame(frame, view="plain", unicode=False, width=50))
    start = next(i for i, line in enumerate(lines) if line.startswith("suggest:"))
    assert lines[start + 2].startswith("POOLS") or lines[start + 1].startswith("POOLS")
    frame["selected_groups"] = ["activity"]
    assert "suggest:" not in render_grouped_frame(frame, view="plain", unicode=False, width=100)


def test_group_capacity_prints_lines_1_2_the_suggestion_and_pools(frame):
    frame["selected_groups"] = ["capacity"]
    frame["groups"].pop("activity")
    frame["alert_sources"]["activity"] = "not_selected"
    lines = lines_of(render_grouped_frame(frame, view="plain", unicode=False, width=100))
    assert lines[0] == "OK? (asks not read) | min quota 1% ag 3P-7D low"
    assert lines[1].startswith("PeerHub Monitor")
    assert lines[2].startswith("suggest:") and lines[3].startswith("POOLS 8")
    assert len(lines) == 12
    assert not any(line.startswith(("RUNNING", "RECENT")) for line in lines)


def test_activity_read_error_is_disclosed_in_the_running_header(frame):
    frame["groups"]["activity"] = {"status": "ERROR", "data": None}
    frame["alert_sources"]["activity"] = "error"
    frame["alerts"] = [{"code": "a6", "kind": "store", "subject": "activity: OperationalError", "age_s": None,
                        "severity": "high"}]
    lines = lines_of(render_grouped_frame(frame, view="plain", unicode=False, width=100))
    assert lines[0].startswith("ATTN 1? | store: activity: OperationalError | min quota")  # A7: activity not read
    assert "RUNNING ? | activity read failed" in lines


def test_expand_lists_every_field_of_asks_leads_and_alerts(frame):
    asks = frame["groups"]["activity"]["data"]["asks"]
    asks[0].update(alive=None, alive_reason="other host", tokens={"input": 1200, "output": 300})
    asks[3].update(reasons=["caller unreadable"])
    asks.append(ask("unsure", name="old-one", superseded=True, note="superseded", age=4000, exact=True))
    frame["groups"]["activity"]["data"] = activity_data(asks)
    frame["alerts"] = [{"code": "a1", "kind": "empty", "subject": "ag.standard denied-postinstall", "age_s": 180.0,
                        "severity": "high"},
                       {"code": "a6", "kind": "facts", "subject": "ag: version_drift", "age_s": None, "severity": "low"}]
    screen = render_grouped_frame(frame, view="plain", unicode=False, width=120, expand=True)
    flat = " ".join(screen.split())
    lines = lines_of(screen)
    assert lines[0].startswith("ATTN 2 | empty: ag.standard denied-postinstall 3m | facts: ag: version_drift")
    assert lines[1].startswith("PeerHub Monitor")
    order = [next(i for i, line in enumerate(lines) if marker in line) for marker in ("ALERTS", "ACTIVITY [", "QUOTA [")]
    assert order == sorted(order)
    for text in ("empty: ag.standard denied-postinstall | age 3m | high | a1", "facts: ag: version_drift | age - | low | a6",
                 "flag ? (other host)", "state run", "elapsed 16m", "started 2026-11-14 23:30:40Z", "lease left 10m",
                 "profile cx.deepthink", "model model-x", "effort high", "lead QA (cc 9f9f9f9f)", "name review-api",
                 "stream peer:cx:review-api", "tokens in 1,200 out 300",
                 "ask: Review phase four of the interface and every pin it moves (long)",
                 "state empty", "age 3m", "note denied: command", "reasons caller unreadable",
                 "reply: VERDICT: ALL CLOSED (C1-C5 100) (long)", "superseded", "name old-one",
                 "LEADS 2", "PACE=used%-elapsed% pp", "header time is the snapshot time"):
        assert text in flat, text
    # #376: the lead line is longer than the width and wraps between whole fields, so compare without separators
    assert ("QA cc 9f9f9f9f no session found (ended, or its Claude folder is not in PEERHUB_LEAD_DIRS) running 1"
            " asks 24h 1 last ask 2026-11-14 23:30:40Z") in flat.replace(" | ", " ")
    assert "RUNNING 3 | cc 2 cx 1" in flat and "RECENT 3" in flat
    assert all(_width(line) <= 119 for line in lines)


def test_expand_without_alerts_prints_no_alerts_section_and_keeps_existing_groups(frame):
    screen = render_grouped_frame(frame, view="plain", unicode=False, width=120, expand=True)
    assert screen.startswith("OK | ") and "ALERTS" not in screen
    assert "PEERS [" in screen and "ACCOUNTS" in screen


def test_bounded_expand_keeps_the_verdict_first_and_the_height(frame):
    for height in range(3, 40):
        screen = render_grouped_frame(deepcopy(frame), view="plain", unicode=False, width=100, height=height, expand=True)
        lines = lines_of(screen)
        assert lines[0].startswith("OK | 3 running") and len(lines) < height, (height, screen)
        assert all(_width(line) < 100 for line in lines)


def test_monitor_passes_the_preference_source_to_the_read_model(tmp_path, monkeypatch, capsys):
    from peerhub.cli.app import main
    from tests.communication.harness.observation import ObservationHarness

    h = ObservationHarness(tmp_path / "pref")
    seen: list[object] = []

    def render(db, groups, **kw):
        seen.append(kw.get("preference_source"))
        return {"schema_version": "2.0", "status": "OK", "read_at": NOW, "groups": {}, "errors": {},
                "alert_sources": dict(SOURCES_READ), "alerts": [], "min_quota": None}

    monkeypatch.setattr("peerhub.extensions.monitor_read_model.render_monitor_frame", render)
    assert main(["--db", str(h.db_path), "monitor", "--snapshot", "--group", "capacity", "--view", "plain"]) == 0
    assert seen == ["command"]
    capsys.readouterr()
