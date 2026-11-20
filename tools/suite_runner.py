"""Grade saved benchmark replies and verify references without contacting peers."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from types import ModuleType
from typing import Any, cast

SUITE_VERSION = "suite-v1"
DEFAULT_SUITE = Path(__file__).resolve().parents[1] / "docs" / "benchmarks" / SUITE_VERSION
ENTRY_POINTS = {
    "E1": "roman_to_int",
    "E2": "diff_intervals",
    "M1": "shift_date",
    "M2": "price_order",
    "H1": "sched_rr",
    "H2": "solve_game",
}
CASE_TIMEOUT = 0.45
GRADE_BUDGET = 4.75
MAX_REPORT_BYTES = 8 * 1024 * 1024
PYTHON_BLOCK = re.compile(r"^[ \t]*```python[ \t]*\n(.*?)^[ \t]*```[ \t]*$", re.M | re.S)


def _arguments(task: str, args: list[Any]) -> list[Any]:
    # JSON arrays stand for tuples only where the function signature calls for them.
    args = args.copy()
    tuple_lists = {"E2": (0, 1), "M2": (0,), "H1": (0,), "H2": (1,)}
    for index in tuple_lists.get(task, ()):
        args[index] = [tuple(row) for row in args[index]]
    return args


def _expected(task: str, value: Any) -> Any:
    if task == "E2":
        return [tuple(row) for row in value]
    if task == "H1":
        return dict(value)
    if task in {"M1", "H2"} and value is not None:
        return tuple(value)
    return value


def _wire_value(value: Any) -> Any:
    # Keep Python return types distinguishable, including integer dictionary keys.
    kind = type(cast(object, value))
    if value is None or kind in (int, str):
        return value
    if kind is tuple:
        return {"tuple": [_wire_value(item) for item in value]}
    if kind is list:
        return [_wire_value(item) for item in value]
    if kind is dict:
        return {"dict": [_wire_value(pair) for pair in sorted(value.items())]}
    raise TypeError("unsupported return type")


def _install_audit_hook() -> list[str]:
    import _frozen_importlib_external
    import zipimport

    blocked: list[str] = []
    roots = [os.path.normcase(os.path.abspath(path)) for path in sys.path]
    loader_globals = (_frozen_importlib_external.__dict__, zipimport.__dict__)
    blocked_prefixes = (
        "os.", "socket.", "subprocess.", "_winapi.", "ctypes.",
        "winreg.", "shutil.", "mmap.", "pty.", "resource.",
    )
    write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND

    def guard(event: str, args: tuple[Any, ...]) -> None:
        if event not in {"open", "os.listdir"} and not event.startswith(blocked_prefixes):
            return
        if event in {"open", "os.listdir"}:
            caller = sys._getframe(1).f_globals  # pyright: ignore[reportPrivateUsage]
            if any(caller is loader for loader in loader_globals):
                path = args[0]
                if isinstance(path, str):
                    path = os.path.normcase(os.path.abspath(path))
                    in_stdlib = any(path == root or path.startswith(root + os.sep) for root in roots)
                    in_site = {"site-packages", "dist-packages"}.intersection(Path(path).parts)
                    if in_stdlib and not in_site and (event != "open" or not args[2] & write_flags):
                        return
        if not blocked:
            blocked.append(event)
        raise PermissionError(f"blocked by grader: {event}")

    sys.addaudithook(guard)
    return blocked


def _worker() -> None:
    request = json.load(sys.stdin.buffer)
    task = request["task"]
    args = _arguments(task, request["args"])
    original = deepcopy(args) if task == "E2" else None
    module = ModuleType("peer_solution")
    sys.modules[module.__name__] = module

    # Keep peer output off the result channel, even for writes to stdout's fd.
    with open(os.dup(sys.stdout.fileno()), "wb") as report, open(os.devnull, "w", encoding="utf-8") as discard:
        os.dup2(discard.fileno(), 1)
        os.dup2(discard.fileno(), 2)
        sys.stdout = sys.stderr = discard
        blocked = _install_audit_hook()
        try:
            exec(compile(request["source"], "<peer response>", "exec"), vars(module))
            result = getattr(module, ENTRY_POINTS[task])(*args)
            if original is not None and args != original:
                outcome = ["mutated input"]
            else:
                outcome = ["ok", _wire_value(result)]
        except BaseException as exc:
            outcome = [type(exc).__name__]
        if blocked:
            outcome = [f"blocked by grader: {blocked[0]}"]
        payload = json.dumps(outcome, ensure_ascii=True).encode("ascii")
        if len(payload) <= MAX_REPORT_BYTES:
            report.write(payload)


def _run_case(task: str, source: str, case: dict[str, Any], timeout: float) -> str | None:
    request = json.dumps({"task": task, "source": source, "args": case["args"]}).encode("ascii")
    # On Windows the loader can need SystemRoot. Provider credentials and
    # workspace-specific Python configuration stay in the parent.
    env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR") if key in os.environ}
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(Path(__file__).resolve()), "--worker"],
            input=request,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            cwd=Path(sys.executable).parent,
            env=env,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "timeout"
    if proc.returncode != 0 or not proc.stdout:
        return "worker failed"
    try:
        outcome = json.loads(proc.stdout)
    except ValueError:
        return "invalid worker result"
    if not isinstance(outcome, list) or not outcome or not isinstance(outcome[0], str):
        return "invalid worker result"
    wanted = _wire_value(_expected(task, case["expected"]))
    if outcome == ["ok", wanted]:
        return None
    return "wrong answer" if outcome[0] == "ok" else outcome[0]


def grade(suite: Path, task: str, source: str | None) -> list[str | None]:
    """Return one failure reason (or None for a pass) per hidden case."""
    deadline = monotonic() + GRADE_BUDGET
    cases: list[dict[str, Any]] = json.loads((suite / task / "cases.json").read_text(encoding="utf-8"))
    if source is None:
        return ["missing Python code block"] * len(cases)
    results: list[str | None] = []
    for case in cases:
        remaining = deadline - monotonic()
        if remaining <= 0:
            results.append("task time budget exhausted")
        else:
            results.append(_run_case(task, source, case, min(CASE_TIMEOUT, remaining)))
    return results


def _score(results: list[str | None]) -> str:
    return f"{results.count(None)}/{len(results)}"


def _print_grade(task: str, results: list[str | None]) -> None:
    for i, reason in enumerate(results, 1):
        if reason is not None:
            print(f"{task} case {i}: {reason}")
    print(f"{task} SCORE {_score(results)}")


def _record(path: Path, args: argparse.Namespace, score: str) -> None:
    row = {
        "suite_version": SUITE_VERSION,
        "task": args.task,
        "profile": args.profile,
        "model": args.model,
        "effort": None if args.effort == "none" else args.effort,
        "peer_cli_version": args.peer_cli_version,
        "score": score,
        "wall_seconds": args.wall_seconds,
        "date": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
    }
    line = (json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    with path.open("ab", buffering=0) as stream:
        if os.name == "nt":
            # The Windows C runtime implements append as a seek and write.
            import msvcrt

            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                stream.write(line)
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            stream.write(line)


def _wall_seconds(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0:
        raise argparse.ArgumentTypeError("expected finite, non-negative seconds")
    return seconds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("grade", "verify", "record"):
        cmd = commands.add_parser(command)
        cmd.set_defaults(suite=DEFAULT_SUITE)
        if command != "record":
            cmd.add_argument("--suite", type=Path)
        if command != "verify":
            cmd.add_argument("--task", required=True, choices=ENTRY_POINTS)
            cmd.add_argument("--response", required=True, type=Path)
        if command == "record":
            for key in ("profile", "model", "effort", "peer-cli-version"):
                cmd.add_argument(f"--{key}", required=True)
            cmd.add_argument("--wall-seconds", required=True, type=_wall_seconds)
    args = parser.parse_args(argv)

    try:
        if args.command == "verify":
            failed = False
            for task in ENTRY_POINTS:
                source = (args.suite / task / "reference.py").read_text(encoding="utf-8")
                results = grade(args.suite, task, source)
                _print_grade(task, results)
                failed |= any(results)
            return int(failed)

        response = args.response.read_text(encoding="utf-8-sig")
        match = PYTHON_BLOCK.search(response)
        results = grade(args.suite, args.task, match[1] if match else None)
        _print_grade(args.task, results)
        if args.command == "record":
            _record(args.suite / "results.jsonl", args, _score(results))
        return 0
    except (OSError, ValueError) as exc:
        print(f"suite_runner: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--worker"]:
        _worker()
    else:
        raise SystemExit(main())
