from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from tempfile import mkstemp
from time import monotonic

import pytest

from tools import suite_runner


@pytest.fixture
def temporary_file() -> Iterator[Path]:
    descriptor, name = mkstemp()
    os.close(descriptor)
    path = Path(name)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def test_verify_all_references(capsys: pytest.CaptureFixture[str]) -> None:
    assert suite_runner.main(["verify"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"{task} SCORE 10/10" for task in ("E1", "E2", "M1", "M2", "H1", "H2")
    ]


def test_grade_extracts_first_python_block(
    temporary_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reference = (suite_runner.DEFAULT_SUITE / "E1" / "reference.py").read_text(encoding="utf-8")
    temporary_file.write_text(
        "Intro\n```text\nignored\n```\n```python\ndef roman_to_int(s): return -1\n```\n"
        f"```python\n{reference}```\n",
        encoding="utf-8",
        newline="\n",
    )

    assert suite_runner.main([
        "grade", "--suite", str(suite_runner.DEFAULT_SUITE), "--task", "E1", "--response", str(temporary_file)
    ]) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "E1 SCORE 6/10"


def test_timeout_kills_worker_and_next_case_passes() -> None:
    source = """
def roman_to_int(s):
    if s == 'I':
        while True:
            pass
    return 444
"""
    started = monotonic()
    results = suite_runner.grade(suite_runner.DEFAULT_SUITE, "E1", source)
    assert results[:2] == ["timeout", None]
    assert len(results) == 10
    assert monotonic() - started < 5


@pytest.mark.parametrize(
    ("operation", "event"),
    [
        (f"open({str(suite_runner.DEFAULT_SUITE / 'E1' / 'cases.json')!r})", "open"),
        ("open(os.devnull, 'w')", "open"),
        ("socket.socket()", "socket.__new__"),
        ("subprocess.Popen([sys.executable, '-c', 'pass'])", "subprocess.Popen"),
    ],
)
def test_audit_hook_blocks_side_effects(operation: str, event: str) -> None:
    source = f"""
import os, socket, subprocess, sys

def roman_to_int(s):
    try:
        {operation}
    except PermissionError:
        pass
    return 1
"""
    case = {"args": ["I"], "expected": 1}
    reason = suite_runner._run_case("E1", source, case, suite_runner.CASE_TIMEOUT)  # pyright: ignore[reportPrivateUsage]
    assert reason == f"blocked by grader: {event}"


def test_record_creates_and_appends_versioned_json_lines(temporary_file: Path) -> None:
    temporary_file.unlink()
    args = argparse.Namespace(
        task="H1", profile="cx.effort", model="effective-model", effort="high",
        peer_cli_version="0.171.0", wall_seconds=8.25,
    )

    suite_runner._record(temporary_file, args, "7/10")  # pyright: ignore[reportPrivateUsage]
    first_line = temporary_file.read_bytes()
    args.effort = "none"
    suite_runner._record(temporary_file, args, "9/10")  # pyright: ignore[reportPrivateUsage]

    content = temporary_file.read_bytes()
    assert content.startswith(first_line)
    assert content.count(b"\n") == 2
    assert b"\r" not in content
    first, second = [json.loads(line) for line in content.splitlines()]
    first_date = datetime.fromisoformat(first.pop("date"))
    second_date = datetime.fromisoformat(second.pop("date"))
    assert first_date.tzinfo == second_date.tzinfo == timezone.utc
    assert first_date <= second_date
    assert first == {
        "suite_version": "suite-v1", "task": "H1", "profile": "cx.effort",
        "model": "effective-model", "effort": "high", "peer_cli_version": "0.171.0",
        "score": "7/10", "wall_seconds": 8.25,
    }
    assert second == dict(first, effort=None, score="9/10")
