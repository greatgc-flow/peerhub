"""#376 P2 (view): the estimated context of a cc session in the sessions, context and peers sections of --expand, always
as an estimate with its age; nothing in the compact frame.

#412 moved the context of a RECENT row: it is no longer joined from the SESSIONS sample by the view. The read model gives
each recent ask its own `session` (the newest session of its stream, read in the activity statement), and the view shows
`ctx 62k/1M 6%` on a cc row only; see test_monitor_recent_layout.py and test_monitor_recent_store.py.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from peerhub.cli.monitor_view import render_grouped_frame
from tests.communication.diag.test_monitor_activity_view import NOW, frame  # noqa: F401

UNKNOWN: dict[str, Any] = {"occupancy_tokens": None, "window_tokens": None, "percent": None, "source": None,
                           "observed_at": None, "window_reported_at": None, "state": "UNKNOWN",
                           "reason": "no_context_measurement"}
DONE = "peer:cc:shipped15-cc2"  # the ended cc ask of the fixture
RUNNING = "peer:cc:rank-374-r1/cc"  # a running cc ask of the fixture


def estimated(percent: object = 2, at: object = NOW - 180, **over: Any) -> dict[str, Any]:
    context: dict[str, Any] = {"occupancy_tokens": 20_113, "window_tokens": 1_000_000, "percent": percent,
                               "source": "cc.stream.context", "observed_at": at, "window_reported_at": at,
                               "state": "ESTIMATED", "reason": None}
    context.update(over)
    return context


def session(stream: str, context: dict[str, Any], *, peer: str = "cc", generation: int = 1) -> dict[str, Any]:
    return {"handle": f"sess_{peer}_{generation}_{len(stream):03d}", "stream_id": stream, "peer_id": peer,
            "generation": generation, "provider": peer, "state": "ACTIVE", "resumable": True, "execution_mode": "fresh",
            "uses": 1, "configured_binding": {"profile": f"{peer}.fable", "model": "model-x", "effort": "high"},
            "telemetry": {"usage": None, "context": context}}


def with_sessions(payload: dict[str, Any], *items: dict[str, Any]) -> dict[str, Any]:
    """The sessions and context groups as the read model gives them."""
    payload["selected_groups"] = [*payload["selected_groups"], "sessions", "context"]
    payload["groups"]["sessions"] = {"status": "OK", "data": {"items": list(items), "sample_size": len(items),
                                                              "truncated": False}}
    payload["groups"]["context"] = {"status": "OK", "data": {"sessions": [
        {"handle": item["handle"], "peer_id": item["peer_id"], "generation": item["generation"],
         "context": item["telemetry"]["context"]} for item in items]}}
    return payload


def expanded(payload: dict[str, Any], *, view: str = "plain", width: int = 140, **kw: Any) -> list[str]:
    return render_grouped_frame(deepcopy(payload), view=view, unicode=view == "rich", width=width, expand=True,
                                **kw).splitlines()


def part(lines: list[str], start: str, end: tuple[str, ...]) -> list[str]:
    """The lines from the head `start` up to a line that starts with one of `end` (the rich view draws a head as a
    rule: `── RECENT 4 ──`)."""
    first = next(i for i, line in enumerate(lines) if line.removeprefix("── ").startswith(start))
    last = next((i for i in range(first + 1, len(lines)) if lines[i].removeprefix("── ").startswith(end)), len(lines))
    return lines[first:last]


# ---- the sessions group no longer decides what a RECENT row shows ---------------------------------------------------------
@pytest.mark.parametrize("view", ["plain", "rich"])
def test_a_sessions_item_does_not_put_an_estimate_on_a_recent_row(frame, view):
    with_sessions(frame, session(DONE, estimated()), session(RUNNING, estimated(90)))
    lines = expanded(frame, view=view)
    assert not any("% est," in line for line in part(lines, "RECENT ", ("LEADS ",)))  # #412: the ask row carries it
    assert not any("90%" in line for line in part(lines, "RUNNING ", ("POOLS", "RECENT", "LEADS")))


# ---- nothing in the compact frame ------------------------------------------------------------------------------------
@pytest.mark.parametrize("view", ["plain", "rich"])
def test_the_compact_frame_is_unchanged_by_an_estimate(frame, view):
    before = render_grouped_frame(deepcopy(frame), view=view, unicode=view == "rich", width=100)
    with_sessions(frame, session(DONE, estimated()), session(RUNNING, estimated(90)))
    frame["groups"]["peers"]["data"]["peers"][1]["context"] = {
        "percent": 2, "occupancy_tokens": 20_113, "window_tokens": 1_000_000, "observed_at": NOW - 180, "state": "ESTIMATED"}
    after = render_grouped_frame(deepcopy(frame), view=view, unicode=view == "rich", width=100)
    assert after == before and " est" not in after and "90%" not in after


# ---- the sessions, context and peers sections of --expand ------------------------------------------------------------
def test_the_sessions_section_shows_the_estimate_as_its_last_field(frame):
    with_sessions(frame, session(DONE, estimated(), generation=2), session("peer:ag:x", UNKNOWN, peer="ag"))
    block = part(expanded(frame, width=160), "-- SESSIONS [", ("-- ",))
    cc_row, ag_row = (next(line for line in block if line.lstrip().startswith(peer + " ")) for peer in ("cc", "ag"))
    assert cc_row.endswith("| resumable | ctx 2% est, 3m ago") and "gen 2" in cc_row
    assert ag_row.endswith("| resumable") and "ctx" not in ag_row


def test_a_narrow_sessions_section_drops_the_estimate_whole_and_says_so(frame):
    with_sessions(frame, session(DONE, estimated()))
    block = part(expanded(frame, width=60), "-- SESSIONS [", ("-- ",))
    assert not any("% est," in line for line in block)
    assert "ctx" in " ".join(line for line in block if "omitted" in line.lower())


@pytest.mark.parametrize("at", [None, "yesterday", True, float("nan")])
def test_an_estimate_without_a_time_says_so_in_the_sessions_section(frame, at):
    with_sessions(frame, session(DONE, estimated(at=at)))
    assert any(line.endswith("| ctx 2% est, age ?") for line in part(expanded(frame, width=160), "-- SESSIONS [", ("-- ",)))


@pytest.mark.parametrize("context", [
    estimated(None), estimated(True), estimated("2"), estimated(101), estimated(-1), estimated(float("nan")),
    estimated(state="UNKNOWN"), estimated(state="MEASURED"), estimated(state=None), "garbage", None])
def test_only_a_valid_estimate_is_shown(frame, context):
    with_sessions(frame, session(DONE, UNKNOWN))
    frame["groups"]["sessions"]["data"]["items"][0]["telemetry"]["context"] = context
    assert not any("% est," in line for line in expanded(frame))


def test_the_context_section_appears_for_an_estimate_and_never_calls_it_measured(frame):
    with_sessions(frame, session(DONE, estimated()), session("peer:ag:x", UNKNOWN, peer="ag"))
    lines = expanded(frame)
    block = part(lines, "-- CONTEXT [", ("-- ",))
    row = next(line for line in block if line.lstrip().startswith("cc "))
    assert row.strip().startswith("cc 2% est, 3m ago ") and row.endswith("| 20,113 of 1,000,000 tokens")
    assert "MEASURED" not in " ".join(block) and "ESTIMATED" not in " ".join(block)
    assert any(line.strip() == "ag UNKNOWN | no_context_measurement" for line in block)


def test_the_context_section_stays_hidden_without_a_measured_or_estimated_session(frame):
    with_sessions(frame, session(DONE, UNKNOWN), session("peer:ag:x", UNKNOWN, peer="ag"))
    lines = expanded(frame)
    assert not any("CONTEXT [" in line for line in lines) and "no_context_measurement" not in " ".join(lines)


def test_a_measured_context_row_is_unchanged(frame):
    with_sessions(frame, session(DONE, {"percent": 25.0, "state": "MEASURED", "reason": None}))
    block = part(expanded(frame), "-- CONTEXT [", ("-- ",))
    assert any(line.strip().startswith("cc 25% ") and line.endswith("| MEASURED") for line in block)
    assert not any("% est," in line for line in block)


def test_the_peers_section_shows_the_newest_session_estimate(frame):
    peers = frame["groups"]["peers"]["data"]["peers"]
    peers[1].update(session={"generation": 2, "execution_mode": "reused", "resumable": True},
                    context={"percent": 2, "occupancy_tokens": 20_113, "window_tokens": 1_000_000,
                             "observed_at": NOW - 180, "state": "ESTIMATED"})
    frame["selected_groups"] = ["peers"]
    lines = expanded(frame)
    assert "-> latest session: reused gen 2 -> context: 2% est, 3m ago" in lines
    assert sum("% est," in line for line in lines) == 1
    narrow = expanded(frame, width=44)  # below 48 columns a peer is one line of whole fields: the estimate goes whole
    assert not any("% est," in line for line in narrow)
    assert "ctx" in " ".join(line for line in narrow if "omitted" in line.lower())
    peers[1]["context"]["state"] = "UNKNOWN"
    assert not any("context:" in line for line in expanded(frame))


def test_the_legend_says_what_the_estimate_is(frame):
    from peerhub.cli.monitor_legend import MONITOR_LEGEND

    line = next(text for text in MONITOR_LEGEND if text.startswith("CTX est:"))
    assert line == ("CTX est: estimated context of a cc session at the end of its last ask (percent of the model's maximum"
                    " input, with its age); not a live measurement")
    with_sessions(frame, session(DONE, estimated()))
    assert line in " ".join(expanded(frame, width=200))


def test_a_measured_peer_context_is_unchanged(frame):
    peers = frame["groups"]["peers"]["data"]["peers"]
    peers[2].update(session={"generation": 0, "execution_mode": "fresh", "resumable": True},
                    context={"percent": 22.0, "state": "MEASURED", "occupancy_tokens": 45000})
    frame["selected_groups"] = ["peers"]
    assert any("-> context: 45k (22%)" in line for line in expanded(frame))


# ---- end to end: a real store, the read model's ask row and session item, the --expand frame ------------------------
def test_a_real_cc_ask_shows_its_estimate_in_expand_from_the_store_to_the_frame(tmp_path, bridge_h):
    import json

    from peerhub.extensions.monitor_read_model import render_monitor_frame
    from tests.communication.adapters.test_cc_telemetry_events import REAL_CC_STREAM, _cc_target  # pyright: ignore[reportPrivateUsage]
    from tests.communication.bridge_helpers import bseed

    bseed(bridge_h)  # one ask of a cc runtime: the adapter reads the context from the recorded stream of a real ask
    assert bridge_h.delivery_cycle("b", "s", _cc_target(tmp_path, REAL_CC_STREAM, 0)).status == "delivered"
    with bridge_h.cs.transaction() as conn:
        receipt = json.loads(conn.execute("SELECT detail FROM bridge_evidence WHERE kind='session_telemetry'").fetchone()[0])
    groups = ["activity", "peers", "sessions", "context"]
    payload = render_monitor_frame(bridge_h.db_path, groups, read_at=receipt["observed_at"] + 200,
                                   liveness=lambda *a, **k: True)
    payload.update(selected_groups=groups, store_selection={"path": str(bridge_h.db_path), "source": "explicit"})
    (row,) = payload["groups"]["activity"]["data"]["asks"]
    (item,) = payload["groups"]["sessions"]["data"]["items"]
    assert (row["stream"], row["peer"]) == (item["stream_id"], item["peer_id"]) == ("s", "b")
    assert row["state"] == "ok" and row["started_at"] <= receipt["observed_at"] <= row["ended_at"]
    assert item["telemetry"]["context"]["state"] == "ESTIMATED" and item["telemetry"]["context"]["percent"] == 2
    # #412: the ask row carries the session of its stream, read in the activity statement (not the SESSIONS sample)
    assert row["session"]["generation"] == item["generation"] and row["session"]["context"]["percent"] == 2
    assert row["session"]["provider"] == "cc" and row["session"]["context"]["state"] == "ESTIMATED"
    lines = expanded(payload, width=160)
    ended = part(lines, "RECENT ", ("LEADS ",))
    assert ended[0] == "RECENT 1 (0 running)" and "ctx 26k/1M 2% now" in ended[1]
    assert any(line.endswith("| ctx 2% est, 3m ago") for line in part(lines, "-- SESSIONS [", ("-- ",)))
    assert any(line.startswith("b 2% est, 3m ago ") and line.endswith("| 25,848 of 1,000,000 tokens")
               for line in part(lines, "-- CONTEXT [", ("-- ",)))
    assert any(line.endswith("-> context: 2% est, 3m ago") for line in part(lines, "-- PEERS [", ("-- ",)))
    compact = render_grouped_frame(deepcopy(payload), view="plain", unicode=False, width=100)
    assert "% est," not in compact and "ctx" not in compact and "62k" not in compact
    row_line = next(line for line in compact.splitlines() if line.startswith(" ok"))
    assert "26k/2%" in row_line  # the compact RECENT row: tokens/percent of the stream's newest session
