"""The plain compact frame, pinned byte for byte over every width, height and output encoding of `golden_cases()`.

The goldens in `tests/communication/fixtures/monitor/plain_v0_24_17.json` began as the `v0.24.17` tag's frames (the file
keeps its name); #400 regenerated the cases that show the n/a pool, and #412 regenerated all 100 for the order
verdict, header, POOLS, the suggestion, RECENT, RUNNING, LEADS (0.24.17 printed RUNNING, LEADS, RECENT, the suggestion,
POOLS) and RECENT's new rows (newest first, five at most, with the session and cache columns). Only what #412
changed moved: every case still renders the same pools, suggestion, RUNNING rows, LEADS rows and line 1.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from peerhub.cli.monitor_view import render_grouped_frame
from tests.communication.diag.test_monitor_activity_view import NOW, SOURCES_READ, activity_data, ask, pool
from tests.communication.diag.test_monitor_leads_view import lead, sess

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "monitor" / "plain_v0_24_17.json"
OLD_ORDER = ("RUNNING", "LEADS", "RECENT", "suggest:", "POOLS")
NEW_ORDER = ("POOLS", "suggest:", "RECENT", "RUNNING", "LEADS")
_HEAD = re.compile(r"^(RUNNING|LEADS|RECENT|POOLS) |^(suggest:)")
PROFILES = [{"profile": name, "fields": {"pools": {"value": pools}}} for name, pools in (
    ("ag.standard", ["G-5H", "G-7D"]), ("ag.argon", ["A-7D"]), ("ag.deepthink", ["3P-5H", "3P-7D"]),
    ("cc.effort", ["C-5H", "C-7D"]), ("cc.fable", ["F-7D"]), ("cx.effort", ["X-5H", "X-7D"]))]


def _payload(asks: list[dict[str, Any]], observations: list[dict[str, Any]], **over: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "2.0", "status": "OK", "read_at": NOW, "selected_groups": ["activity", "peers", "capacity"],
        "store_selection": {"path": "/w/.peerhub/core.db", "source": "env"}, "_nearest_store_path": "/w/.peerhub/core.db",
        "configured_profiles": PROFILES,
        "groups": {"activity": {"status": "OK", "data": activity_data(asks)},
                   "peers": {"status": "OK", "data": {"peers": [{"peer_id": p} for p in ("ag", "cc", "cx")]}},
                   "capacity": {"status": "OK", "data": {"observations": observations}}},
        "errors": {}, "alert_sources": dict(SOURCES_READ), "alerts": [], "min_quota": None}
    payload.update(over)
    return payload


def full_payload() -> dict[str, Any]:
    """Every section and every row state: flagged running asks, leads with each ctx form, ended asks of each state,
    pools of each state (with a binding 5-hour row and an n/a note), two alerts and Korean text."""
    asks = [ask(name="review-api", peer="cx", profile="cx.deepthink", lead=("QA", "cc", "9f9f9f9f"), elapsed=960,
                ask_line="Review phase four of the interface and every pin it moves"),
            ask(name="activity-view", profile="cc.fable", elapsed=300, alive=False,
                ask_line="평가 요청: 저장소의 활동 화면을 읽기 전용으로 검토해 주세요 그리고 결과를"),
            ask(elapsed=41, alive=None, alive_reason="other host"),
            ask("overdue", name="late-task", peer="ag", profile="ag.effort", elapsed=4000, lease=-120.0),
            ask("empty", peer="ag", profile="ag.standard", name="denied-postinstall", age=180, exact=True,
                ask_line=None, note="denied: command"),
            ask("ok", peer="ag", profile="ag.effort", name="rank-r1/ag", age=120, exact=True,
                reply_line="결론: 모두 닫힘 (C1-C5 100)"),
            ask("unsure", profile="cc.fable", name="shipped-check", age=1980, exact=False),
            ask("error", peer="cx", profile="cx.effort", name="build-docs", age=2400, exact=True, reply_line="exit 2")]
    aged = pool("ag", "A-7D", .79, pace=14)
    aged.update(state="STALE", age_seconds=540, measurements=None)
    absent = pool("cx", "X-30D", None)
    absent.update(state="ABSENT", stored_state="ABSENT", measurements=None, last_measurements=None,
                  reason="not reported by provider; plan=prolite")
    unknown = pool("cc", "F-7D", None)
    unknown.update(state="UNKNOWN", stored_state="UNKNOWN", measurements=None, last_measurements=None)
    passed = pool("ag", "G-7D", .97, pace=-4, reset=-1)
    observations = [pool("ag", "3P-5H", .50), pool("ag", "3P-7D", .01, pace=71), aged, pool("ag", "G-5H", .40), passed,
                    pool("cc", "C-5H", .30), pool("cc", "C-7D", .63, pace=-20), unknown,
                    pool("cx", "X-5H", 0.0, reset=3600), pool("cx", "X-7D", .42, fast=True, pace=31), absent]
    payload = _payload(asks, observations, min_quota={"pool": "ag 3P-7D", "left_pct": 1, "state": "low"}, alerts=[
        {"code": "a2", "kind": "gone", "subject": "cc.fable activity-view", "age_s": 300.0, "severity": "high"},
        {"code": "a3", "kind": "pool_out", "subject": "cx X-5H", "age_s": None, "severity": "high"}])
    data = payload["groups"]["activity"]["data"]
    data["leads"] = [lead("docs-site", running=1, session=sess()),
                     lead("shop-4f", lead_id="2b3c4d5e", running=2, session=sess(alive=None, context_pct=83,
                                                                                title="제목 " * 20)),
                     lead("notes", lead_id="3c4d5e6f", session=sess(context_pct=None, context_window=None)),
                     lead("ci-box", tool="?", lead_id="51c2a9f0", running=1)]
    data["leads_read"] = {"state": "partial", "unreadable": 1, "not_read": 2}
    return payload


def quiet_payload() -> dict[str, Any]:
    """Nothing runs and nothing ended: line 1 is `OK`, RUNNING 0, no LEADS and no RECENT block."""
    return _payload([], [pool("cc", "C-7D", .63, pace=-20), pool("cx", "X-7D", .89)],
                    min_quota={"pool": "cc C-7D", "left_pct": 63, "state": "ok"})


def empty_payload() -> dict[str, Any]:
    """No quota evidence: `POOLS 0` with its two notes; one running ask and one ended ask."""
    return _payload([ask(elapsed=41), ask("ok", name="done", age=60, exact=True, reply_line="fine")], [])


PAYLOADS = {"full": full_payload, "quiet": quiet_payload, "empty": empty_payload}
WIDTHS = (40, 60, 80, 100)
HEIGHTS = (None, 6, 10, 16, 24)
ENCODINGS = (None, "utf-8", "cp949")


def golden_cases() -> Iterator[tuple[str, dict[str, Any], dict[str, Any]]]:
    """(case id, payload, render arguments): the plain view as the monitor calls it (ASCII decoration)."""
    for name, build in PAYLOADS.items():
        for width in WIDTHS:
            for height in HEIGHTS:
                for encoding in ENCODINGS if name == "full" else (None,):
                    yield (f"{name}-w{width}-h{height}-{encoding}", build(),
                           {"view": "plain", "unicode": False, "width": width, "height": height, "text_encoding": encoding})


def blocks(screen: str) -> dict[str, list[str]]:
    """The frame's lines by section: `top` (line 1, line 2 and the lines that belong to it) and one block per head."""
    result: dict[str, list[str]] = {"top": []}
    key = "top"
    for line in screen.splitlines():
        match = _HEAD.match(line)
        if match:
            key = match[1] or match[2]
            assert key not in result, f"two {key} blocks"
            result[key] = []
        result[key].append(line)
    return result


def in_order(screen: str, order: tuple[str, ...]) -> str:
    parts = blocks(screen)
    lines = [*parts["top"], *(line for key in order for line in parts.get(key, []))]
    return "\n".join(lines) + ("\n" if lines else "")


GOLDENS: dict[str, str] = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}


def with_lead_count_labels(screen: str, width: int) -> str:
    """The only new text in these historical payloads: known entry/session counts, ASCII-fitted to width - 1.

    The full payload has four entries, three sessions and a partial read. The empty-quota payload has one
    caller entry, no session and a complete session read. Neither the renderer nor its helpers provide this oracle.
    """
    labels = {
        "1": "LEADS 1 entry | 0 sessions found",
        "4": "LEADS 4 entries | partial (1 unreadable, 2 not read) | 3 sessions found",
    }
    lines = screen.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("LEADS "):
            label = labels[line.split()[1]]
            lines[index] = label if len(label) < width else label[:width - 2] + "~"
    return "\n".join(lines) + ("\n" if lines else "")


def test_the_goldens_cover_every_case():
    assert sorted(GOLDENS) == sorted(case for case, _, _ in golden_cases())
    assert len(GOLDENS) == 100


@pytest.mark.parametrize("case,payload,kwargs", [pytest.param(*item, id=item[0]) for item in golden_cases()])
def test_plain_frame_matches_its_golden(case, payload, kwargs):
    screen = render_grouped_frame(payload, show_quota_notes=True, **kwargs)
    assert screen == GOLDENS[case]
    assert in_order(screen, NEW_ORDER) == screen  # the sections print in the one order
    assert "\x1b" not in screen


@pytest.mark.parametrize("name", list(PAYLOADS))
def test_the_golden_payloads_show_what_they_claim(name):
    heads = [key for key in blocks(GOLDENS[f"{name}-w100-hNone-None"]) if key != "top"]
    expected = {"full": list(NEW_ORDER), "quiet": ["POOLS", "suggest:", "RUNNING"],
                "empty": list(NEW_ORDER)}[name]
    assert heads == expected
