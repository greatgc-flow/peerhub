"""Lead sessions read from Claude Code's local files (#376 design v5, sections 3.1, 3.2, 3.5-3.7; tests 1-11 and 18)."""
from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from peerhub.extensions import lead_sessions as ls
from peerhub.extensions.lead_sessions import lead_dirs as real_lead_dirs  # bound before the autouse fixture patches it

NOW = 1_763_200_000.0
HOST = "box"
SID = "11111111-2222-3333-4444-555555555555"
TICKS = 134_000_000_000_000_001
STARTED = NOW - 3_600
MISSING = object()
SECRET = "hunter2-very-secret"


def iso(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def write_session(root: Path, pid: int = 4242, file: str | None = None, **over: object) -> Path:
    item: dict[str, object] = {
        "pid": pid, "sessionId": SID, "cwd": "D:/work/docs-site", "startedAt": STARTED * 1000, "procStart": str(TICKS),
        "version": "2.1.323", "kind": "interactive", "entrypoint": "cli", "pidDomain": f"win32:{HOST}",
        "name": "docs-site-5a", "nameSource": "derived", "status": "busy", "updatedAt": (NOW - 100) * 1000,
        "statusUpdatedAt": (NOW - 100) * 1000}
    item.update(over)
    item = {k: v for k, v in item.items() if v is not MISSING}
    folder = root / "sessions"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (file or f"{pid}.json")
    path.write_text(json.dumps(item), encoding="utf-8")
    return path


def assistant(*, model: object = "claude-fable-5-2", usage: object = MISSING, sidechain: object = False,
              ts: object = "2026-11-15T00:08:28Z", content: object = MISSING, message: object = MISSING) -> dict[str, object]:
    body: dict[str, object] = {"model": model,
                               "usage": {"input_tokens": 100, "cache_read_input_tokens": 1000,
                                         "cache_creation_input_tokens": 10, "output_tokens": 5} if usage is MISSING else usage,
                               "content": [{"type": "text", "text": "Done."}] if content is MISSING else content}
    return {"type": "assistant", "isSidechain": sidechain, "timestamp": ts, "message": body if message is MISSING else message}


def write_transcript(root: Path, lines: list[object], *, sid: str = SID, project: str = "D--work-docs",
                     age: float = 60, raw: bytes | None = None) -> Path:
    folder = root / "projects" / project
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sid}.jsonl"
    path.write_bytes(raw if raw is not None else b"".join(json.dumps(line).encode() + b"\n" for line in lines))
    os.utime(path, (NOW - age, NOW - age))
    return path


def write_catalog(root: Path, models: list[object], *, name: str = "abc-cc.json", age: float = 10) -> Path:
    folder = root / "cache" / "model-catalog"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(json.dumps({"catalog": {"config": {"models": models}}, "version": 2}), encoding="utf-8")
    os.utime(path, (NOW - age, NOW - age))
    return path


def window(model: str, tokens: object) -> dict[str, object]:
    return {"id": model, "runtime": {"max_input_tokens": tokens}}


def read(dirs: list[Path], **kw: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    args: dict[str, Any] = {"now": NOW, "text_on": True, "secrets": [], "created": lambda pid: STARTED - 0.5,
                            "ticks": lambda pid: TICKS, "os_name": "win32", "host": HOST}
    args.update(kw)
    return ls.read_leads([str(d) for d in dirs], **args)


def one(dirs: list[Path], **kw: Any) -> dict[str, Any]:
    sessions, _ = read(dirs, **kw)
    assert len(sessions) == 1, sessions
    return sessions[0]


def clean_read(unreadable: int = 0, not_read: int = 0, state: str | None = None) -> dict[str, object]:
    return {"state": state or ("partial" if unreadable + not_read else "read"), "unreadable": unreadable, "not_read": not_read}


# 1. lead_dirs ---------------------------------------------------------------------------------------------------------
def test_lead_dirs_default_is_the_home_claude_folder():
    assert real_lead_dirs({}) == [os.path.abspath(os.path.expanduser("~/.claude"))]
    assert real_lead_dirs({"CLAUDE_CONFIG_DIR": ""}) == [os.path.abspath(os.path.expanduser("~/.claude"))]


def test_lead_dirs_claude_config_dir_then_registered_folders_in_order(tmp_path: Path):
    a, b, c = (str(tmp_path / n) for n in "abc")
    env = {"CLAUDE_CONFIG_DIR": a, "PEERHUB_LEAD_DIRS": os.pathsep.join(["", b, a, "", c, b + os.sep + ".", ""])}
    assert real_lead_dirs(env) == [a, b, c]


def test_lead_dirs_duplicates_compare_with_normcase(tmp_path: Path):
    a = str(tmp_path / "Claude")
    dirs = real_lead_dirs({"CLAUDE_CONFIG_DIR": a, "PEERHUB_LEAD_DIRS": a.upper()})
    assert dirs == ([a] if os.path.normcase("A") == "a" else [a, a.upper()])


def test_lead_dirs_expand_the_user_and_make_relative_entries_absolute():
    dirs = real_lead_dirs({"CLAUDE_CONFIG_DIR": "rel-claude", "PEERHUB_LEAD_DIRS": "~/other"})
    assert dirs == [os.path.abspath("rel-claude"), os.path.abspath(os.path.expanduser("~/other"))]


def test_nine_folders_read_eight_and_count_one_not_read(tmp_path: Path):
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "d0"),
           "PEERHUB_LEAD_DIRS": os.pathsep.join(str(tmp_path / f"d{i}") for i in range(1, 9))}
    dirs = real_lead_dirs(env)
    assert len(dirs) == 9
    write_session(Path(dirs[8]))  # the ninth folder is never opened
    sessions, state = ls.read_leads(dirs, now=NOW, text_on=True, secrets=[], created=lambda pid: STARTED,
                                    ticks=lambda pid: TICKS, os_name="win32", host=HOST)
    assert sessions == [] and state == clean_read(not_read=1)
    assert ls.LEAD_DIRS_CAP == 8


# 2-3. folders ---------------------------------------------------------------------------------------------------------
def test_no_sessions_folder_is_an_empty_read(tmp_path: Path):
    assert read([tmp_path / "absent"]) == ([], clean_read())
    assert read([tmp_path]) == ([], clean_read())


def test_a_listing_error_is_error_and_the_next_folder_is_still_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bad, good = tmp_path / "bad", tmp_path / "good"
    write_session(bad, pid=1)
    write_session(good)
    real = ls._listdir  # pyright: ignore[reportPrivateUsage]

    def listdir(path: str) -> list[str]:
        if os.path.normcase(path) == os.path.normcase(os.path.realpath(bad / "sessions")):
            raise PermissionError(13, "denied")
        return real(path)

    monkeypatch.setattr(ls, "_listdir", listdir)
    sessions, state = read([bad, good])
    assert [s["pid"] for s in sessions] == [4242]
    assert state == {"state": "error", "unreadable": 0, "not_read": 0}


def test_sessions_that_is_a_file_is_error(tmp_path: Path):
    tmp_path.joinpath("sessions").write_text("x", encoding="utf-8")
    assert read([tmp_path])[1]["state"] == "error"


# 4. candidates --------------------------------------------------------------------------------------------------------
def test_names_that_are_not_digits_dot_json_are_ignored(tmp_path: Path):
    for name in ("abc.json", "12.json.bak", "12345678901.json", "-1.json", "4242.JSON.txt"):
        write_session(tmp_path, file=name)
    calls: list[int] = []
    assert read([tmp_path], created=lambda pid: calls.append(pid)) == ([], clean_read())
    assert calls == []


def bad_candidates(root: Path) -> None:
    write_session(root, pid=11, file="12.json")  # name differs from pid
    big = write_session(root, pid=13)
    big.write_bytes(big.read_bytes() + b" " * (ls.LEAD_FILE_MAX + 1 - big.stat().st_size))
    assert big.stat().st_size == 16_385
    (root / "sessions" / "14.json").write_bytes(b"{not json")
    (root / "sessions" / "15.json").write_text("[1, 2]", encoding="utf-8")
    write_session(root, pid=16, sessionId="../../etc/passwd")
    write_session(root, pid=17, startedAt=MISSING)
    write_session(root, pid=18, startedAt=True)
    (root / "sessions" / "19.json").write_text(json.dumps({"pid": 19, "sessionId": SID, "startedAt": "x"}).replace(
        '"x"', "Infinity"), encoding="utf-8")
    write_session(root, file="20.json", pid=True)
    (root / "sessions" / "21.json").write_bytes(b"\xff\xfe{}")


BAD_PIDS = [12, 13, 14, 15, 16, 17, 18, 19, 20, 21]


def test_invalid_candidates_count_unreadable_when_a_process_has_the_pid_of_the_name(tmp_path: Path):
    bad_candidates(tmp_path)
    asked: list[int] = []
    sessions, state = read([tmp_path], created=lambda pid: asked.append(pid) or 1.0)
    assert sessions == [] and state == clean_read(unreadable=len(BAD_PIDS))
    assert sorted(asked) == BAD_PIDS


def test_invalid_candidates_are_ignored_when_no_process_has_the_pid(tmp_path: Path):
    bad_candidates(tmp_path)
    assert read([tmp_path], created=lambda pid: None) == ([], clean_read())


@pytest.mark.parametrize("over", [{"kind": "bg"}, {"kind": MISSING}, {"entrypoint": "sdk-cli"}, {"entrypoint": "sdk-ts"},
                                  {"entrypoint": MISSING}, {"entrypoint": 7}])
def test_ordinary_exclusions_have_no_count(tmp_path: Path, over: dict[str, object]):
    write_session(tmp_path, **over)
    assert read([tmp_path]) == ([], clean_read())


def test_sixty_five_candidates_read_the_sixty_four_newest(tmp_path: Path):
    for pid in range(1, 66):
        path = write_session(tmp_path, pid=pid, sessionId=f"session-{pid:04d}")
        os.utime(path, (NOW - 1000 + pid, NOW - 1000 + pid))
    sessions, state = read([tmp_path], ticks=lambda pid: TICKS)
    assert len(sessions) == 64 and 1 not in {s["pid"] for s in sessions}
    assert state == clean_read(not_read=1)


# 5. liveness ----------------------------------------------------------------------------------------------------------
def probe(value: object, calls: list[int] | None = None) -> Callable[[int], object]:
    def created(pid: int) -> object:
        if calls is not None:
            calls.append(pid)
        return value
    return created


@pytest.mark.parametrize("domain", [MISSING, 7, "win32:other", "linux:box", "WIN32:box", None])
def test_another_or_missing_machine_is_unknown_and_no_probe_is_called(tmp_path: Path, domain: object):
    write_session(tmp_path, pidDomain=domain)
    write_transcript(tmp_path, [assistant()])
    calls: list[int] = []
    session = one([tmp_path], created=probe(STARTED, calls), ticks=probe(TICKS, calls))
    assert session["alive"] is None and calls == []


def test_the_machine_gate_uses_os_name_and_host(tmp_path: Path):
    write_session(tmp_path, pidDomain="linux:node7")
    assert one([tmp_path], os_name="linux", host="node7")["alive"] is True


def test_the_host_defaults_to_this_machine_in_lower_case(tmp_path: Path):
    import platform
    write_session(tmp_path, pidDomain=f"win32:{platform.node().lower()}")
    assert one([tmp_path], host=None)["alive"] is True


def test_no_process_with_the_pid_is_excluded(tmp_path: Path):
    write_session(tmp_path)
    write_transcript(tmp_path, [assistant()])
    ticked: list[int] = []
    assert read([tmp_path], created=lambda pid: None, ticks=probe(TICKS, ticked)) == ([], clean_read())
    assert ticked == []  # the process must exist before any identity is compared


def test_equal_ticks_are_alive_even_when_the_float_is_twelve_ticks_away(tmp_path: Path):
    write_session(tmp_path)
    created = TICKS / 1e7 - 11_644_473_600 + 12e-7
    session = one([tmp_path], created=lambda pid: created)
    assert session["alive"] is True


@pytest.mark.parametrize("delta", [1, -1, 9_999, 10_000_000])
def test_ticks_that_differ_are_excluded(tmp_path: Path, delta: int):
    write_session(tmp_path)
    write_transcript(tmp_path, [assistant()])
    assert read([tmp_path], ticks=lambda pid: TICKS + delta) == ([], clean_read())


NO_IDENTITY: list[dict[str, Any]] = [
    {"session": {"procStart": MISSING}}, {"session": {"procStart": TICKS}}, {"session": {"procStart": ""}},
    {"session": {"procStart": "abc"}}, {"session": {"procStart": "1" * 21}}, {"session": {"procStart": True}},
    {"ticks": None}]


@pytest.mark.parametrize("case", NO_IDENTITY)
def test_no_identity_uses_the_time_bound_only_to_reject(tmp_path: Path, case: dict[str, Any]):
    write_session(tmp_path, **case.get("session", {}))
    write_transcript(tmp_path, [assistant()])
    ticks = (lambda pid: None) if "ticks" in case else (lambda pid: TICKS)
    assert one([tmp_path], created=lambda pid: STARTED + 2.0, ticks=ticks)["alive"] is None
    assert read([tmp_path], created=lambda pid: STARTED + 2.001, ticks=ticks) == ([], clean_read())
    assert one([tmp_path], created=lambda pid: STARTED - 300, ticks=ticks)["alive"] is None


@pytest.mark.parametrize("created", ["denied", True, False, math.inf, math.nan, "12"])
def test_a_creation_time_that_is_not_a_finite_number_is_unknown(tmp_path: Path, created: object):
    write_session(tmp_path, procStart=MISSING)
    write_transcript(tmp_path, [assistant()])
    assert one([tmp_path], created=lambda pid: created)["alive"] is None


def test_linux_without_ticks_is_never_alive(tmp_path: Path):
    write_session(tmp_path, pidDomain="linux:box")
    write_transcript(tmp_path, [assistant()])
    session = one([tmp_path], os_name="linux", ticks=lambda pid: None, created=lambda pid: STARTED - 0.5)
    assert session["alive"] is None


@pytest.mark.parametrize(("age", "kept"), [(23 * 3600, True), (86_400, True), (25 * 3600, False), (None, False)])
def test_an_unknown_session_needs_a_transcript_written_in_the_last_day(tmp_path: Path, age: float | None, kept: bool):
    write_session(tmp_path, pidDomain="win32:other")
    if age is not None:
        write_transcript(tmp_path, [assistant()], age=age)
    sessions, _ = read([tmp_path])
    assert bool(sessions) is kept


def test_an_alive_session_is_kept_without_a_transcript(tmp_path: Path):
    write_session(tmp_path)
    session = one([tmp_path])
    assert session["alive"] is True and session["active_at"] is None and session["context_tokens"] is None


def test_process_ticks_is_none_off_windows_and_out_of_range(monkeypatch: pytest.MonkeyPatch):
    from peerhub.extensions import process_tree
    monkeypatch.setattr(process_tree, "creation_ticks", lambda pid: 77)
    assert ls.process_ticks(4242, os_name="linux") is None
    assert ls.process_ticks(4242, os_name="darwin") is None
    for pid in (0, -1, 2**32, True):
        assert ls.process_ticks(pid, os_name="win32") is None
    assert ls.process_ticks(2**32 - 1, os_name="win32") == 77


def test_process_ticks_is_none_when_the_wrapped_call_fails(monkeypatch: pytest.MonkeyPatch):
    from peerhub.extensions import process_tree
    monkeypatch.setattr(process_tree, "creation_ticks", lambda pid: -1)
    assert ls.process_ticks(4242, os_name="win32") is None

    def boom(pid: int) -> int:
        raise OSError("no")

    monkeypatch.setattr(process_tree, "creation_ticks", boom)
    assert ls.process_ticks(4242, os_name="win32") is None


def test_process_ticks_passes_an_int_that_a_float_cannot_hold(monkeypatch: pytest.MonkeyPatch):
    from peerhub.extensions import process_tree
    value = 2**56 + 1
    assert int(float(value)) != value
    monkeypatch.setattr(process_tree, "creation_ticks", lambda pid: value)
    assert ls.process_ticks(4242, os_name="win32") == value


def test_batch_keeps_the_moved_reader_under_its_old_name():
    from peerhub.extensions import batch, process_tree
    assert batch._creation_time is process_tree.creation_ticks  # pyright: ignore[reportPrivateUsage]


@pytest.mark.skipif(sys.platform != "win32", reason="kernel32 FILETIME reader")
def test_process_ticks_is_the_exact_filetime_of_the_kernel_calls(monkeypatch: pytest.MonkeyPatch):
    import ctypes
    high, low = 0x01DC5A3B, 0x89ABCDEF
    expected = (high << 32) | low
    assert int(float(expected)) != expected

    class Call:
        def __init__(self, fn: Callable[..., object]) -> None:
            self.fn, self.restype = fn, None

        def __call__(self, *args: object) -> object:
            return self.fn(*args)

    def times(handle: object, *refs: Any) -> int:
        refs[0]._obj.dwHighDateTime, refs[0]._obj.dwLowDateTime = high, low
        return 1

    class Kernel:
        OpenProcess = Call(lambda access, inherit, pid: 99)
        GetProcessTimes = Call(times)
        CloseHandle = Call(lambda handle: 1)

    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: Kernel())
    assert ls.process_ticks(4242) == expected


@pytest.mark.skipif(sys.platform != "win32", reason="kernel32 FILETIME reader")
def test_process_ticks_smoke_matches_psutil_for_this_process():
    import psutil  # pyright: ignore[reportMissingModuleSource]
    actual = ls.process_ticks(os.getpid())
    assert actual is not None
    assert abs(actual - (psutil.Process(os.getpid()).create_time() + 11_644_473_600) * 1e7) <= 100
    free = next(p for p in range(2_000_000_000, 2_000_100_000, 4) if not any(psutil.pid_exists(p + i) for i in range(4)))
    assert ls.process_ticks(free) is None


# 6. confinement -------------------------------------------------------------------------------------------------------
def redirect(link: Path, target: Path) -> Callable[[str], str]:
    """An injected resolver that makes `link` behave like a symbolic link or junction to `target`."""
    real = os.path.realpath
    prefix = os.path.normcase(real(link))

    def resolve(path: str) -> str:
        resolved = real(path)
        if os.path.normcase(resolved) == prefix or os.path.normcase(resolved).startswith(prefix + os.sep):
            return real(target) + resolved[len(prefix):]
        return resolved
    return resolve


def full_folder(root: Path) -> None:
    write_session(root)
    write_transcript(root, [assistant()])
    write_catalog(root, [window("claude-fable-5-2", 1_000_000)])


def test_a_full_folder_reads_everything(tmp_path: Path):
    full_folder(tmp_path)
    session = one([tmp_path])
    assert session["context_tokens"] == 1110 and session["context_window"] == 1_000_000
    assert read([tmp_path])[1] == clean_read()


def test_a_link_at_sessions_is_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    full_folder(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    write_session(outside)
    monkeypatch.setattr(ls, "_realpath", redirect(tmp_path / "sessions", outside / "sessions"))
    assert read([tmp_path]) == ([], {"state": "error", "unreadable": 0, "not_read": 0})


def test_a_link_at_a_session_file_is_not_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = write_session(tmp_path)
    monkeypatch.setattr(ls, "_realpath", redirect(path, tmp_path / "elsewhere.json"))
    assert read([tmp_path]) == ([], clean_read(unreadable=1))
    assert read([tmp_path], created=lambda pid: None) == ([], clean_read())


@pytest.mark.parametrize("part", [("projects", "D--work-docs"), ("projects", "D--work-docs", f"{SID}.jsonl"),
                                  ("projects",)])
def test_a_link_in_the_transcript_path_is_not_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, part: tuple[str, ...]):
    full_folder(tmp_path)
    monkeypatch.setattr(ls, "_realpath", redirect(tmp_path.joinpath(*part), tmp_path.parent / "outside"))
    session = one([tmp_path])
    assert session["context_tokens"] is None and session["active_at"] is None
    assert read([tmp_path])[1] == clean_read(unreadable=1)


@pytest.mark.parametrize("part", [("cache",), ("cache", "model-catalog"), ("cache", "model-catalog", "abc-cc.json")])
def test_a_link_in_the_catalog_path_is_not_used(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, part: tuple[str, ...]):
    full_folder(tmp_path)
    monkeypatch.setattr(ls, "_realpath", redirect(tmp_path.joinpath(*part), tmp_path.parent / "outside"))
    session = one([tmp_path])
    assert session["context_tokens"] == 1110 and session["context_window"] is None and session["context_pct"] is None
    assert read([tmp_path])[1] == clean_read(unreadable=1)


def test_a_path_that_resolves_differently_after_the_open_is_discarded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = write_session(tmp_path)
    real = os.path.realpath
    seen: list[str] = []

    def resolve(value: str) -> str:
        resolved = real(value)
        if os.path.normcase(resolved) == os.path.normcase(real(path)):
            seen.append(value)
            if len(seen) > 1:
                return real(tmp_path / "swapped.json")
        return resolved

    monkeypatch.setattr(ls, "_realpath", resolve)
    assert read([tmp_path]) == ([], clean_read(unreadable=1))
    assert len(seen) == 2


def test_the_registered_root_itself_may_resolve_elsewhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    real_root = tmp_path / "real"
    full_folder(real_root)
    alias = tmp_path / "alias"
    monkeypatch.setattr(ls, "_realpath", redirect(alias, real_root))
    session = one([alias])
    assert session["context_window"] == 1_000_000


def make_dir_link(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        import _winapi
        _winapi.CreateJunction(str(target), str(link))  # pyright: ignore[reportUnknownMemberType]
    else:
        os.symlink(target, link, target_is_directory=True)


def test_a_real_directory_link_at_sessions_is_error_and_the_root_may_be_one(tmp_path: Path):
    outside = tmp_path / "outside"
    write_session(outside)
    root = tmp_path / "root"
    root.mkdir()
    make_dir_link(root / "sessions", outside / "sessions")
    assert read([root])[1]["state"] == "error"
    alias = tmp_path / "alias"
    make_dir_link(alias, outside)
    assert [s["pid"] for s in read([alias])[0]] == [4242]


def test_a_real_file_link_at_a_session_file_is_not_used(tmp_path: Path):
    outside = write_session(tmp_path / "outside")
    (tmp_path / "root" / "sessions").mkdir(parents=True)
    try:
        os.symlink(outside, tmp_path / "root" / "sessions" / "4242.json")
    except OSError:
        pytest.skip("file symbolic links need a privilege here; the injected resolver test covers the rule")
    assert read([tmp_path / "root"]) == ([], clean_read(unreadable=1))


# 7. transcript --------------------------------------------------------------------------------------------------------
def test_the_transcript_is_found_in_the_second_project_folder(tmp_path: Path):
    write_session(tmp_path)
    (tmp_path / "projects" / "A-first").mkdir(parents=True)
    write_transcript(tmp_path, [assistant()], project="B-second", age=42)
    session = one([tmp_path])
    assert session["active_at"] == NOW - 42 and session["context_tokens"] == 1110


def test_projects_is_listed_once_for_two_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    write_session(tmp_path, pid=1, sessionId="session-aaaa")
    write_session(tmp_path, pid=2, sessionId="session-bbbb")
    write_transcript(tmp_path, [assistant()], sid="session-aaaa")
    write_transcript(tmp_path, [assistant()], sid="session-bbbb")
    real = ls._listdir  # pyright: ignore[reportPrivateUsage]
    listed: list[str] = []

    def listdir(path: str) -> list[str]:
        listed.append(os.path.basename(path))
        return real(path)

    monkeypatch.setattr(ls, "_listdir", listdir)
    sessions, _ = read([tmp_path])
    assert len(sessions) == 2 and all(s["context_tokens"] == 1110 for s in sessions)
    assert listed.count("projects") == 1


def test_no_transcript_leaves_the_transcript_fields_none(tmp_path: Path):
    write_session(tmp_path)
    (tmp_path / "projects" / "A").mkdir(parents=True)
    session = one([tmp_path])
    for key in ("active_at", "model", "context_tokens", "context_window", "context_pct", "context_at", "said_line",
                "title", "prompt_line"):
        assert session[key] is None, key


def test_a_transcript_that_exists_and_cannot_be_read_is_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    write_session(tmp_path)
    path = write_transcript(tmp_path, [assistant()])
    real = os.open

    def opener(name: str, flags: int, *args: Any) -> int:
        if os.path.normcase(name) == os.path.normcase(os.path.realpath(path)):
            raise PermissionError(13, "denied")
        return real(name, flags, *args)

    monkeypatch.setattr(ls, "_open", opener)
    session = one([tmp_path])
    assert session["context_tokens"] is None and session["active_at"] is None
    assert read([tmp_path])[1] == clean_read(unreadable=1)


def test_a_duplicate_session_id_in_a_second_folder_is_excluded(tmp_path: Path):
    write_session(tmp_path / "a", pid=1)
    write_session(tmp_path / "b", pid=2)
    sessions, state = read([tmp_path / "a", tmp_path / "b"])
    assert [s["pid"] for s in sessions] == [1] and state == clean_read()


def test_more_than_two_thousand_project_names_count_not_read(tmp_path: Path):
    write_session(tmp_path)
    projects = tmp_path / "projects"
    projects.mkdir()
    for i in range(ls.LEAD_PROJECTS_CAP + 1):
        (projects / f"p{i:05d}").mkdir()
    write_transcript(tmp_path, [assistant()], project="p02000")  # the 2,001st name in sorted order
    session = one([tmp_path])
    assert session["context_tokens"] is None
    assert read([tmp_path])[1] == clean_read(not_read=1)


# 8. tail --------------------------------------------------------------------------------------------------------------
def tail_of(tmp_path: Path, lines: list[object], **kw: Any) -> dict[str, Any]:
    write_session(tmp_path)
    write_transcript(tmp_path, lines)
    return one([tmp_path], **kw)


def test_the_last_main_chain_assistant_line_wins(tmp_path: Path):
    newest = assistant(model="claude-opus-5-5", usage={"input_tokens": 1, "cache_read_input_tokens": 2,
                                                         "cache_creation_input_tokens": 3}, ts="2026-11-15T00:09:00Z")
    session = tail_of(tmp_path, [assistant(), newest, {"type": "user", "message": {}}])
    assert session["context_tokens"] == 6 and session["model"] == "claude-opus-5-5"
    assert session["context_at"] == iso("2026-11-15T00:09:00Z")


@pytest.mark.parametrize("skipped", [
    assistant(sidechain=True, model="claude-sub"),
    assistant(usage={"input_tokens": 0}, model="claude-zero"),
    assistant(usage={"input_tokens": True, "cache_read_input_tokens": 5}, model="claude-bool"),
    assistant(usage={"input_tokens": -1, "cache_read_input_tokens": 5}, model="claude-negative"),
    assistant(usage={"input_tokens": "9"}, model="claude-string"),
    assistant(usage={"input_tokens": 1.5}, model="claude-float"),
    assistant(usage=[1], model="claude-list"),
    assistant(message="text"),
    {"type": "assistant", "isSidechain": False},
])
def test_unusable_assistant_lines_are_skipped(tmp_path: Path, skipped: dict[str, object]):
    session = tail_of(tmp_path, [assistant(), skipped])
    assert session["context_tokens"] == 1110 and session["model"] == "claude-fable-5-2"


def test_missing_token_kinds_count_as_zero(tmp_path: Path):
    assert tail_of(tmp_path, [assistant(usage={"cache_read_input_tokens": 7})])["context_tokens"] == 7


@pytest.mark.parametrize("model", ["claude fable", "claude/../x", 7, None, "x" * 129])
def test_an_unsafe_model_is_none_and_the_tokens_are_kept(tmp_path: Path, model: object):
    session = tail_of(tmp_path, [assistant(model=model)])
    assert session["model"] is None and session["context_tokens"] == 1110


@pytest.mark.parametrize("ts", [MISSING, "yesterday", 1_763_165_340, "2026-11-15T00:09:00"])
def test_a_missing_or_bad_timestamp_gives_no_context_time(tmp_path: Path, ts: object):
    line = assistant()
    if ts is MISSING:
        del line["timestamp"]
    else:
        line["timestamp"] = ts
    session = tail_of(tmp_path, [line])
    assert session["context_at"] is None and session["context_tokens"] == 1110


def test_the_first_piece_is_dropped_only_when_the_tail_is_cut(tmp_path: Path):
    line = json.dumps(assistant(model="claude-first")).encode() + b"\n"
    filler = b"\n" * (ls.LEAD_TAIL_BYTES - len(line))
    write_session(tmp_path)
    write_transcript(tmp_path, [], raw=line + filler)  # exactly the tail: read from offset 0, the first piece is kept
    assert one([tmp_path])["model"] == "claude-first"
    write_transcript(tmp_path, [], raw=b"older\n" + line + filler)  # the tail starts exactly at the line: still dropped
    assert one([tmp_path])["model"] is None


def test_invalid_utf8_invalid_json_and_deeply_nested_lines_are_skipped(tmp_path: Path):
    good = json.dumps(assistant()).encode()
    raw = b"\n".join([good, b"\xff\xfe{\"type\": \"assistant\"}", b"{not json", b"[" * 100_000 + b"]" * 100_000,
                      b"\"a string\"", b""]) + b"\n"
    write_session(tmp_path)
    write_transcript(tmp_path, [], raw=raw)
    assert one([tmp_path])["context_tokens"] == 1110


# 9. text --------------------------------------------------------------------------------------------------------------
def test_the_last_title_prompt_and_said_win(tmp_path: Path):
    lines: list[object] = [
        {"type": "ai-title", "aiTitle": "Old title"}, {"type": "last-prompt", "lastPrompt": "old prompt"},
        assistant(content=[{"type": "text", "text": "old said"}]),
        {"type": "ai-title", "aiTitle": "Release notes for the spring update"},
        {"type": "last-prompt", "lastPrompt": "---\n\nAdd the section about the importer\nand more"},
        assistant(content=[{"type": "text", "text": "first block"}, {"type": "tool_use", "id": "x"},
                           {"type": "text", "text": "Drafted.\nRunning the link check now.\n---"}]),
    ]
    session = tail_of(tmp_path, lines)
    assert session["title"] == "Release notes for the spring update"
    assert session["prompt_line"] == "Add the section about the importer"
    assert session["said_line"] == "Running the link check now."
    assert "prompt" not in session and "said" not in session


@pytest.mark.parametrize("content", ["plain text", [{"type": "text"}], [{"type": "text", "text": ""}],
                                     [{"type": "tool_use", "text": "not text"}], [{"type": "text", "text": 5}], ["x"]])
def test_assistant_content_without_a_text_block_says_nothing_new(tmp_path: Path, content: object):
    session = tail_of(tmp_path, [assistant(content=[{"type": "text", "text": "earlier"}]), assistant(content=content)])
    assert session["said_line"] == "earlier"


def test_text_kinds_with_the_wrong_value_type_are_skipped(tmp_path: Path):
    session = tail_of(tmp_path, [{"type": "ai-title", "aiTitle": "Good"}, {"type": "ai-title", "aiTitle": 5},
                                 {"type": "last-prompt", "lastPrompt": "Ok"}, {"type": "last-prompt", "lastPrompt": None}])
    assert session["title"] == "Good" and session["prompt_line"] == "Ok"


def test_controls_and_secrets_are_removed_from_every_string(tmp_path: Path):
    dirty = f"a\x1b[31m\u200bb {SECRET}"
    write_session(tmp_path, name=dirty, cwd=f"D:/work/{dirty}", version=f"2.1\x07 {SECRET}")
    write_transcript(tmp_path, [{"type": "ai-title", "aiTitle": dirty}, {"type": "last-prompt", "lastPrompt": dirty},
                                assistant(content=[{"type": "text", "text": dirty}])])
    session = one([tmp_path], secrets=[SECRET])
    for key in ("name", "folder", "cwd", "version", "title", "prompt_line", "said_line"):
        value = session[key]
        assert isinstance(value, str) and SECRET not in value and "\x1b" not in value and "\u200b" not in value, key
        assert "\x07" not in value and "[REDACTED]" in value, key


def test_a_secret_that_matches_the_entry_state_and_model_patterns_never_reaches_the_result(tmp_path: Path):
    secret = "sekret_value"  # matches the entry, state and safe-model patterns alike
    write_session(tmp_path, entrypoint=secret, status=secret)
    write_transcript(tmp_path, [assistant(model=secret)])
    session = one([tmp_path], secrets=[secret])
    assert (session["entry"], session["state"], session["model"]) == (None, None, None)
    assert session["context_tokens"] == 1110 and secret not in json.dumps(session)


def test_entry_state_and_model_without_a_secret_are_kept(tmp_path: Path):
    write_session(tmp_path, entrypoint="claude-vscode", status="busy")
    write_transcript(tmp_path, [assistant(model="claude-opus-5-5")])
    session = one([tmp_path], secrets=["unrelated-secret"])
    assert (session["entry"], session["state"], session["model"]) == ("claude-vscode", "busy", "claude-opus-5-5")


def test_a_secret_split_by_a_format_character_is_still_redacted(tmp_path: Path):
    write_session(tmp_path)
    write_transcript(tmp_path, [{"type": "ai-title", "aiTitle": "x hunter2-very-\u200bsecret y"}])
    assert one([tmp_path], secrets=[SECRET])["title"] == "x [REDACTED] y"


def test_strings_are_cut_to_their_caps(tmp_path: Path):
    write_session(tmp_path, name="n" * 90, cwd="D:/" + "c" * 300, version="v" * 50)
    write_transcript(tmp_path, [{"type": "ai-title", "aiTitle": "t" * 200}])
    session = one([tmp_path])
    assert len(session["name"]) == 40 and len(session["folder"]) == 40 and len(session["cwd"]) == 200
    assert len(session["version"]) == 20 and len(session["title"]) == 80


def test_text_off_removes_the_four_fields_and_searches_no_text_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    write_session(tmp_path)
    write_transcript(tmp_path, [{"type": "ai-title", "aiTitle": "T"}, {"type": "last-prompt", "lastPrompt": "P"},
                                assistant(content=[{"type": "text", "text": "S"}])])
    called: list[str] = []
    monkeypatch.setattr(ls, "activity_lines", lambda *a, **k: called.append("x") or (None, None))
    session = one([tmp_path], text_on=False)
    assert (session["name"], session["title"], session["prompt_line"], session["said_line"]) == (None, None, None, None)
    assert called == [] and session["context_tokens"] == 1110 and session["folder"] == "docs-site"


def test_the_walk_stops_when_every_wanted_kind_was_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    write_session(tmp_path)
    write_transcript(tmp_path, [{"type": "user"}] * 5 + [{"type": "ai-title", "aiTitle": "Early"}, assistant()])
    real = ls._json_object  # pyright: ignore[reportPrivateUsage]
    parsed: list[bytes] = []
    monkeypatch.setattr(ls, "_json_object", lambda piece: parsed.append(piece) or real(piece))

    def transcript_lines() -> int:
        return sum(not piece.startswith(b'{"pid"') for piece in parsed)  # the session file is parsed too

    assert one([tmp_path], text_on=False)["context_tokens"] == 1110
    assert transcript_lines() == 1
    parsed.clear()
    assert one([tmp_path], text_on=True)["title"] == "Early"
    assert transcript_lines() == 7  # no prompt line exists, so the walk reads every line


def test_the_session_fields(tmp_path: Path):
    write_session(tmp_path, status="BUSY!", entrypoint="cli")
    write_transcript(tmp_path, [assistant()])
    write_catalog(tmp_path, [window("claude-fable-5-2", 1_000_000)])
    session = one([tmp_path])
    digest = hashlib.sha256(SID.encode()).hexdigest()
    assert session == {
        "key": digest, "id": digest[:8], "tool": "cc", "pid": 4242, "alive": True, "entry": "cli",
        "name": "docs-site-5a", "folder": "docs-site", "cwd": "D:/work/docs-site", "state": None,
        "state_since": NOW - 100, "started_at": STARTED, "version": "2.1.323", "active_at": NOW - 60,
        "model": "claude-fable-5-2", "context_tokens": 1110, "context_window": 1_000_000, "context_pct": 0,
        "context_at": iso("2026-11-15T00:08:28Z"), "title": None, "prompt_line": None, "said_line": "Done."}


@pytest.mark.parametrize(("over", "key", "expected"), [
    ({"status": "busy"}, "state", "busy"), ({"status": "a" * 17}, "state", None), ({"status": 3}, "state", None),
    ({"entrypoint": "Claude Desktop"}, "entry", None), ({"entrypoint": "claude-vscode"}, "entry", "claude-vscode"),
    ({"statusUpdatedAt": "x"}, "state_since", None), ({"statusUpdatedAt": True}, "state_since", None),
    ({"cwd": "C:\\Users\\me\\shop\\"}, "folder", "shop"), ({"cwd": 5}, "cwd", None), ({"cwd": 5}, "folder", None),
    ({"version": ["x"]}, "version", None), ({"name": 7}, "name", None),
])
def test_a_malformed_optional_value_is_none(tmp_path: Path, over: dict[str, object], key: str, expected: object):
    write_session(tmp_path, **over)
    assert one([tmp_path])[key] == expected


# 10. window -----------------------------------------------------------------------------------------------------------
def test_the_newest_catalog_file_wins(tmp_path: Path):
    write_session(tmp_path)
    write_transcript(tmp_path, [assistant()])
    write_catalog(tmp_path, [window("claude-fable-5-2", 2_000)], name="old-cc.json", age=100)
    write_catalog(tmp_path, [window("claude-fable-5-2", 4_000)], name="new-cc.json", age=1)
    (tmp_path / "cache" / "model-catalog" / "notes.txt").write_text("{}", encoding="utf-8")
    session = one([tmp_path])
    assert session["context_window"] == 4_000 and session["context_pct"] == 27


@pytest.mark.parametrize("value", [True, 0, -5, "1000000", 1e6, None])
def test_window_values_that_are_not_positive_ints_are_rejected(tmp_path: Path, value: object):
    write_session(tmp_path)
    write_transcript(tmp_path, [assistant()])
    write_catalog(tmp_path, [window("claude-fable-5-2", value), {"id": 5, "runtime": {"max_input_tokens": 9}}, "x",
                             {"id": "claude-fable-5-2"}])
    session = one([tmp_path])
    assert session["context_window"] is None and session["context_pct"] is None and session["context_tokens"] == 1110


@pytest.mark.parametrize(("tokens", "size", "pct"), [(1110, 1_000_000, 0), (1110, 1111, 99), (1110, 1110, 100),
                                                      (1110, 555, 100), (1110, 1387, 80)])
def test_the_percentage_floors_and_caps_at_one_hundred(tmp_path: Path, tokens: int, size: int, pct: int):
    write_session(tmp_path)
    write_transcript(tmp_path, [assistant()])
    write_catalog(tmp_path, [window("claude-fable-5-2", size)])
    assert one([tmp_path])["context_pct"] == pct


def test_an_unknown_model_has_no_window(tmp_path: Path):
    write_session(tmp_path)
    write_transcript(tmp_path, [assistant(model="claude-new-9")])
    write_catalog(tmp_path, [window("claude-fable-5-2", 1_000_000)])
    session = one([tmp_path])
    assert session["context_window"] is None and session["context_tokens"] == 1110


@pytest.mark.parametrize("raw", [b"{not json", b"[1]", b"\xff"])
def test_an_unreadable_catalog_counts_unreadable(tmp_path: Path, raw: bytes):
    full_folder(tmp_path)
    (tmp_path / "cache" / "model-catalog" / "abc-cc.json").write_bytes(raw)
    session, state = read([tmp_path])
    assert session[0]["context_window"] is None and state == clean_read(unreadable=1)


def test_a_catalog_over_the_limit_counts_unreadable(tmp_path: Path):
    full_folder(tmp_path)
    path = tmp_path / "cache" / "model-catalog" / "abc-cc.json"
    path.write_bytes(path.read_bytes() + b" " * ls.LEAD_CATALOG_MAX)
    assert read([tmp_path])[1] == clean_read(unreadable=1)


def test_the_catalog_is_read_once_per_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    write_session(tmp_path, pid=1, sessionId="session-aaaa")
    write_session(tmp_path, pid=2, sessionId="session-bbbb")
    write_catalog(tmp_path, [window("claude-fable-5-2", 1_000)])
    real = ls._listdir  # pyright: ignore[reportPrivateUsage]
    listed: list[str] = []
    monkeypatch.setattr(ls, "_listdir", lambda path: listed.append(os.path.basename(path)) or real(path))
    read([tmp_path])
    assert listed.count("model-catalog") == 1


# 11. never raises -----------------------------------------------------------------------------------------------------
def test_a_directory_named_like_a_session_file(tmp_path: Path):
    (tmp_path / "sessions" / "4242.json").mkdir(parents=True)
    assert read([tmp_path]) == ([], clean_read(unreadable=1))
    assert read([tmp_path], created=lambda pid: None) == ([], clean_read())


def test_a_transcript_that_is_a_directory(tmp_path: Path):
    write_session(tmp_path)
    (tmp_path / "projects" / "P" / f"{SID}.jsonl").mkdir(parents=True)
    session = one([tmp_path])
    assert session["context_tokens"] is None and read([tmp_path])[1] == clean_read(unreadable=1)


def test_a_file_that_vanishes_between_the_listing_and_the_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = write_session(tmp_path)
    real = ls._listdir  # pyright: ignore[reportPrivateUsage]

    def listdir(folder: str) -> list[str]:
        names = real(folder)
        if os.path.basename(folder) == "sessions":
            path.unlink()
        return names

    monkeypatch.setattr(ls, "_listdir", listdir)
    assert read([tmp_path], created=lambda pid: None) == ([], clean_read())


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFOs are POSIX only")
def test_a_fifo_named_like_a_session_file_is_not_opened(tmp_path: Path):
    (tmp_path / "sessions").mkdir()
    os.mkfifo(tmp_path / "sessions" / "4242.json")  # pyright: ignore[reportAttributeAccessIssue]
    assert read([tmp_path]) == ([], clean_read(unreadable=1))


def test_a_stat_that_is_not_a_regular_file_is_not_opened(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = write_session(tmp_path)
    real_stat = os.stat
    opened: list[str] = []

    def fake_stat(name: str, *args: Any, **kwargs: Any) -> os.stat_result:
        result = real_stat(name, *args, **kwargs)
        if os.path.normcase(str(name)) == os.path.normcase(os.path.realpath(path)):
            return os.stat_result((stat.S_IFIFO | 0o600, *tuple(result)[1:]))
        return result

    monkeypatch.setattr(ls, "_stat", fake_stat)
    monkeypatch.setattr(ls, "_open", lambda name, *a: opened.append(name) or os.open(name, *a))
    assert read([tmp_path]) == ([], clean_read(unreadable=1))
    assert opened == []


def test_an_unexpected_error_in_a_probe_never_raises(tmp_path: Path):
    write_session(tmp_path / "a")
    write_session(tmp_path / "b", pid=7, sessionId="session-other")

    def created(pid: int) -> object:
        if pid == 4242:
            raise RuntimeError("probe failed")
        return STARTED

    sessions, state = read([tmp_path / "a", tmp_path / "b"], created=created)
    assert [s["pid"] for s in sessions] == [7] and state["state"] == "error"


def test_the_reader_writes_nothing(tmp_path: Path):
    full_folder(tmp_path)
    before = sorted((p.relative_to(tmp_path), p.stat().st_mtime_ns) for p in tmp_path.rglob("*"))
    read([tmp_path])
    assert sorted((p.relative_to(tmp_path), p.stat().st_mtime_ns) for p in tmp_path.rglob("*")) == before


def test_the_named_limits():
    assert (ls.LEAD_DIRS_CAP, ls.LEAD_FILES_CAP, ls.LEAD_FILE_MAX, ls.LEAD_PROJECTS_CAP, ls.LEAD_TAIL_BYTES,
            ls.LEAD_CATALOG_MAX, ls.LEAD_PID_SLACK_S, ls.LEAD_CONTEXT_MARK_PCT) == (
        8, 64, 16_384, 2_000, 262_144, 1_048_576, 2.0, 80)


# 18. isolation --------------------------------------------------------------------------------------------------------
def test_the_autouse_fixture_gives_the_reader_one_empty_temporary_folder(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude"))
    monkeypatch.setenv("PEERHUB_LEAD_DIRS", os.path.expanduser("~"))
    assert ls.lead_dirs is not real_lead_dirs
    dirs = ls.lead_dirs(os.environ)
    assert len(dirs) == 1 and os.path.isdir(dirs[0]) and os.listdir(dirs[0]) == []
    assert os.path.normcase(os.path.expanduser("~/.claude")) != os.path.normcase(dirs[0])
