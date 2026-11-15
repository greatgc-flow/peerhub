"""#376 P2 (view): the estimated context of a cc session in --expand: on the RECENT row of its ask and in the sessions,
context and peers sections. Always as an estimate with its age; never on a RUNNING row; nothing in the compact frame."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from peerhub.cli.monitor_view import render_grouped_frame, _width
from tests.communication.diag.test_monitor_activity_view import NOW, activity_data, ask, frame  # noqa: F401

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
    """The sessions and context groups as the read model gives them, and each peer's newest context."""
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
    first = next(i for i, line in enumerate(lines) if line.startswith(start))
    last = next((i for i in range(first + 1, len(lines)) if lines[i].startswith(end)), len(lines))
    return lines[first:last]


def asks_of(lines: list[str], head: str, end: tuple[str, ...]) -> list[str]:
    """Each ask of one activity block (RUNNING or RECENT) as one text; an ask starts with `- `."""
    chunks: list[str] = []
    for line in part(lines, head, end)[1:]:
        if line.startswith("- "):
            chunks.append(line)
        else:
            chunks[-1] += "\n" + line
    return chunks


def recent(lines: list[str]) -> list[str]:
    return asks_of(lines, "RECENT ", ("LEADS ",))


def running(lines: list[str]) -> list[str]:
    return asks_of(lines, "RUNNING ", ("RECENT ",))


# ---- RECENT rows of --expand -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("view", ["plain", "rich"])
def test_the_recent_row_of_a_cc_ask_shows_its_estimate_with_the_age(frame, view):
    with_sessions(frame, session(DONE, estimated(50, NOW - 9000), generation=1), session(DONE, estimated(), generation=2),
                  session("peer:ag:rank-374-r1/ag", UNKNOWN, peer="ag"))
    lines = expanded(frame, view=view)
    rows = recent(lines)
    (row,) = [text for text in rows if "name shipped15-cc2" in text]
    assert "ctx 2% est, 3m ago" in row  # the newest generation: percent, the word `est`, the age of the estimate
    assert "50%" not in row
    assert sum("ctx " in text for text in rows) == 1  # no other ended ask has an estimate
    assert all(_width(line) <= 139 for line in lines)


def test_the_estimate_follows_the_tokens_field_and_wraps_as_one_field(frame):
    with_sessions(frame, session(DONE, estimated()))
    (row,) = [text for text in recent(expanded(frame, width=60)) if "shipped15-cc2" in text]
    assert "ctx 2% est, 3m ago" in row  # never split between two lines
    wide = next(text for text in recent(expanded(frame, width=200)) if "shipped15-cc2" in text)
    assert "tokens ? | ctx 2% est, 3m ago" in wide


def test_the_newest_generation_decides_and_an_unknown_one_shows_nothing(frame):
    with_sessions(frame, session(DONE, estimated(), generation=1), session(DONE, UNKNOWN, generation=2))
    assert not any("ctx " in text for text in recent(expanded(frame)))


@pytest.mark.parametrize("item", [session(DONE, estimated(), peer="ag"), session("peer:cc:another", estimated())])
def test_stream_and_peer_must_both_match(frame, item):
    with_sessions(frame, item)
    assert not any("ctx " in text for text in recent(expanded(frame)))


def test_a_running_ask_never_shows_a_context(frame):
    with_sessions(frame, session(RUNNING, estimated()), session(DONE, estimated()))
    lines = expanded(frame)
    assert len(running(lines)) == 3 and not any("ctx" in text or "% est," in text for text in running(lines))
    assert any("ctx 2% est, 3m ago" in text for text in recent(lines))


def test_a_superseded_ask_shows_no_context_of_the_session_that_replaced_it(frame):
    asks = frame["groups"]["activity"]["data"]["asks"]
    asks.append(ask("unsure", name="shipped15-cc2", profile="cc.fable", superseded=True, note="superseded", age=4000,
                    exact=True))
    frame["groups"]["activity"]["data"] = activity_data(asks)
    with_sessions(frame, session(DONE, estimated()))
    rows = recent(expanded(frame))
    assert [("ctx " in text, "(superseded)" in text) for text in rows if "name shipped15-cc2" in text] == [
        (True, False), (False, True)]


@pytest.mark.parametrize("order", ["newest first", "newest last"])
def test_only_the_newest_ended_ask_of_a_stream_and_peer_shows_the_estimate(frame, order):
    # The estimate is about the end of the session's last ask: an ask of the same stream that ended hours earlier
    # must not read `ctx 2% est, 3m ago`.
    asks = frame["groups"]["activity"]["data"]["asks"]
    older = [ask("ok", name="shipped15-cc2", profile="cc.fable", age=7200, exact=True, reply_line="first answer"),
             ask("error", name="shipped15-cc2", profile="cc.fable", age=90_000, exact=True, reply_line="exit 2")]
    frame["groups"]["activity"]["data"] = activity_data([*asks, *older] if order == "newest first" else [*older, *asks])
    with_sessions(frame, session(DONE, estimated()))
    rows = [text for text in recent(expanded(frame)) if "name shipped15-cc2" in text]
    assert [("age 33m" in text, "age 2h00m" in text, "age 1d01h" in text) for text in rows] == [
        (True, False, False), (False, True, False), (False, False, True)]  # the newest ended ask comes first
    assert ["ctx 2% est, 3m ago" in text for text in rows] == [True, False, False]
    assert sum("% est," in text for text in recent(expanded(frame))) == 1


def test_the_newest_ended_ask_of_each_stream_and_peer_shows_its_own_estimate(frame):
    with_sessions(frame, session(DONE, estimated()), session("peer:ag:rank-374-r1/ag", estimated(7, NOW - 60), peer="ag"))
    rows = recent(expanded(frame))
    assert [("ctx 7% est, 1m ago" in text, "ctx 2% est, 3m ago" in text) for text in rows
            if "% est," in text] == [(True, False), (False, True)]


@pytest.mark.parametrize("change,age", [({}, "3m ago"), ({"display_elapsed_seconds": 60}, "4m ago"),
                                        ({"display_elapsed_seconds": 7200}, "2h03m ago")])
def test_the_age_is_counted_at_the_time_of_the_frame(frame, change, age):
    with_sessions(frame, session(DONE, estimated()))
    frame.update(change)
    assert any(f"ctx 2% est, {age}" in text for text in recent(expanded(frame)))


@pytest.mark.parametrize("at", [None, "yesterday", True, float("nan")])
def test_an_estimate_without_a_time_says_so(frame, at):
    with_sessions(frame, session(DONE, estimated(at=at)))
    assert any("ctx 2% est, age ?" in text for text in recent(expanded(frame)))


def test_an_estimate_from_the_future_is_not_given_a_negative_age(frame):
    with_sessions(frame, session(DONE, estimated(at=NOW + 500)))
    assert any("ctx 2% est, 0s ago" in text for text in recent(expanded(frame)))


@pytest.mark.parametrize("context", [
    estimated(None), estimated(True), estimated("2"), estimated(101), estimated(-1), estimated(float("nan")),
    estimated(state="UNKNOWN"), estimated(state="MEASURED"), estimated(state=None), "garbage", None])
def test_only_a_valid_estimate_is_shown(frame, context):
    with_sessions(frame, session(DONE, UNKNOWN))
    frame["groups"]["sessions"]["data"]["items"][0]["telemetry"]["context"] = context
    lines = expanded(frame)
    assert not any("ctx " in text for text in recent(lines)) and not any("% est," in line for line in lines)


def test_without_the_sessions_group_there_is_nothing_to_join(frame):
    with_sessions(frame, session(DONE, estimated()))
    frame["groups"].pop("sessions")
    frame["selected_groups"].remove("sessions")
    assert not any("ctx " in text for text in recent(expanded(frame)))


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
    from peerhub.cli.monitor_view import MONITOR_LEGEND

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
