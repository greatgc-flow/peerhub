"""Lead sessions in the monitor read model (#376 design v5 section 3.3; tests 12, 13 and the CLI composition of A-1)."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Callable

import pytest

from peerhub.extensions import monitor_read_model as mrm
from tests.communication.diag.test_monitor_activity import (  # noqa: F401  (db is a fixture)
    NOW, caller, claim, db, delivery, iso, ok_result)

pytestmark = [pytest.mark.integration, pytest.mark.diag]

READ = {"state": "read", "unreadable": 0, "not_read": 0}
Reader = Callable[..., tuple[list[dict[str, Any]], dict[str, Any]]]


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def session(sid: str = "sess-raw-1", *, key: str | None = None, **over: Any) -> dict[str, Any]:
    full = key or digest(sid)
    item: dict[str, Any] = {
        "key": full, "id": full[:8], "tool": "cc", "pid": 4242, "alive": True, "entry": "cli", "name": "docs-site-5a",
        "folder": "docs-site", "cwd": "D:/work/docs-site", "state": "busy", "state_since": NOW - 100,
        "started_at": NOW - 3600, "version": "2.1.323", "active_at": NOW - 12, "model": "claude-fable-5-2",
        "context_tokens": 106_137, "context_window": 1_000_000, "context_pct": 10, "context_at": NOW - 20,
        "title": "Release notes", "prompt_line": "Add the section", "said_line": "Drafted."}
    item.update(over)
    return item


def public(item: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in item.items() if k != "key"}


def reader(sessions: list[dict[str, Any]], read: dict[str, Any] | None = None,
           calls: list[dict[str, Any]] | None = None) -> Reader:
    def read_leads(**facts: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if calls is not None:
            calls.append(facts)
        return deepcopy(sessions), dict(read or READ)
    return read_leads


def frame(db: str, groups: tuple[str, ...] = ("activity",), **kw: Any) -> dict[str, Any]:
    kw.setdefault("read_at", NOW)
    kw.setdefault("liveness", lambda *a, **k: True)
    return mrm.render_monitor_frame(db, list(groups), **kw)


def activity(db: str, **kw: Any) -> dict[str, Any]:
    out = frame(db, **kw)
    assert out["groups"]["activity"]["status"] == "OK", out
    return out["groups"]["activity"]["data"]


def running_ask(db: str, stream: str, sid: str | None, **over: Any) -> None:
    claim(db, stream)
    delivery(db, stream, caller_detail=caller(session=sid, **over))


def ended_ask(db: str, stream: str, sid: str, age: float = 50.0, **over: Any) -> None:
    claim(db, stream, expires=NOW - age)
    delivery(db, stream, certainty="TERMINAL", terminal_at=iso(NOW - age), result=ok_result(),
             caller_detail=caller(session=sid, **over))


# 12. merge on the full digest ---------------------------------------------------------------------------------------
def test_a_caller_lead_and_a_session_with_its_digest_merge_and_the_caller_label_wins(db):
    running_ask(db, "s-1", "sess-raw-1", label="peerhub")
    item = session(name="docs-site-5a")
    data = activity(db, lead_reader=reader([item]))
    assert data["leads"] == [{"label": "peerhub", "tool": "cc", "id": item["id"], "running": 1, "asks_24h": 1,
                              "last_ask_at": NOW - 30.0, "session": public(item)}]
    assert data["leads_read"] == READ


def test_two_digests_with_the_same_first_eight_characters_stay_two_leads(db):
    running_ask(db, "s-1", "sess-raw-1")
    full = digest("sess-raw-1")
    other = full[:8] + ("0" if full[8] != "0" else "1") + full[9:]
    leads = activity(db, lead_reader=reader([session("x", key=other)]))["leads"]
    assert len(leads) == 2 and leads[0]["id"] == leads[1]["id"] == full[:8]
    by_label = {lead["label"]: lead for lead in leads}
    assert by_label["peerhub"]["session"] is None and by_label["peerhub"]["running"] == 1
    assert by_label["docs-site-5a"]["session"] is not None and by_label["docs-site-5a"]["running"] == 0


@pytest.mark.parametrize(("name", "folder", "label"), [("docs-site-5a", "docs", "docs-site-5a"), (None, "docs", "docs"),
                                                       (None, None, "?")])
def test_a_session_without_caller_rows_is_a_lead_of_its_own(db, name, folder, label):
    item = session(name=name, folder=folder)
    data = activity(db, lead_reader=reader([item]))
    assert data["leads"] == [{"label": label, "tool": "cc", "id": item["id"], "running": 0, "asks_24h": 0,
                              "last_ask_at": None, "session": public(item)}]
    assert data["counts"]["leads"] == 0


def test_a_lead_without_a_session_has_session_none(db):
    running_ask(db, "s-1", "sess-raw-1")
    data = activity(db, lead_reader=reader([]))
    assert [lead["session"] for lead in data["leads"]] == [None] and data["leads_read"] == READ


def test_the_order_of_leads(db):
    running_ask(db, "a-1", "lead-a", label="A")
    running_ask(db, "a-2", "lead-a", label="A")
    ended_ask(db, "b-1", "lead-b", label="B")
    ended_ask(db, "h-1", "lead-h", age=20.0, label="H")
    sessions = [
        session("lead-b", alive=True, context_pct=85, active_at=NOW - 500),        # ! and alive: first
        session("lead-c", name="C", alive=None, context_pct=95, active_at=NOW - 40),  # ! but alive unknown: not first
        session("lead-d", name="D", context_pct=10, active_at=NOW - 5),
        session("lead-e", name="E", context_pct=10, active_at=NOW - 50),
        session("lead-h", context_pct=None, active_at=None),                     # newer of active_at and last_ask_at
        session("lead-f", name="F", context_pct=None, active_at=NOW - 60),
        session("lead-g", name="G", context_pct=None, active_at=NOW - 60),
    ]
    leads = activity(db, lead_reader=reader(sessions))["leads"]
    tail = sorted(["F", "G"], key=lambda n: digest(f"lead-{n.lower()}")[:8])
    assert [lead["label"] for lead in leads] == ["B", "D", "H", "C", "E", *tail, "A"]


def test_a_ninety_percent_lead_without_a_known_window_is_not_marked_first(db):
    sessions = [session("lead-x", name="X", context_pct=None, context_window=None, active_at=NOW - 1),
                session("lead-y", name="Y", context_pct=79, active_at=NOW - 100),
                session("lead-z", name="Z", context_pct=80, active_at=NOW - 200)]
    leads = activity(db, lead_reader=reader(sessions))["leads"]
    assert [lead["label"] for lead in leads] == ["Z", "X", "Y"]


def test_sessions_do_not_change_counts_leads(db):
    running_ask(db, "s-1", "sess-raw-1")
    ended_ask(db, "s-2", "lead-2")
    before = activity(db)["counts"]
    after = activity(db, lead_reader=reader([session(), session("lead-2"), session("file-only")]))["counts"]
    assert before == after and after["leads"] == 1


def test_the_digest_never_reaches_the_output(db):
    running_ask(db, "s-1", "sess-raw-1")
    running_ask(db, "s-2", None)  # a caller without a session id: host, pid and creation time
    out = frame(db, lead_reader=reader([session(), session("file-only")]))
    data = out["groups"]["activity"]["data"]
    assert all(set(a["lead"]) == {"label", "tool", "id"} for a in data["asks"] if a["lead"])
    assert all("key" not in lead and (lead["session"] is None or "key" not in lead["session"]) for lead in data["leads"])
    text = json.dumps(out)
    assert digest("sess-raw-1") not in text and digest("file-only") not in text


def test_without_a_reader_no_file_is_read_and_leads_read_is_none(db, monkeypatch):
    from peerhub.extensions import lead_sessions

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the read model must not read lead sessions by itself")

    monkeypatch.setattr(lead_sessions, "read_leads", forbidden)
    monkeypatch.setattr(lead_sessions, "lead_dirs", forbidden)
    running_ask(db, "s-1", "sess-raw-1")
    data = activity(db)
    assert data["leads_read"] is None and [lead["session"] for lead in data["leads"]] == [None]
    assert set(data["asks"][0]["lead"]) == {"label", "tool", "id"}


def test_the_read_model_does_not_import_the_lead_reader():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(mrm))
    names = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    names |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert not any("lead_sessions" in (name or "") for name in names)


# 13. when the reader runs -------------------------------------------------------------------------------------------
def test_the_reader_gets_the_facts_and_runs_after_the_read_transaction(db):
    running_ask(db, "s-1", "sess-raw-1")
    trace: list[str] = []
    calls: list[dict[str, Any]] = []

    def probe(pid: int) -> object:
        return None

    def read_leads(**facts: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        trace.append("READER")
        return reader([], calls=calls)(**facts)

    frame(db, lead_reader=read_leads, trace_callback=trace.append, process_probe=probe)
    assert trace.index("READER") > max(i for i, s in enumerate(trace) if s.strip().upper() == "ROLLBACK")
    assert trace[-1] == "READER"  # nothing is read from the store after the reader
    (facts,) = calls
    assert set(facts) == {"now", "text_on", "secrets", "created"}
    assert facts["now"] == NOW and facts["text_on"] is True and facts["created"] is probe
    assert isinstance(facts["secrets"], list)


def test_the_reader_gets_text_off_and_the_default_probe(db, monkeypatch):
    monkeypatch.setenv("PEERHUB_MONITOR_TEXT", "off")
    calls: list[dict[str, Any]] = []
    activity(db, lead_reader=reader([], calls=calls))
    assert calls[0]["text_on"] is False and calls[0]["created"] is mrm._process_created  # pyright: ignore[reportPrivateUsage]


def test_a_reader_that_raises_gives_no_sessions_and_state_error(db):
    running_ask(db, "s-1", "sess-raw-1")

    def broken(**facts: Any) -> Any:
        raise RuntimeError("reader failed")

    out = frame(db, lead_reader=broken)
    data = out["groups"]["activity"]["data"]
    assert out["status"] == "OK" and data["leads_read"] == {"state": "error", "unreadable": 0, "not_read": 0}
    assert [lead["session"] for lead in data["leads"]] == [None]


@pytest.mark.parametrize("returned", [None, ("x",), ([1], READ), ([], "read")])
def test_a_reader_that_returns_garbage_is_an_error(db, returned):
    data = activity(db, lead_reader=lambda **facts: returned)
    assert data["leads_read"] == {"state": "error", "unreadable": 0, "not_read": 0} and data["leads"] == []


@pytest.mark.parametrize("read", [
    {}, {"state": "read", "unreadable": 0}, {"state": "read", "not_read": 0}, {"unreadable": 0, "not_read": 0},
    {"state": "done", "unreadable": 0, "not_read": 0}, {"state": None, "unreadable": 0, "not_read": 0},
    {"state": "read", "unreadable": True, "not_read": 0}, {"state": "read", "unreadable": 0, "not_read": False},
    {"state": "partial", "unreadable": -1, "not_read": 0}, {"state": "partial", "unreadable": 0, "not_read": -3},
    {"state": "partial", "unreadable": 1.0, "not_read": 0}, {"state": "partial", "unreadable": "1", "not_read": 0},
])
def test_a_malformed_read_object_is_an_error(db, read):
    data = activity(db, lead_reader=lambda **facts: ([session()], read))
    assert data["leads_read"] == {"state": "error", "unreadable": 0, "not_read": 0} and data["leads"] == []


@pytest.mark.parametrize("read", [READ, {"state": "partial", "unreadable": 2, "not_read": 1},
                                  {"state": "error", "unreadable": 0, "not_read": 0}])
def test_a_valid_read_object_is_kept(db, read):
    data = activity(db, lead_reader=lambda **facts: ([session()], dict(read)))
    assert data["leads_read"] == read and len(data["leads"]) == 1


def test_the_reader_is_not_called_when_activity_is_not_selected(db):
    calls: list[dict[str, Any]] = []
    out = frame(db, groups=("store",), lead_reader=reader([session()], calls=calls))
    assert calls == [] and "activity" not in out["groups"]


def test_the_reader_is_not_called_after_an_activity_sql_error(db, monkeypatch):
    monkeypatch.setattr(mrm, "_ACTIVITY_SQL", "SELECT * FROM no_such_table_376")
    calls: list[dict[str, Any]] = []
    out = frame(db, lead_reader=reader([session()], calls=calls))
    assert calls == [] and out["groups"]["activity"]["status"] == "ERROR" and out["groups"]["activity"]["data"] is None


def test_alert_sources_and_alerts_are_those_of_the_frame_without_leads(db):
    running_ask(db, "s-1", "sess-raw-1")
    ended_ask(db, "s-bad", "lead-2")
    claim(db, "s-empty", expires=NOW - 60)
    delivery(db, "s-empty", certainty="TERMINAL", terminal_at=iso(NOW - 60), result={"response": "", "error": None})
    plain = frame(db)
    with_leads = frame(db, lead_reader=reader([session(context_pct=99), session("file-only", alive=None)],
                                              {"state": "partial", "unreadable": 2, "not_read": 1}))
    assert with_leads["alert_sources"] == plain["alert_sources"] and with_leads["alerts"] == plain["alerts"]
    assert plain["alerts"]  # the comparison covers a frame that has an alert

    def broken(**facts: Any) -> Any:
        raise OSError("reader failed")

    failed = frame(db, lead_reader=broken)
    assert failed["alert_sources"] == plain["alert_sources"] and failed["alerts"] == plain["alerts"]


# A-1: the CLI composes the default reader ----------------------------------------------------------------------------
def test_a_cli_snapshot_has_leads_read_from_the_isolated_reader(db, capsys):
    from peerhub.cli.app import main
    code = main(["--db", str(db), "monitor", "--snapshot", "--group", "activity", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and payload["groups"]["activity"]["data"]["leads_read"] == READ


def test_the_cli_watch_passes_the_reader_and_it_reads_the_isolated_folder(db, capsys, monkeypatch):
    from peerhub.cli import monitor
    from peerhub.cli.app import main
    monkeypatch.setattr(monitor, "refresh_with_profile_pools", lambda *a, **k: {"status": "OK", "observations": []})
    code = main(["--db", str(db), "monitor", "--json", "--cycles", "1", "--group", "activity"])
    first = json.loads(capsys.readouterr().out.splitlines()[0])
    assert code == 0 and first["snapshot"]["groups"]["activity"]["data"]["leads_read"] == READ


def test_both_cli_call_sites_pass_one_reader_that_looks_up_the_lead_module_at_call_time(db, capsys, monkeypatch):
    from peerhub.cli import monitor
    from peerhub.cli.app import main
    from peerhub.extensions import lead_sessions
    seen: list[object] = []
    real = mrm.render_monitor_frame

    def render(*args: Any, **kw: Any) -> dict[str, Any]:
        seen.append(kw.get("lead_reader"))
        return real(*args, **kw)

    monkeypatch.setattr(mrm, "render_monitor_frame", render)
    monkeypatch.setattr(monitor, "refresh_with_profile_pools", lambda *a, **k: {"status": "OK", "observations": []})
    marker = [session("from-the-patched-module")]
    monkeypatch.setattr(lead_sessions, "read_leads", lambda dirs, **facts: (deepcopy(marker), dict(READ)))
    main(["--db", str(db), "monitor", "--snapshot", "--group", "activity", "--json"])
    main(["--db", str(db), "monitor", "--json", "--cycles", "1", "--group", "activity"])
    out = capsys.readouterr().out.splitlines()
    assert len(seen) >= 2 and all(callable(item) for item in seen) and len(set(map(id, seen))) == 1
    snapshot = json.loads(out[0])["groups"]["activity"]["data"]
    assert [lead["session"]["id"] for lead in snapshot["leads"]] == [marker[0]["id"]]
