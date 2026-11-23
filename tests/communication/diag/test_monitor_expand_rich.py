"""Expanded layout, quota, activity and historical plain output."""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from peerhub.cli import monitor_view as mv
from peerhub.cli.monitor_view import FRAME_HEADS, FRAME_SECTIONS, render_grouped_frame, _width
from tests.communication.diag.test_monitor_activity_view import pool
from tests.communication.diag.test_monitor_plain_golden import GOLDEN_GUIDE_URL, _payload, blocks, full_payload, quiet_payload

SGR = re.compile(r"\x1b\[([0-9;]*)m")
OUR_CODES = {"0", *mv.RICH_SGR.values(), *mv.PILL_SGR.values()}  # #414: a child bar's empty cells
U, A = mv.RICH_GLYPHS[True], mv.RICH_GLYPHS[False]
GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "monitor" / "plain_expand_sections.json"
ORDER = [*FRAME_SECTIONS.values(), "STORE"]
_FRAME_HEAD = re.compile(r"^(?:── |-- )?(" + "|".join(map(re.escape, FRAME_SECTIONS.values())) + r")(?= |$)")
_OTHER_RULE_HEAD = re.compile(r"^(?:──|--) ([A-Z]+)\b")
DB_LINE = "DB:env (/w/.peerhub/core.db)"


@pytest.fixture(autouse=True)
def _colour_env(monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("COLORTERM", raising=False)
    monkeypatch.setenv("PEERHUB_GUIDE_URL", GOLDEN_GUIDE_URL)


def strip(text: str) -> str:
    return SGR.sub("", text)


def expand(payload: dict[str, Any], *, color: bool = False, unicode: bool = True, width: int = 100,
           height: int | None = None, **kw: Any) -> str:
    return render_grouped_frame(deepcopy(payload), view="rich", color=color, unicode=unicode, width=width, height=height,
                                expand=True, **kw)


def compact(payload: dict[str, Any], *, color: bool = False, unicode: bool = True, width: int = 100,
            height: int | None = None, **kw: Any) -> str:
    return render_grouped_frame(deepcopy(payload), view="rich", color=color, unicode=unicode, width=width, height=height,
                                **kw)


def detail_payload() -> dict[str, Any]:
    """`full_payload` with every detail group (selected in an order that is not the frame's), accounts and a credit."""
    payload = full_payload()
    payload["selected_groups"] = ["store", "context", "usage", "sessions", "execution", "peers", "capacity", "activity"]
    groups = payload["groups"]
    groups["execution"] = {"status": "OK", "data": {"scopes": [
        {"stream_id": "work", "peer_id": "cx", "certainty": {"not_started": 1, "may_have_started": 0, "started": 2,
                                                             "terminal": 3},
         "latency_30d": {"sum_ns": 3_000_000_000, "count": 2}}], "sample_size": 1, "truncated": False}}
    sessions = [{"peer_id": peer, "handle": f"sess_{peer}_1", "state": "ACTIVE", "generation": 2, "resumable": True,
                 "uses": 3, "configured_binding": {"profile": f"{peer}.effort", "model": "model-x", "effort": "high"},
                 "telemetry": {"usage": {"normalized": {"provider_total_tokens": 1250, "provider_input_tokens": 1000,
                                                        "cached_input_tokens": 500, "output_tokens": 250}}}}
                for peer in ("cc", "cx")]
    groups["sessions"] = {"status": "OK", "data": {"items": sessions, "sample_size": 2, "truncated": False}}
    groups["usage"] = {"status": "OK", "data": {"sessions": [{**item, "telemetry": item["telemetry"]["usage"]}
                                                              for item in sessions],
                                                "coverage_by_scope": [{"peer_id": "cx", "complete": True}]}}
    groups["context"] = {"status": "OK", "data": {"sessions": [
        {"peer_id": "cx", "context": {"percent": 25.0, "state": "MEASURED", "reason": None}}]}}
    groups["store"] = {"status": "OK", "data": {"schema_version": 7, "database_size_bytes": 2048}}
    groups["capacity"]["data"]["accounts"] = [
        {"peer_id": "cc", "plan": "max", "plan_status": "reported", "logged_in": True, "login_expiry_status": "n/a",
         "auth_method": "oauth", "state": "MEASURED", "age_seconds": 30}]
    groups["capacity"]["data"]["reset_credits"] = [{"peer_id": "cx", "available_count": 0, "state": "MEASURED"}]
    return payload


def eight_pools_payload() -> dict[str, Any]:
    """Eight pools that the compact frame shows whole (six 7-day pools and two binding 5-hour rows), nothing to
    refresh, one suggestion line (two below 100 columns): the compact frame's first 12 lines are line 1, line 2,
    POOLS and the suggestion."""
    observations = [pool("ag", "3P-5H", .05), pool("ag", "3P-7D", .31, pace=11), pool("ag", "A-7D", .79, pace=14),
                    pool("ag", "G-7D", .97, pace=-4), pool("cc", "C-7D", .63, pace=-20), pool("cc", "F-7D", .57),
                    pool("cx", "X-5H", 0.0, reset=3600), pool("cx", "X-7D", .42, fast=True, pace=31)]
    profiles = [{"profile": name, "fields": {"pools": {"value": pools}}} for name, pools in (
        ("ag.standard", ["G-7D"]), ("ag.argon", ["A-7D"]), ("ag.deepthink", ["3P-5H", "3P-7D"]),
        ("cc.effort", ["C-7D"]), ("cc.fable", ["F-7D"]), ("cx.effort", ["X-5H", "X-7D"]))]
    payload = _payload(full_payload()["groups"]["activity"]["data"]["asks"], observations, configured_profiles=profiles,
                       min_quota={"pool": "ag 3P-7D", "left_pct": 31, "state": "ok"})
    return payload


def heads(screen: str) -> list[str]:
    """The first word of each section heading, including unruled headings."""
    names: list[str] = []
    for line in strip(screen).splitlines():
        if match := _FRAME_HEAD.match(line) or _OTHER_RULE_HEAD.match(line):
            names.append(match[1])
    return names


def block(screen: str, section: str) -> list[str]:
    """The lines of one section (its rule first), colour removed, up to the next rule or the first line at column 0."""
    head = FRAME_SECTIONS[section]
    lines = strip(screen).splitlines()
    start = next(i for i, line in enumerate(lines) if re.match(rf"^(?:──|--) {re.escape(head)}(?= |$)", line))
    end = next((i for i in range(start + 1, len(lines)) if not lines[i].startswith(" ")), len(lines))
    return lines[start:end]


def entries(lines: list[str]) -> list[str]:
    """The entries of a RUNNING, LEADS or RECENT block: one starts in the second cell, its other lines further in."""
    chunks: list[str] = []
    for line in lines[1:]:
        if re.match(r" \S", line):
            chunks.append(line)
        else:
            chunks[-1] += "\n" + line
    return chunks


def flat(text: str) -> str:
    return " ".join(text.split())


# ---- R1: the top is the compact frame's ------------------------------------------------------------------------------
@pytest.mark.parametrize("width", [60, 80, 100])
@pytest.mark.parametrize("color", [False, True])
@pytest.mark.parametrize("unicode", [True, False])
def test_expansion_keeps_the_compact_pool_rows_and_section_order(width, color, unicode):
    payload = eight_pools_payload()
    small = compact(payload, width=width, color=color, unicode=unicode)
    big = expand(payload, width=width, color=color, unicode=unicode)
    assert small.splitlines()[:2] == big.splitlines()[:2]
    assert block(small, "pools") == block(big, "pools")
    assert {"POOLS", "RUNNING", "RECENT"} <= set(heads(small))
    for screen in (small, big):
        shown = [head for head in heads(screen) if head in FRAME_HEADS]
        assert shown == [head for head in FRAME_HEADS if head in shown]

def test_expand_uses_the_terminal_width_and_the_compact_frame_stays_at_100_columns():
    payload = eight_pools_payload()
    assert max(_width(line) for line in compact(payload, width=140).splitlines()) == 99
    wide = expand(payload, width=140).splitlines()
    assert max(_width(line) for line in wide) == 139 and _width(wide[0]) == 139  # one rule: the last column stays free
    assert all(_width(line) == 139 for line in wide if line.startswith("── "))


def test_expand_shows_every_pool_without_a_cap_or_an_overflow_line():
    payload = full_payload()
    small, big = compact(payload).splitlines(), expand(payload, show_quota_notes=True).splitlines()
    assert block("\n".join(small), "pools")[0].startswith("── POOLS 9 ─")
    assert block("\n".join(big), "pools")[0].startswith("── POOLS 11 ─") and not any(re.match(r"^\+\d+ pools", line) for line in big)
    rows = block("\n".join(big), "pools")[1:]
    names = [" ".join(row.replace("└─ ", "").split()[:2]) for row in rows]
    assert len(rows) == 10 and len(set(names)) == 10  # every observation of the payload but the n/a one (#400) has its row
    # Expanded POOLS adds non-binding 5-hour rows directly under their weekly parents.
    for index, row in enumerate(rows):
        if "└─" in row:
            assert rows[index - 1].split()[1] == row.split()[1].replace("-5H", "-7D")
    assert {row.split()[1] for row in rows if "└─" in row} == {"X-5H", "3P-5H", "C-5H", "G-5H"}
    compact_rows = [" ".join(row.split()[:3]) for row in block("\n".join(small), "pools")[1:]]
    order = [" ".join(row.split()[:3]) for row in rows]
    assert [row for row in order if row in compact_rows] == compact_rows  # the same order, with the hidden rows added
    assert any("(X-30D n/a)" in row for row in rows)


def test_the_suggestion_wraps_without_a_cap():
    payload = full_payload()
    payload["configured_profiles"] = [
        {"profile": f"{peer}.{suffix}", "fields": {"pools": {"value": [pool_name]}}}
        for peer, pool_name in (("cc", "C-7D"), ("cx", "X-7D"), ("ag", "A-7D"))
        for suffix in ("standard", "effort", "deepthink", "a-very-long-top-profile-name")]
    wide = compact(payload, width=250).splitlines()  # the compact frame stops at 100 columns: two whole lines
    first = next(i for i, line in enumerate(wide) if line.startswith("suggest:"))
    whole = flat(" ".join(wide[first:next(i for i in range(first, len(wide)) if wide[i].startswith("─"))]))
    assert whole.count("=") == 4 and "…" not in whole
    narrow = compact(payload, width=30).splitlines()
    at = next(i for i, line in enumerate(narrow) if line.startswith("suggest:"))
    assert next(i for i in range(at + 1, len(narrow)) if narrow[i].startswith("── ")) - at <= 2
    lines = expand(payload, width=30).splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("suggest:"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("── "))
    assert end - start > 2 and "…" not in "".join(lines[start:end])
    assert "".join(lines[start:end]).replace(" ", "") == whole.replace(" ", "")  # nothing is lost at 30 columns


def test_the_header_drops_the_fields_hidden_text_and_the_conditional_db_part():
    payload = full_payload()
    payload["_nearest_store_path"] = "/elsewhere/.peerhub/core.db"  # the compact frame names the store on line 3
    small = compact(payload, width=48).splitlines()
    assert "Fields hidden · --expand" in small and "DB: /w/.peerhub/core.db" in small
    big = expand(payload, width=48).splitlines()
    assert not any("Fields hidden" in line or line.startswith("DB: ") for line in big)
    assert big[1].startswith("PeerHub Monitor 2026-11-14 23:46Z") and [line for line in big if line.startswith("DB:")] == [
        DB_LINE]
    assert big.index(DB_LINE) == 2


def test_without_capacity_data_the_store_line_stays_in_the_header():
    payload = full_payload()
    payload["selected_groups"] = ["activity"]
    lines = expand(payload).splitlines()
    assert lines[1].startswith("PeerHub Monitor 2026-11-14 23:46Z") and "DB:env" in lines[1]
    assert heads("\n".join(lines)) == [head for head in FRAME_HEADS if head not in {"POOLS", "suggest:"}]
    at = lines.index(DB_LINE)
    assert at == 2
    payload["_store_note"] = "--db overrides PEERHUB_DB (/other/core.db)"
    assert expand(payload).splitlines()[at:at + 2] == [DB_LINE, "--db overrides PEERHUB_DB (/other/core.db)"]


# ---- R2: the store path, then the sections in their order -------------------------------------------------------------
@pytest.mark.parametrize("width", [12, 20, 34, 60])
def test_the_db_path_wraps_and_is_never_truncated(width):
    payload = quiet_payload()
    path = "C:/Users/someone/projects/a-long-workspace-name/sub/.peerhub/core.db"
    payload["store_selection"] = {"path": path, "source": "explicit"}
    lines = expand(payload, width=width).splitlines()
    chunks = mv._compact_wrap(f"DB:explicit ({path})", width - 1)  # pyright: ignore[reportPrivateUsage]
    start = next(i for i, line in enumerate(lines) if line.startswith("DB:"))
    assert lines[start:start + len(chunks)] == chunks  # #412: near the end of the frame, wrapped, never cut
    assert "".join(chunks).replace(" ", "") == f"DB:explicit({path})" and "…" not in "".join(chunks)
    assert len(chunks) > 1 and all(_width(line) <= width - 1 for line in lines)


def test_one_db_line_at_100_columns_then_the_sections_in_the_agreed_order():
    payload = detail_payload()
    screen = expand(payload)
    lines = screen.splitlines()
    assert heads(screen) == ORDER
    assert lines[1].startswith("PeerHub Monitor") and "DB:env" in lines[1]  # the title names the store (#412)...
    assert lines[2] == DB_LINE
    suggest = next(i for i, line in enumerate(lines) if line.startswith("suggest:"))
    assert lines[suggest + 1].startswith("── ALERTS 2 ─")
    assert sum(line.startswith("PeerHub Monitor") for line in lines) == 1  # the grouped frame's own title line is gone
    assert not any("ACTIVITY [" in line or "PeerHub Monitor | " in line for line in lines)
    assert lines[-2] == "Coverage: partial; peerhub explain <code>"
    assert lines[-1].startswith(f"{mv.GUIDE_TEXT}: ")
    legend = lines.index(next(line for line in lines if line.startswith("ok enough · low >0-10%")))
    assert legend > lines.index(next(line for line in lines if line.startswith("── STORE ")))  # the legend ends the frame


def test_the_order_does_not_depend_on_the_selection_order_and_an_unknown_group_stays():
    payload = detail_payload()
    payload["selected_groups"] = [*reversed(payload["selected_groups"]), "runtime", "mystery"]
    payload["groups"]["runtime"] = {"status": "OK", "data": {"sessions": []}}
    payload["groups"]["mystery"] = {"status": "OK", "data": {"some_value": 3}}
    screen = expand(payload)
    assert heads(screen) == [*ORDER, "RUNTIME", "MYSTERY"]
    assert "── MYSTERY [OK] [unrecognized group] ─" in screen and " some value: 3" in screen.splitlines()


def test_the_expanded_section_rules_match_the_compact_style():
    screen = expand(detail_payload(), color=True)
    rules = [line for line in screen.splitlines() if strip(line).startswith("── ")]
    assert heads("\n".join(rules)) == [head for head in ORDER if head != "suggest:"]
    dim, title = mv.RICH_SGR["dim"], mv.RICH_SGR["title"]
    assert all(line.startswith(f"\x1b[{dim}m──\x1b[0m \x1b[{title}m") and _width(line) == 99 for line in rules)
    texts = [strip(line) for line in rules]
    assert any(text.startswith("── RUNNING 4 · ag 1 cc 2 cx 1 ─") for text in texts)
    assert any(text.startswith("── LEADS 4 entries · partial (1 unreadable, 2 not read) · 3 sessions found ─") for text in texts)
    assert any(text.startswith("── QUOTA [OK] · measured remaining") for text in texts)
    assert any(text.startswith("── PEERS [OK] ─") for text in texts) and any(text.startswith("── RECENT 4 (4 running) ─") for text in texts)
    assert f"\x1b[{title}mQUOTA [OK]\x1b[0m\x1b[{dim}m · \x1b[0mmeasured remaining" in screen  # title, dim separator


def test_section_lines_use_the_dot_separator_never_a_pipe():
    screen = expand(detail_payload(), width=160)
    body = screen.split(DB_LINE)[1]
    assert " | " not in body.replace("--expand | more", "").replace(mv.POOL_LEGEND, "")  # G8 uses pipes.
    lines = screen.splitlines()
    assert " gone: cc.fable activity-view · age 5m · high · a2" in lines
    assert " pool_out: cx X-5H · age - · high · a3" in lines
    assert " cx reset credits: 0 · soonest - [MEASURED]" in lines
    assert " cc login ok · plan max · login token expiry n/a · auth oauth · age 30s [MEASURED]" in lines
    assert " Model facts: profile defaults rev unknown (date unknown) · sources: codex debug models · agy models · claude: known ids" in lines
    assert " Schema 7 · database 2.0 KiB" in lines
    assert " ag  quota: unknown -> unknown [unknown]" in lines and "     -> latest session: none" in lines
    assert " ACCOUNTS (plan is display data; the expiry is the login token's, not billing)" in lines


# ---- R3: QUOTA details -----------------------------------------------------------------------------------------------
def quota_rows(screen: str) -> dict[str, list[str]]:
    """The cells of each pool row of the QUOTA details table, by pool name."""
    rows: dict[str, list[str]] = {}
    for line in block(screen, "capacity")[2:]:
        cells = line.replace("└─ ", "").replace("`- ", "").split()
        name = next((cell for cell in cells[:2] if re.fullmatch(r"[0-9A-Z]+-\d+[HD]", cell)), None)
        if name and name not in rows and "next:" not in line and "n/a not" not in line:
            rows[name] = cells[cells.index(name) + 1:]
    return rows


def test_g_quota_details_match_pools_vocabulary_and_bar():
    screen = expand(detail_payload(), width=140)
    table = block(screen, "capacity")
    assert table[1].split()[:7] == ["PEER", "POOL", "STATE", "LEFT", "PACE(pp)", "EXH/RESET", "FAMILY"]
    rows = quota_rows(screen)
    assert len(rows) == 10  # X-30D remains a folded note.
    for name, cells in rows.items():
        row = next(line for line in block(screen, "pools")[1:]
                   if re.match(rf"^\s*(?:\w+ +)?(?:[└├]─ )?{name} +", line))
        actual = row.split()[row.split().index(name) + 1:]
        assert (cells[:2], cells[3:6]) == (actual[:2], actual[3:6]), name  # G: state, left, pace and pair
        assert len(cells[2]) > len(actual[2]) == mv.BAR_CELLS
    assert rows["X-7D"][:2] == [U["fast"], "fast"]
    assert rows["X-7D"][3:5] == ["42%", "+31.0!"]


@pytest.mark.parametrize("unicode", [True, False])
def test_stale_duplicate_pool_names_keep_each_human_observations_state(unicode):
    observations = []
    for peer, account, remaining in (("cc", "one", .05), ("cc", "two", .70), ("cx", "one", .80)):
        item = pool(peer, "C-7D", remaining)
        item.update(pool_ref=f"quota:{account}:C-7D", state="STALE", age_seconds=540, measurements=None)
        observations.append(item)
    wire = [{**item, "last_measurements": None} for item in observations]
    payload = _payload([], wire, _human_observations=observations)
    screen = expand(payload, width=160, unicode=unicode)
    glyphs = U if unicode else A
    expected = [[glyphs["low"], "low"], [glyphs["aged"], "aged"], [glyphs["aged"], "aged"]]
    for section in ("pools", "capacity"):
        states = [cells[at + 1:at + 3] for line in block(screen, section)[1:]
                  if "C-7D" in (cells := line.split()) and "next:" not in line
                  for at in [cells.index("C-7D")]]
        assert states == expected, (section, states)


@pytest.mark.parametrize("elapsed", [0, 2])
def test_quota_remaining_matches_pools_after_reset_passes(elapsed):
    payload = _payload([], [pool("cc", "C-7D", .10, fast=True, pace=31.0, reset=-1 if elapsed == 0 else 1)],
                       display_elapsed_seconds=elapsed)
    screen = expand(payload, width=160)
    pool_cells = block(screen, "pools")[1].split()
    quota_cells = quota_rows(screen)["C-7D"]
    assert pool_cells[2:4] == quota_cells[:2] == [U["aged"], "reset"]
    assert quota_cells[3:6] == pool_cells[5:8] == ["—", "—", "—/passed"]  # G3: expired window


def test_g_one_even_burn_legend_follows_pools_and_gap():
    for view in ("plain", "rich"):
        screen = render_grouped_frame(detail_payload(), view=view, width=140, expand=True)
        assert screen.index("gap vs ") < screen.index(mv.POOL_LEGEND) < screen.index("suggest:")
        assert "FORECAST" not in screen


def test_quota_details_keep_notes_credits_and_accounts_and_name_dropped_columns():
    screen = expand(detail_payload(), show_quota_notes=True, width=140)
    quota = block(screen, "capacity")
    for row in (" cx X-30D n/a not reported (plan prolite)", " ag STALE:window; next: peerhub observation refresh --peers ag",
                " cc F-7D evidence UNKNOWN; next: peerhub observation refresh --peers cc",
                " cx reset credits: 0 · soonest - [MEASURED]",
                " ACCOUNTS (plan is display data; the expiry is the login token's, not billing)"):
        assert row in quota, row
    assert "cc.effort" in next(line for line in quota if " C-7D " in line)
    narrow = block(expand(detail_payload(), show_quota_notes=True, width=60), "capacity")
    assert next(line for line in narrow if "PEER POOL" in line).split()[:5] == ["PEER", "POOL", "STATE", "LEFT", "PACE(pp)"]
    assert any("Columns omitted: age" in line for line in narrow)  # G4


def test_quota_details_take_their_colour_from_the_pool_colour():
    screen = expand(detail_payload(), color=True, width=140)
    lines = screen.splitlines()
    start = next(i for i, line in enumerate(lines) if "QUOTA [" in line)
    first = next(i for i, line in enumerate(lines) if strip(line).startswith("── POOLS "))
    pools = lines[first + 1:next(i for i, line in enumerate(lines) if strip(line).startswith("suggest:"))]
    pools = [line for line in pools if re.match(r"^\s*(?:\w+ +)?(?:[└├]─ )?[0-9A-Z]+-\d+[HD] +", strip(line))]
    assert len(pools) == 10
    details = [line for line in lines[start + 1:] if re.match(r"^\s*(?:\w+ +)?(?:[└├]─ )?[0-9A-Z]+-\d+[HD] {2,}", strip(line))]

    def state_code(line: str) -> tuple[str, str]:
        match = re.search(r"\x1b\[([0-9;]+)m(. \S+)\x1b\[0m", line)
        assert match, line
        return match[2], match[1]

    top = {re.search(r"[0-9A-Z]+-\d+[HD]", strip(line))[0]: state_code(line) for line in pools}  # type: ignore[index]
    for line in details:
        name = re.search(r"[0-9A-Z]+-\d+[HD]", strip(line))
        if name is None or "next:" in line or "n/a not" in strip(line):
            continue
        assert state_code(line) == top[name[0]], line
    assert state_code(next(line for line in details if " X-7D " in strip(line))) == (f"{U['fast']} fast", mv.RICH_SGR["warn"])
    assert state_code(next(line for line in details if " C-7D " in strip(line))) == (f"{U['ok']} ok", mv.RICH_SGR["ok"])
    assert set(SGR.findall(screen)) <= OUR_CODES and not {"32", "33", "31"} & set(SGR.findall(screen))
    assert all(chunk.startswith(U["cempty"]) for chunk in screen.split("\x1b[2m")[1:])  # SGR 2 only dims a child bar's empty cells
    x7d = next(line for line in details if " X-7D " in strip(line))
    assert span(x7d, "+31.0!") == mv.RICH_SGR["warn"]  # M1/G2


def span(line: str, text: str) -> str | None:
    match = re.search(r"\x1b\[([0-9;]*)m[^\x1b]*" + re.escape(text), line)
    return match[1] if match and match[1] != "0" else None


# ---- R4: the entries carry the compact markers and keep every field ---------------------------------------------------
def test_running_entries_start_with_the_compact_marker_and_keep_every_field():
    payload = detail_payload()
    screen = expand(payload, color=True, width=120)
    rows = entries(block(screen, "running"))
    assert [row.split()[0] for row in rows] == [U["run"], U["gone"], "?", "!"]
    asks = [row for row in payload["groups"]["activity"]["data"]["asks"] if row["state"] in ("run", "overdue")]
    for row, ask_row in zip(rows, asks):
        text = flat(row)
        for field in mv._ask_fields(ask_row):
            assert field in text, field
        assert f"ask: {ask_row['ask_line_long']}" in text
        assert all(line.startswith("   ") and not line.startswith("    ") for line in row.splitlines()[1:])
    assert rows[0].startswith(f" {U['run']} flag none (caller alive) · state run · elapsed 16m · started 2026-11-14 23:30:40Z")
    assert rows[1].startswith(f" {U['gone']} flag !gone (caller process gone) · state run")
    assert rows[3].startswith(" ! flag !overdue (lease expired 2m ago) · state overdue")
    painted = [line for line in screen.splitlines() if re.match(r"^ \x1b\[[0-9;]+m.\x1b\[0m flag ", line)]
    assert [SGR.findall(line)[0] for line in painted] == [mv.RICH_SGR[c] for c in ("ok", "bad", "warn", "warn")]
    assert not any(line.startswith("- ") for line in strip(screen).splitlines())  # the plain view's entry mark


def test_recent_entries_start_with_a_state_dot_in_the_state_colour():
    payload = detail_payload()
    screen = expand(payload, color=True, width=120)
    rows = entries(block(screen, "recent"))
    # newest first: ok (2m), empty (3m), unsure (33m), error (40m); each is two lines, the dot starting the first
    assert [row.split()[:2] for row in rows] == [[U["dot"], state] for state in ("ok", "empty", "unsure", "error")]
    assert all(len(row.splitlines()) == 2 and row.splitlines()[1].startswith("   in ") for row in rows)
    assert "reply \"결론: 모두 닫힘 (C1-C5 100) (long)\"" in flat(rows[0])
    assert "(denied: command)" in rows[1]  # the note stands in for the reply that was never written
    painted = [line for line in screen.splitlines() if re.match(r"^ \x1b\[[0-9;]+m.\x1b\[0m (ok|empty|unsure|error) ", line)]
    assert [SGR.findall(line)[0] for line in painted] == [mv.RICH_SGR[c] for c in ("ok", "bad", "warn", "bad")]


def test_recent_expands_history_and_names_the_running_and_superseded_counts():
    from tests.communication.diag.test_monitor_activity_view import activity_data, ask

    payload = quiet_payload()
    asks = [ask("ok", name=f"done-{i}", age=60.0 * (i + 1), exact=True, reply_line="fine")
            for i in range(mv.TEXT_NEWEST_ROWS + 1)]
    asks.append(ask("unsure", name="replaced", superseded=True, note="superseded", age=4000, exact=True))
    asks.append(ask(name="still-running"))
    payload["groups"]["activity"]["data"] = activity_data(asks)
    screen = expand(payload)
    assert f"── RECENT {mv.TEXT_NEWEST_ROWS + 1} (1 running) · superseded 1 ─" in screen
    rows = entries(block(screen, "recent"))
    assert len(rows) == mv.TEXT_NEWEST_ROWS + 1
    assert f"done-{mv.TEXT_NEWEST_ROWS - 1}" in rows[-2] and "replaced" in rows[-1]
    assert "still-running" not in "".join(rows)

def test_leads_entries_list_every_lead_with_a_context_meter():
    payload = detail_payload()
    screen = expand(payload, color=True, width=120)
    rows = entries(block(screen, "leads"))
    assert [row.split()[0] for row in rows] == ["docs-site", "shop-4f", "notes", "ci-box"]  # the compact frame caps at 3
    assert rows[0].startswith(" docs-site · cc 1a2b3c4d · busy since 2026-11-14 23:16:29Z · active 12s ago · alive · pid 4242")
    assert "model claude-fable-5-2 · ctx ■□□□□ 106,137 of 1,000,000 model max (10%) at 2026-11-14 23:45:08Z" in flat(rows[0])
    assert "ctx ■■■■□ 106,137 of 1,000,000 model max (83%!) at 2026-11-14 23:45:08Z" in flat(rows[1])
    assert "ctx ───── 106,137 (window unknown) at 2026-11-14 23:45:08Z" in flat(rows[2])
    assert rows[3] == " ci-box · ? 51c2a9f0 · no session (not a Claude Code lead) · running 1 · asks 24h 0 · last ask ?"
    for text in ("entry cli · claude 2.1.323 · started 2026-11-14 09:30Z", "running 1 · asks 24h 0 · last ask ?",
                 "name docs-site-5a · cwd D:/work/docs-site", "title: Release notes for the spring update",
                 "prompt: Add the section about the importer",
                 "said: The importer section is drafted; running the link check now."):
        assert text.replace(" · ", " ") in flat(rows[0]).replace(" · ", " "), text  # a line may end between two fields
    assert all(line.startswith("   ") for row in rows for line in row.splitlines()[1:])
    assert f"ctx \x1b[{mv.RICH_SGR['bad']}m■■■■□\x1b[0m 106,137" in screen  # 80% or more
    assert f"ctx \x1b[{mv.RICH_SGR['dim']}m─────\x1b[0m 106,137 (window unknown)" in screen  # no value: structure
    assert "ctx ■□□□□ 106,137" in screen  # below the mark: not painted
    payload["groups"]["activity"]["data"]["leads"][0]["session"]["context_tokens"] = None
    assert "ctx ───── ?" in strip(expand(payload, width=120))


def test_an_unreadable_activity_group_is_one_running_section_that_says_so():
    payload = detail_payload()
    payload["groups"]["activity"] = {"status": "ERROR", "data": None}
    payload["errors"] = {"activity": "OperationalError: secret text"}
    screen = expand(payload)
    assert block(screen, "running") == ["── RUNNING ? · activity read failed " + "─" * 63,
                                        " OperationalError: database read failed"]
    assert "LEADS" not in heads(screen) and "RECENT" not in heads(screen) and "secret text" not in screen


def test_a_failed_group_names_the_error_class_only():
    payload = detail_payload()
    payload["groups"]["capacity"] = {"status": "ERROR", "data": None}
    payload["errors"] = {"capacity": "OperationalError: SELECT secret"}
    for height in (None, 80):
        screen = expand(payload, height=height)
        assert block(screen, "capacity")[:3] == ["── QUOTA [ERROR] · measured remaining " + "─" * 61,
                                              " OperationalError: database read failed", " data unavailable"]
        assert "SELECT secret" not in screen


# ---- R5: NO_COLOR, the ASCII fallback, narrow widths, a bounded height ------------------------------------------------
@pytest.mark.parametrize("build", [detail_payload, quiet_payload, eight_pools_payload])
@pytest.mark.parametrize("width", [48, 100, 140])
def test_no_color_keeps_the_same_layout_without_sgr(build, width, monkeypatch):
    painted = expand(build(), color=True, width=width)
    bare = expand(build(), color=False, width=width)
    assert "\x1b[" in painted and "\x1b" not in bare
    assert strip(painted).splitlines()[1:] == bare.splitlines()[1:]  # colour changes no text...
    head = re.match(r"^(OK\??|ATTN \d+\??)", bare.splitlines()[0])
    assert head and painted.splitlines()[0].startswith(f"\x1b[{mv.PILL_SGR['ATTN' if 'ATTN' in head[1] else head[1]]}m {head[1]} ")
    assert strip(painted).splitlines()[0].split() == bare.splitlines()[0].split()  # ...but the pill's padding
    assert " · " in bare.splitlines()[0] and " | " not in bare.splitlines()[0]  # the pill line's layout, not its colour
    if width >= 100:
        assert bare.splitlines()[0].endswith("23:46Z") and _width(bare.splitlines()[0]) == width - 1
    monkeypatch.setenv("NO_COLOR", "1")
    assert expand(build(), color=True, width=width) == bare
    assert set(SGR.findall(painted)) <= OUR_CODES


@pytest.mark.parametrize("width", [60, 100])
def test_the_ascii_fallback_keeps_the_layout_with_ascii_decoration(width):
    screen = expand(detail_payload(), unicode=False, width=width)
    assert screen.isascii() and "\x1b" not in screen
    assert heads(screen) == ORDER
    lines = screen.splitlines()
    assert lines[0].startswith("ATTN 2 | gone: cc.fable activity-view 5m")
    assert any(line.startswith("-- POOLS 11 ") for line in lines) and any("`- 3P-5H" in line for line in lines)
    assert [row.split()[0] for row in entries(block(screen, "running"))] == [A["run"], A["gone"], "?", "!"]
    assert all(re.match(rf" {re.escape(A['dot'])} (ok|empty|unsure|error) ", row) for row in entries(block(screen, "recent")))
    if width == 100:
        assert "ctx #.... 106,137 of 1,000,000 model max (10%)" in screen and "ctx ----- 106,137 (window unknown)" in screen
        assert " gone: cc.fable activity-view | age 5m | high | a2" in lines
    assert f"{A['fast']} fast" in " ".join(block(screen, "capacity")) and mv.POOL_LEGEND in " ".join(screen.split())  # G8 wraps the legend.
    kept = expand(detail_payload(), unicode=False, width=width, text_encoding="utf-8")
    assert "결론: " in kept and "─" not in kept and "·" not in kept and "●" not in kept  # A5: text stays (cut at 60 columns)
    # the same layout line for line once the text has the same widths (without an encoding, Korean becomes `?`)
    assert len(kept.splitlines()) == len(expand(detail_payload(), width=width).splitlines())
    assert heads(kept) == ORDER
    painted = expand(detail_payload(), unicode=False, color=True, width=width)
    assert strip(painted).splitlines()[1:] == lines[1:] and strip(painted).isascii()


@pytest.mark.parametrize("build", [detail_payload, quiet_payload])
@pytest.mark.parametrize("width", [0, 1, 2, 3, 8, 12, 19, 20, 26, 33, 40, 47, 48, 59, 64, 79, 80, 81, 100, 132, 250])
def test_every_line_fits_every_width(build, width):
    for unicode in (True, False):
        for color in (False, True):
            for height in (None, 30):
                screen = expand(build(), width=width, unicode=unicode, color=color, height=height)
                lines = screen.splitlines()
                assert all(_width(line) <= max(0, width - 1) for line in lines), (width, unicode, color, height)
                assert set(SGR.findall(screen)) <= OUR_CODES and "\x1b" not in strip(screen)
                if width <= 1:
                    assert screen == ""
                elif height is None and width >= 20:
                    assert "POOLS" in heads(screen)
                    assert build is quiet_payload or any(strip(line).lstrip("─- ").startswith("RECENT") for line in lines)
                    assert strip(lines[0]).startswith(("OK", " OK", "ATTN", " ATTN"))


@pytest.mark.parametrize("width", [60, 100, 140])
def test_a_bounded_height_keeps_the_top_and_ends_with_the_more_hint(width):
    payload = detail_payload()
    guide = f"{mv.GUIDE_TEXT}: "
    whole = [line for line in expand(payload, width=width, height=10_000).splitlines() if not line.startswith(guide)]
    unbounded = [line for line in expand(payload, width=width).splitlines() if not line.startswith(guide)]
    assert whole[-1].startswith("Coverage: ") and "rows hidden" not in "\n".join(whole)
    assert unbounded[:len(whole) - 1] == whole[:-1] and unbounded[-1] == whole[-1] and len(unbounded) > len(whole)
    assert not any(line.startswith("ok enough") for line in whole) and any(line.startswith("ok enough") for line in unbounded)
    assert expand(payload, width=width, height=len(whole) + 1).splitlines() == whole
    for height in range(4, len(whole) + 1):
        screen = expand(payload, width=width, height=height)
        lines = screen.splitlines()
        assert len(lines) <= height - 1
        assert lines[:2] == whole[:2]
        shown = heads(screen)
        if height == 5 + mv.FRAME_TRAILING_ROWS:
            assert lines[-1].startswith(f"gap vs {mv.PACE_GAP_BASELINE}")
        else:
            assert "--expand | more" in screen
        assert shown == ORDER[:len(shown)]

def test_a_bounded_height_keeps_alerts_and_running_visible():
    screen = expand(detail_payload(), height=28)
    shown = heads(screen)
    assert len(screen.splitlines()) <= 27 and {"ALERTS", "RUNNING"} <= set(shown)
    assert shown == [head for head in FRAME_HEADS if head in shown]
    assert DB_LINE in screen and "pool_out:" in screen and "review-api" in screen
    assert "--expand | more" in screen

def test_the_narrow_hint_keeps_the_count_and_the_command():
    lines = expand(detail_payload(), width=40, height=12).splitlines()
    assert len(lines) <= 11 and any(re.match(r"^\+\d+ ", line) and "--expand | more" in line for line in lines)


@pytest.mark.parametrize("style", ["plain", "rich"])
def test_short_narrow_frame_fits_the_hint_on_one_line_to_keep_a_second_pool(style):
    lines = render_grouped_frame(detail_payload(), view=style, expand=True, width=60, height=9).splitlines()
    assert len(lines) == 8
    assert any("C-7D" in line for line in lines)  # G4: the remaining row keeps its core columns.
    assert any(re.match(r"\+\d+ rows hidden", line) for line in lines) and "--expand" in lines[-1]


# ---- the documents say what --expand is --------------------------------------------------------------------------------
def test_the_lesson_and_docs_describe_priority_and_the_full_db_path():
    from peerhub.cli.lessons import lesson_by_id, render_lessons_reference

    root = Path(__file__).resolve().parents[3]
    lesson = lesson_by_id("monitor-view")[0]["fix"]
    assert "ALERTS and RUNNING get space before RECENT" in lesson and "full DB path" in lesson
    assert (root / "docs" / "lessons.md").read_text(encoding="utf-8") == render_lessons_reference()
    assert "ALERTS and RUNNING get space before RECENT" in (
        root / "docs/implementation/OBSERVATION_PROBES.md").read_text(encoding="utf-8")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    newer, release = changelog.split("## [0.24.21] - 2026-11-17", 1)
    assert "ALERTS, RUNNING, LEADS, QUOTA, PEERS, EXECUTION, SESSIONS, USAGE, CONTEXT" in release.split("\n## [", 1)[0]
    assert "### Changed" in newer and "RECENT" in newer

# ---- the plain view retains its layout; the payload is untouched ------------------------------------------------------
PLAIN_WIDTHS = (60, 120)
PLAIN_HEIGHTS = (None, 14, 40)
PLAIN_PAYLOADS = {"full": full_payload, "detail": detail_payload}


def plain_cases() -> Iterator[tuple[str, dict[str, Any], dict[str, Any]]]:
    for name, build in PLAIN_PAYLOADS.items():
        for width in PLAIN_WIDTHS:
            for height in PLAIN_HEIGHTS:
                yield (f"{name}-w{width}-h{height}", build(),
                       {"view": "plain", "unicode": False, "width": width, "height": height, "expand": True, "tty": True})


GOLDENS: dict[str, str] = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}


def test_the_plain_expand_goldens_cover_every_case():
    assert sorted(GOLDENS) == sorted(case for case, _, _ in plain_cases()) and len(GOLDENS) == 12


@pytest.mark.parametrize("case,payload,kwargs", [pytest.param(*item, id=item[0]) for item in plain_cases()])
def test_plain_expand_matches_its_golden(case, payload, kwargs):
    # C1, M3–M5 and G: QUOTA shares POOLS' state, tick, durations and profile labels.
    # M9/G8 add the same gap and legend above suggest, moving the height cut.
    screen = render_grouped_frame(payload, show_quota_notes=True, **kwargs)
    actual, expected = blocks(screen, expanded=True), blocks(GOLDENS[case], expanded=True)
    pools = FRAME_SECTIONS["pools"]
    assert actual.get(pools) == expected.get(pools)
    if kwargs["height"] is None:
        assert actual == expected
    else:
        # Expanded RECENT can switch formats and the trailer counts the rows cut by the current ladder.
        for section in (actual.keys() & expected.keys()) - {pools, "trailer", FRAME_SECTIONS["recent"]}:
            shown, saved = actual[section], expected[section]
            common = min(len(shown), len(saved))
            assert shown[:common] == saved[:common], section
    assert "\x1b" not in screen and screen.isascii()
    assert render_grouped_frame(payload, show_quota_notes=True, color=True, **kwargs) == screen  # the plain view is never painted


def test_the_plain_expand_frame_has_the_one_order_of_the_compact_frame():
    """The plain expanded frame follows the compact order and shows the shared quota bar and state words (G)."""
    screen = render_grouped_frame(detail_payload(), view="plain", unicode=False, width=120, expand=True)
    lines = screen.splitlines()
    assert lines[0].startswith("ATTN 2 | gone: cc.fable activity-view 5m | pool_out: cx X-5H")
    assert lines[1].startswith("PeerHub Monitor 2026-11-14 23:46Z | DB:env | quota age")
    assert heads(screen) == ORDER
    assert any("PACE(pp)" in line and "EXH/RESET" in line for line in lines)  # G4
    assert any(line.startswith("- flag none (caller alive) | state run") for line in lines)  # RUNNING keeps every field
    assert lines.index(DB_LINE) == 2


@pytest.mark.parametrize("build", [detail_payload, quiet_payload, eight_pools_payload])
def test_rendering_does_not_change_the_payload(build):
    payload = build()
    before = deepcopy(payload)
    for kw in ({}, {"height": 20}, {"color": True}, {"unicode": False, "width": 40}):
        render_grouped_frame(payload, view="rich", expand=True, **kw)
    assert payload == before
    assert json.dumps(payload, sort_keys=True, default=str) == json.dumps(before, sort_keys=True, default=str)
