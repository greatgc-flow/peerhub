"""The LEADS block of the compact monitor frame, its fit order, the --expand rows and JSON (#376 design v5 3.4; tests 14-17)."""
from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from peerhub.cli.monitor_view import CompactFrame, MONITOR_LEGEND, fit_compact_frame, render_grouped_frame, _width
from tests.communication.diag.test_monitor_activity import db  # noqa: F401  (a fixture: a store with the Bridge tables)
from tests.communication.diag.test_monitor_activity_view import NOW, activity_data, ask, frame  # noqa: F401

READ = {"state": "read", "unreadable": 0, "not_read": 0}
TITLE = "Release notes for the spring update"


def sess(**over: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": "1a2b3c4d", "tool": "cc", "pid": 4242, "alive": True, "entry": "cli", "name": "docs-site-5a",
        "folder": "docs-site", "cwd": "D:/work/docs-site", "state": "busy", "state_since": NOW - 1811,
        "started_at": NOW - 51_400, "version": "2.1.323", "active_at": NOW - 12, "model": "claude-fable-5-2",
        "context_tokens": 106_137, "context_window": 1_000_000, "context_pct": 10, "context_at": NOW - 92,
        "title": TITLE, "prompt_line": "Add the section about the importer",
        "said_line": "The importer section is drafted; running the link check now."}
    item.update(over)
    return item


def lead(label: str, *, tool: str = "cc", lead_id: str = "1a2b3c4d", running: int = 0, asks: int = 0,
         last: float | None = None, session: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"label": label, "tool": tool, "id": lead_id, "running": running, "asks_24h": asks, "last_ask_at": last,
            "session": session}


def with_leads(payload: dict[str, Any], leads: list[dict[str, Any]], read: dict[str, Any] | None = READ) -> dict[str, Any]:
    data = payload["groups"]["activity"]["data"]
    data["leads"] = leads
    data["leads_read"] = read
    return payload


def screen(payload: dict[str, Any], width: int = 100, unicode: bool = False, **kw: Any) -> list[str]:
    return render_grouped_frame(payload, view="plain", unicode=unicode, width=width, **kw).splitlines()


def leads_block(lines: list[str]) -> list[str]:
    start = next(i for i, line in enumerate(lines) if line.startswith("LEADS"))
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^(RUNNING|RECENT|POOLS|suggest:)", lines[i])),
               len(lines))
    return lines[start:end]


# 14. the compact LEADS block ----------------------------------------------------------------------------------------
def test_leads_sit_between_running_and_recent_with_the_column_order(frame):
    with_leads(frame, [lead("docs-site", session=sess())])
    lines = screen(frame)
    heads = [line.split()[0] for line in lines if re.match(r"^(RUNNING|LEADS|RECENT|POOLS|suggest:)", line)]
    assert heads == ["RUNNING", "LEADS", "RECENT", "suggest:", "POOLS"]
    block = leads_block(lines)
    assert block[0] == "LEADS 1"
    assert block[1].split() == ["docs-site", "busy", "12s", "ctx", "10%", "0", "run", "fable-5-2", *TITLE.split()]


@pytest.mark.parametrize(("over", "cell"), [
    ({"context_pct": 11}, "ctx 11%"), ({"context_pct": 83}, "ctx 83%!"), ({"context_pct": 80}, "ctx 80%!"),
    ({"context_pct": None, "context_window": None}, "ctx 106k"),
    ({"context_pct": None, "context_window": None, "context_tokens": None}, "ctx ?"),
])
def test_the_four_ctx_forms(frame, over, cell):
    with_leads(frame, [lead("docs-site", session=sess(**over))])
    row = leads_block(screen(frame))[1]
    assert f"  {cell}  " in row


@pytest.mark.parametrize(("over", "state"), [({"alive": None}, "?busy"), ({"state": None}, "?"),
                                             ({"state": None, "alive": None}, "?")])
def test_the_state_flag(frame, over, state):
    with_leads(frame, [lead("docs-site", session=sess(**over))])
    assert leads_block(screen(frame))[1].split()[1] == state


def test_a_lead_with_a_running_ask_and_no_session_shows_dashes(frame):
    with_leads(frame, [lead("ci-box", tool="?", running=2)])
    row = leads_block(screen(frame))[1]
    assert row.split() == ["ci-box", "-", "-", "-", "2", "run", "-"]


def test_an_unknown_activity_time_is_a_dash(frame):
    with_leads(frame, [lead("docs-site", session=sess(active_at=None))])
    assert leads_block(screen(frame))[1].split()[2] == "-"


def test_three_rows_then_the_overflow_line_in_read_model_order(frame):
    leads = [lead(f"lead-{i}", lead_id=f"0000000{i}", session=sess(active_at=NOW - 60 * i)) for i in range(5)]
    with_leads(frame, leads)
    block = leads_block(screen(frame))
    assert block[0] == "LEADS 5" and [row.split()[0] for row in block[1:4]] == ["lead-0", "lead-1", "lead-2"]
    assert block[4] == "+2 leads (--expand)" and len(block) == 5


def test_only_leads_with_a_session_or_a_running_ask_are_rows(frame):
    with_leads(frame, [lead("old", asks=3, last=NOW - 4000), lead("live", session=sess()), lead("busy", running=1)])
    block = leads_block(screen(frame))
    assert block[0] == "LEADS 2" and [row.split()[0] for row in block[1:]] == ["live", "busy"]


@pytest.mark.parametrize(("read", "header"), [
    (READ, "LEADS 1"),
    ({"state": "partial", "unreadable": 1, "not_read": 2}, "LEADS 1 | partial (1 unreadable, 2 not read)"),
    ({"state": "partial", "unreadable": 0, "not_read": 2}, "LEADS 1 | partial (2 not read)"),
    ({"state": "error", "unreadable": 0, "not_read": 0}, "LEADS 1 | read failed"),
])
def test_the_header_forms(frame, read, header):
    with_leads(frame, [lead("live", session=sess())], read)
    assert leads_block(screen(frame))[0] == header


@pytest.mark.parametrize(("read", "header"), [({"state": "error", "unreadable": 0, "not_read": 0}, "LEADS ? | read failed"),
                                              ({"state": "partial", "unreadable": 3, "not_read": 0},
                                               "LEADS ? | partial (3 unreadable)")])
def test_with_no_row_to_show_the_count_is_a_question_mark(frame, read, header):
    with_leads(frame, [lead("old", asks=3, last=NOW - 4000)], read)
    assert leads_block(screen(frame)) == [header]


@pytest.mark.parametrize("read", [READ, None])
def test_the_block_is_omitted_when_there_is_nothing_to_show(frame, read):
    with_leads(frame, [lead("old", asks=3, last=NOW - 4000)], read)
    assert not any(line.startswith("LEADS") for line in screen(frame))


def test_the_block_is_omitted_without_activity(frame):
    with_leads(frame, [lead("live", session=sess())])
    frame["selected_groups"] = ["capacity"]
    assert not any(line.startswith("LEADS") for line in screen(frame))


def test_a_long_label_is_cut_to_sixteen_characters(frame):
    with_leads(frame, [lead("a" * 30, session=sess())])
    assert leads_block(screen(frame))[1].split()[0] == "a" * 15 + "~"


def test_a_long_model_is_cut_and_loses_its_claude_prefix(frame):
    with_leads(frame, [lead("x", session=sess(model="claude-" + "m" * 30))])
    assert leads_block(screen(frame))[1].split()[7] == "m" * 15 + "~"


def test_at_40_columns_ctx_survives_and_run_model_and_title_drop(frame):
    with_leads(frame, [lead("l" * 14, session=sess(alive=None, active_at=NOW - 720, context_pct=100))])
    row = leads_block(screen(frame, width=40))[1]
    assert row.split() == ["l" * 14, "?busy", "12m", "ctx", "100%!"] and _width(row) <= 39


@pytest.mark.parametrize("width", [100, 60])
def test_widths_100_and_60(frame, width):
    with_leads(frame, [lead("docs-site", session=sess()), lead("shop-4f", running=2, session=sess(alive=None, context_pct=83))])
    lines = screen(frame, width=width)
    block = leads_block(lines)
    assert len(block) == 3 and all(_width(line) <= width - 1 for line in lines)
    assert all("ctx" in row for row in block[1:])


def test_the_title_column_is_absent_with_text_off(frame):
    with_leads(frame, [lead("docs-site", session=sess(title=None, name=None))])
    assert leads_block(screen(frame))[1].split() == ["docs-site", "busy", "12s", "ctx", "10%", "0", "run", "fable-5-2"]


def test_the_ascii_view(frame):
    with_leads(frame, [lead("리드" * 10, session=sess(title="평가 " * 30))])
    text = "\n".join(screen(frame))
    assert text.isascii() and "~" in leads_block(text.splitlines())[1]
    unicode = leads_block(screen(frame, unicode=True))
    assert "…" in unicode[1]


def test_running_header_and_line_1_are_unchanged_by_sessions(frame):
    before = screen(deepcopy(frame))
    with_leads(frame, [lead("docs-site", session=sess()), *frame["groups"]["activity"]["data"]["leads"]])
    after = screen(frame)
    assert after[0] == before[0] and after[2] == before[2]


# 15. fit order ------------------------------------------------------------------------------------------------------
def parts(**over: Any) -> CompactFrame:
    base: dict[str, Any] = dict(
        verdict="OK | 9 running | 1 lead",
        header=["PeerHub Monitor 2026-11-14 01:46Z | DB:env | quota age 10s"],
        running=["RUNNING 9 | cc 9", *[f" {i}m  cc.effort  lead  request {i}  task-{i}" for i in range(5)]],
        running_total=9,
        leads=["LEADS 5", *[f" lead-{i}  busy  1m  ctx 10%  0 run  fable-5-2  title" for i in range(3)]],
        leads_total=5,
        recent=["RECENT 4", " ok  1m  cc.effort  lead  reply a  done-a", " ok  2m  cc.effort  lead  reply b  done-b"],
        suggest=["suggest: eco=cc.standard normal=cc.effort", "  quality=cc.deepthink top=cc.fable"],
        pools=["POOLS 10  STATE LEFT", *[f" cc P{i}-7D  ok  50%" for i in range(8)]],
        pools_total=10)
    base.update(over)
    return CompactFrame(**base)


def fitted(height: int | None, max_lines: int = 24, **over: Any) -> list[str]:
    return fit_compact_frame(parts(**over), width=99, height=height, unicode=True, max_lines=max_lines).splitlines()


def test_the_unbounded_frame_with_leads_keeps_24_lines():
    lines = fitted(None)  # 29 lines before fitting: RECENT goes to one row, LEADS to one row, RUNNING to three
    assert len(lines) == 24
    i = lines.index("LEADS 5")
    assert lines[i - 1] == "+6 more (--expand)" and lines[i + 1].startswith(" lead-0") and lines[i + 2] == "+4 leads (--expand)"
    assert lines[i + 3] == "RECENT 4" and lines[i + 4].endswith("done-a") and lines[i + 5].startswith("suggest:")


def test_each_step_removes_in_the_one_order_with_leads():
    previous = None
    seen: list[tuple[int, ...]] = []
    for height in range(30, 1, -1):
        lines = fitted(height, max_lines=40)  # above the 24-line bound, so every step shows
        assert len(lines) <= height - 1
        state = (sum("done-" in line for line in lines), sum(line.startswith(" lead-") for line in lines),
                 sum("task-" in line for line in lines), sum(line.startswith(" cc P") for line in lines),
                 sum(line.startswith(("suggest:", "  quality")) for line in lines),
                 int(any(line.startswith("RECENT") for line in lines)), int(any(line.startswith("LEADS") for line in lines)),
                 int(any(line.startswith("POOLS") for line in lines)), int(any(line.startswith("RUNNING") for line in lines)))
        if previous is not None:
            assert all(now <= before for now, before in zip(state, previous)), (height, lines)
        previous = state
        seen.append(state)
    rows = [s[:5] for s in seen]
    # RECENT rows down to one, then LEADS rows down to one, then RUNNING rows after the third, then POOLS after the fourth
    assert (1, 3, 5, 8, 2) in rows and (1, 1, 5, 8, 2) in rows and (1, 1, 3, 8, 2) in rows and (1, 1, 3, 4, 2) in rows
    assert (1, 1, 3, 4, 1) in rows and (1, 1, 0, 4, 1) in rows and (1, 1, 0, 0, 1) in rows
    # the RECENT header goes before the LEADS header, and the LEADS header before the first suggestion line
    assert (0, 1, 0, 0, 1) in rows and (0, 0, 0, 0, 1) in rows and (0, 0, 0, 0, 0) in rows
    heads = [s[5:] for s in seen]
    assert (0, 1, 1, 1) in heads and (0, 0, 1, 1) in heads


def test_the_removal_that_creates_the_leads_overflow_line_removes_two_rows():
    base: dict[str, Any] = dict(running=None, running_total=0, recent=None, suggest=[], pools=None, pools_total=0,
                                leads=["LEADS 3", " a", " b", " c"], leads_total=3)
    full = fitted(None, **base)
    assert len(full) == 6 and not any(line.startswith("+") for line in full)
    lines = fitted(len(full), **base)
    assert lines[-2:] == [" a", "+2 leads (--expand)"] and len(lines) == len(full) - 1


TWO_LEADS: dict[str, Any] = dict(leads=["LEADS 2", " lead-0  busy  1m  ctx 10%", " lead-1  busy  2m  ctx 20%"],
                                 leads_total=2)


def test_two_lead_rows_stay_while_running_goes_to_three_rows():
    # A-3: removing one of two rows would create `+1 leads`, so K5 takes both; a step with a floor of one or more
    # never leaves a header without a row, so it does nothing and RUNNING gives up its rows instead.
    lines = fitted(None, **TWO_LEADS)  # 27 lines before fitting
    assert len(lines) == 24
    i = lines.index("LEADS 2")
    assert lines[i + 1].startswith(" lead-0") and lines[i + 2].startswith(" lead-1")
    assert sum("task-" in line for line in lines) == 3 and "+6 more (--expand)" in lines


def test_two_lead_rows_leave_together_with_their_header():
    for height in range(2, 30):
        lines = fitted(height, max_lines=40, **TWO_LEADS)
        assert sum(line.startswith(" lead-") for line in lines) in ((0, 2) if "LEADS 2" not in lines else (2,)), (height, lines)


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5])
def test_no_height_shows_a_leads_header_without_a_row(frame, count):
    with_leads(frame, [lead(f"l{i}", session=sess()) for i in range(count)])
    for height in range(2, 30):
        lines = render_grouped_frame(deepcopy(frame), view="plain", unicode=False, width=100, height=height).splitlines()
        heads = [i for i, line in enumerate(lines) if line.startswith("LEADS")]
        assert all(i + 1 < len(lines) and lines[i + 1].startswith(" ") for i in heads), (count, height, lines)


def test_running_and_pools_steps_are_unchanged_by_the_leads_rule():
    # floors 3, 4 and 0: the K5 double removal still happens (two rows for the first overflow line)
    for key, total in (("running", 4), ("pools", 5)):
        rows = [f" row {i}" for i in range(total)]
        other = "pools" if key == "running" else "running"
        base: dict[str, Any] = {key: ["RUNNING 4" if key == "running" else "POOLS 5", *rows], f"{key}_total": total,
                                other: None, f"{other}_total": 0, "recent": None, "suggest": [], "leads": None}
        full = fitted(None, **base)
        lines = fitted(len(full), **base)
        noun = "more" if key == "running" else "pools"
        assert f"+2 {noun} (--expand)" in lines and sum(line.startswith(" row") for line in lines) == total - 2
    base = {"running": ["RUNNING 9", " r0", " r1"], "running_total": 9, "pools": None, "recent": None, "suggest": [],
            "leads": None}
    assert fitted(6, **base) == ["OK | 9 running | 1 lead", "PeerHub Monitor 2026-11-14 01:46Z | DB:env | quota age 10s",
                                 "RUNNING 9", " r0", "+8 more (--expand)"]
    assert fitted(5, **base)[-1] == "RUNNING 9"  # floor 0: RUNNING may still go down to its header


@pytest.mark.parametrize("width", [80, 100])
@pytest.mark.parametrize("unicode", [True, False])
def test_the_worst_case_frame_with_leads_stays_within_the_bounds(frame, width, unicode):
    for o in frame["groups"]["capacity"]["data"]["observations"]:
        o["last_measurements"] = {"remaining_fraction": .05}
    frame["groups"]["activity"]["data"] = activity_data(
        [ask(name="이름" * 20, elapsed=60.0 * (i + 1), ask_line="요청" * 60, lead=("리드" * 20, "cc", f"{i:08x}"))
         for i in range(9)] + [ask("ok", name=f"done-{i}", age=60, exact=True, reply_line="答" * 80) for i in range(4)])
    sessions = [lead("세션" * 20, lead_id=f"{i:08x}", running=i % 2,
                     session=sess(title="제목" * 60, model="claude-" + "모" * 30, context_pct=95, alive=None))
                for i in range(12)]
    with_leads(frame, sessions, {"state": "partial", "unreadable": 9, "not_read": 99})
    frame["alerts"] = [{"code": "a1", "kind": "empty", "subject": "가" * 120, "age_s": 1.0, "severity": "high"}] * 5
    text = render_grouped_frame(frame, view="rich" if unicode else "plain", unicode=unicode, width=width)
    lines = text.splitlines()
    assert len(lines) <= 24 and len(text.encode("utf-8")) <= 8192
    assert all(_width(line) <= min(100, width) - 1 for line in lines)
    assert any(line.startswith("LEADS 12 | partial (9 unreadable, 99 not read)") for line in lines)


@pytest.mark.parametrize("height", range(2, 9))
def test_small_heights_drop_leads_before_the_suggestion(frame, height):
    with_leads(frame, [lead(f"l{i}", session=sess()) for i in range(4)])
    lines = render_grouped_frame(frame, view="plain", unicode=False, width=100, height=height).splitlines()
    assert len(lines) <= height - 1  # a header goes with its last row, so one step can take more than the one line over

    def has(head: str) -> int:
        return int(any(line.startswith(head) for line in lines))

    # RECENT goes before LEADS, and LEADS before the first suggestion line
    assert has("RECENT") <= has("LEADS") <= has("suggest:")


# 16. --expand -------------------------------------------------------------------------------------------------------
def expanded(payload: dict[str, Any]) -> list[str]:
    return render_grouped_frame(payload, view="plain", unicode=False, width=200, expand=True).splitlines()


def test_expand_prints_every_field_of_a_lead_session(frame):
    with_leads(frame, [lead("docs-site", running=0, asks=3, last=NOW - 16_238, session=sess(context_pct=11))])
    lines = expanded(frame)
    i = lines.index("LEADS 1")
    assert lines[i + 1] == ("docs-site | cc 1a2b3c4d | busy since 23:16:29Z | active 12s ago | alive | pid 4242 | entry cli"
                            " | claude 2.1.323 | started 2026-11-14 09:30Z")
    assert lines[i + 2] == ("model claude-fable-5-2 | ctx 106,137 of 1,000,000 model max (11%) at 23:45:08Z | running 0"
                            " | asks 24h 3 | last ask 2026-11-14 19:16:02Z")
    assert lines[i + 3] == "name docs-site-5a | cwd D:/work/docs-site"
    assert lines[i + 4:i + 7] == [f"title: {TITLE}", "prompt: Add the section about the importer",
                                  "said: The importer section is drafted; running the link check now."]


def test_expand_alive_unknown_and_an_unknown_window(frame):
    with_leads(frame, [lead("shop", session=sess(alive=None, context_pct=None, context_window=None))])
    flat = " ".join(expanded(frame))
    assert "| alive ? |" in flat and "ctx 106,137 (window unknown) at 23:45:08Z" in flat


def test_expand_with_text_off_has_no_name_title_prompt_or_said(frame):
    with_leads(frame, [lead("docs-site", session=sess(name=None, title=None, prompt_line=None, said_line=None))])
    text = "\n".join(expanded(frame))
    assert "cwd D:/work/docs-site" in text
    for gone in ("name docs-site-5a", "title:", "prompt:", "said:"):
        assert gone not in text


def test_expand_the_two_no_session_wordings_and_the_header_notes(frame):
    with_leads(frame, [lead("old-lead", lead_id="9a41c0de", asks=2, last=NOW - 45_270),
                       lead("ci-box", tool="?", lead_id="51c2a9f0", running=1, asks=4, last=NOW - 60)],
               {"state": "partial", "unreadable": 1, "not_read": 2})
    lines = expanded(frame)
    i = lines.index("LEADS 2 | partial (1 unreadable, 2 not read)")
    assert lines[i + 1] == ("old-lead | cc 9a41c0de | no session found (ended, or its Claude folder is not in"
                            " PEERHUB_LEAD_DIRS) | running 0 | asks 24h 2 | last ask 2026-11-14 11:12:10Z")
    assert lines[i + 2] == ("ci-box | ? 51c2a9f0 | no session (not a Claude Code lead) | running 1 | asks 24h 4"
                            " | last ask 2026-11-14 23:45:40Z")


def test_expand_header_read_failed_and_question_mark(frame):
    with_leads(frame, [], {"state": "error", "unreadable": 0, "not_read": 0})
    assert "LEADS ? | read failed" in expanded(frame)


def test_an_older_payload_without_session_or_leads_read_still_renders(frame):
    data = frame["groups"]["activity"]["data"]
    data.pop("leads_read", None)
    for item in data["leads"]:
        item.pop("session", None)
    compact = screen(frame)
    assert leads_block(compact)[0] == "LEADS 2"
    assert all(row.split()[1:4] == ["-", "-", "-"] for row in leads_block(compact)[1:])
    lines = expanded(frame)
    assert any(line.startswith("QA | cc 9f9f9f9f | no session found") for line in lines)


def test_the_legend_names_the_leads_columns():
    legend = " ".join(MONITOR_LEGEND)
    assert "LEADS" in legend and "?busy" in legend and "ctx" in legend and "106k" in legend


# 17. JSON and the end-to-end text rule ------------------------------------------------------------------------------
SECRET = "hunter2-very-secret-376"
DIRTY = f"a\x1b[31m\u200bb {SECRET}"


def dirty_folder(root: Path, *, entry: str = "cli", status: str = "busy", model: str = "claude-fable-5-2") -> None:
    sessions = root / "sessions"
    sessions.mkdir(parents=True)
    item = {"pid": 4242, "sessionId": "11111111-2222-3333-4444-555555555555", "cwd": f"D:/work/{DIRTY}",
            "startedAt": (NOW - 3600) * 1000, "version": f"2.1\x07 {SECRET}", "kind": "interactive", "entrypoint": entry,
            "pidDomain": "win32:another-machine", "name": DIRTY, "status": status, "statusUpdatedAt": (NOW - 100) * 1000}
    (sessions / "4242.json").write_text(json.dumps(item), encoding="utf-8")
    project = root / "projects" / "p"
    project.mkdir(parents=True)
    lines = [{"type": "ai-title", "aiTitle": DIRTY}, {"type": "last-prompt", "lastPrompt": DIRTY},
             {"type": "assistant", "isSidechain": False, "timestamp": "2026-11-14T23:45:00Z",
              "message": {"model": model, "usage": {"input_tokens": 10, "cache_read_input_tokens": 90},
                          "content": [{"type": "text", "text": DIRTY}]}}]
    path = project / "11111111-2222-3333-4444-555555555555.jsonl"
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    os.utime(path, (NOW - 60, NOW - 60))


def test_json_keys_schema_and_the_text_rule_from_files_to_every_output(tmp_path, monkeypatch, db):  # noqa: F811
    from peerhub.extensions import lead_sessions
    from peerhub.extensions import monitor_read_model as mrm

    monkeypatch.setenv("PEERHUB_TEST_TOKEN", SECRET)
    root = tmp_path / "claude"
    dirty_folder(root)
    db = str(db)

    def reader(**facts: Any) -> Any:
        return lead_sessions.read_leads([str(root)], **facts)

    payload = mrm.render_monitor_frame(db, ["activity"], read_at=NOW, lead_reader=reader, process_probe=lambda pid: None)
    data = payload["groups"]["activity"]["data"]
    assert payload["schema_version"] == "2.0"
    assert data["leads_read"] == READ and set(data["leads_read"]) == {"state", "unreadable", "not_read"}
    (only,) = data["leads"]
    assert set(only) == {"label", "tool", "id", "running", "asks_24h", "last_ask_at", "session"}
    assert set(only["session"]) == {"id", "tool", "pid", "alive", "entry", "name", "folder", "cwd", "state", "state_since",
                                    "started_at", "version", "active_at", "model", "context_tokens", "context_window",
                                    "context_pct", "context_at", "title", "prompt_line", "said_line"}
    payload.update(selected_groups=["activity"], store_selection={"path": db, "source": "explicit"})
    outputs = {"json": json.dumps(payload, ensure_ascii=False),
               "compact": render_grouped_frame(deepcopy(payload), view="rich", unicode=True, width=100),
               "expand": render_grouped_frame(deepcopy(payload), view="rich", unicode=True, width=200, expand=True)}
    for name, text in outputs.items():
        assert SECRET not in text and "\x1b" not in text and "\u200b" not in text and "\x07" not in text, name
        assert "\\u001b" not in text and "\\u0007" not in text and "\\u200b" not in text, name
    assert "[REDACTED]" in outputs["compact"] and "[REDACTED]" in outputs["expand"]


def test_a_secret_matching_the_entry_state_and_model_patterns_reaches_no_output(tmp_path, monkeypatch, db):  # noqa: F811
    from peerhub.extensions import lead_sessions
    from peerhub.extensions import monitor_read_model as mrm

    state_secret = "sekret_state_x"  # the state pattern allows lower-case letters and `_` only
    monkeypatch.setenv("PEERHUB_TEST_TOKEN", SECRET)  # matches the entry and the safe-model patterns
    monkeypatch.setenv("PEERHUB_STATE_SECRET", state_secret)
    root = tmp_path / "claude"
    dirty_folder(root, entry=SECRET, status=state_secret, model=SECRET)

    def reader(**facts: Any) -> Any:
        return lead_sessions.read_leads([str(root)], **facts)

    payload = mrm.render_monitor_frame(str(db), ["activity"], read_at=NOW, lead_reader=reader,
                                       process_probe=lambda pid: None)
    (only,) = payload["groups"]["activity"]["data"]["leads"]
    assert (only["session"]["entry"], only["session"]["state"], only["session"]["model"]) == (None, None, None)
    payload.update(selected_groups=["activity"], store_selection={"path": str(db), "source": "explicit"})
    for text in (json.dumps(payload, ensure_ascii=False),
                 render_grouped_frame(deepcopy(payload), view="rich", unicode=True, width=100),
                 render_grouped_frame(deepcopy(payload), view="rich", unicode=True, width=200, expand=True)):
        assert SECRET not in text and state_secret not in text
