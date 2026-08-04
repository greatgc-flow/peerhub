"""Unit tests for tools/fake_peer/pipe_executable.py."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[3] / "tools" / "fake_peer" / "pipe_executable.py"
)


def test_script_exists() -> None:
    assert SCRIPT_PATH.is_file(), f"Expected script at {SCRIPT_PATH}"


def test_default_invocation() -> None:
    res = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert res.stdout == "FAKE_PEER_STDOUT\n"
    assert res.stderr == ""


def test_configured_exit_code() -> None:
    res = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--exit-code", "42"],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 42
    assert res.stdout == "FAKE_PEER_STDOUT\n"
    assert res.stderr == ""


def test_configured_stdout_chunks_with_delays() -> None:
    args = [
        sys.executable,
        str(SCRIPT_PATH),
        "--chunk",
        "chunk1\n",
        "--chunk",
        "chunk2\n",
        "--chunk",
        "chunk3\n",
        "--chunk-delay",
        "0.05",
    ]
    start_t = time.monotonic()
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    chunks_received: list[str] = []
    assert proc.stdout is not None
    while True:
        line = proc.stdout.readline()
        if not line:
            break
        chunks_received.append(line)

    proc.wait()
    elapsed = time.monotonic() - start_t

    assert proc.returncode == 0
    assert chunks_received == ["chunk1\n", "chunk2\n", "chunk3\n"]
    # With 3 chunks and 0.05s delay between chunks (2 intervals), minimum duration ~ 0.10s.
    # We use generous tolerance (>= 0.06s).
    assert elapsed >= 0.06


def test_configured_stderr_output() -> None:
    res = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--stdout",
            "normal_output\n",
            "--stderr",
            "error_output\n",
        ],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert res.stdout == "normal_output\n"
    assert res.stderr == "error_output\n"


def test_configured_stderr_chunks() -> None:
    res = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--stderr-chunk",
            "err1\n",
            "--stderr-chunk",
            "err2\n",
        ],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert res.stderr == "err1\nerr2\n"


def test_configured_artificial_delay() -> None:
    start_t = time.monotonic()
    res = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--delay", "0.15"],
        capture_output=True,
        text=True,
    )
    elapsed = time.monotonic() - start_t

    assert res.returncode == 0
    assert elapsed >= 0.10


def test_echo_stdin_with_data() -> None:
    res = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--echo-stdin"],
        input="hello from stdin\n",
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert res.stdout == "hello from stdin\n"


def test_echo_stdin_with_devnull() -> None:
    res = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--echo-stdin"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert res.stdout == ""


def test_environment_variable_fallbacks() -> None:
    env = os.environ.copy()
    env["FAKE_PEER_STDOUT"] = "env stdout\n"
    env["FAKE_PEER_STDERR"] = "env stderr\n"
    env["FAKE_PEER_EXIT_CODE"] = "7"
    env["FAKE_PEER_DELAY"] = "0.05"

    start_t = time.monotonic()
    res = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        env=env,
        capture_output=True,
        text=True,
    )
    elapsed = time.monotonic() - start_t

    assert res.returncode == 7
    assert res.stdout == "env stdout\n"
    assert res.stderr == "env stderr\n"
    assert elapsed >= 0.03
