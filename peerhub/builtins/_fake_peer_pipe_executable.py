#!/usr/bin/env python3
"""Deterministic controlled fake-peer CLI script.

Simulates peer CLI binary behavior for integration and end-to-end testing
of the dispatch pipeline (materialization -> spawn -> stream -> exit -> assess).

Configuration can be provided via CLI arguments or environment variables.

Arguments:
  --stdout TEXT              Fixed stdout string to write.
  --chunk TEXT               Stdout chunk string. Can be specified multiple times.
  --chunk-delay SECONDS      Delay in seconds between stdout chunks (default: 0.0).
  --stderr TEXT              Fixed stderr string to write.
  --stderr-chunk TEXT        Stderr chunk string. Can be specified multiple times.
  --exit-code INT            Exit code to return (default: 0).
  --echo-stdin               Read stdin until EOF and write it to stdout.
  --delay SECONDS            Artificial delay in seconds before exiting (default: 0.0).

Environment Variables (fallbacks if CLI args not passed):
  FAKE_PEER_STDOUT           Fixed stdout string.
  FAKE_PEER_STDERR           Fixed stderr string.
  FAKE_PEER_EXIT_CODE       Exit code integer.
  FAKE_PEER_CHUNK_DELAY     Chunk delay in seconds.
  FAKE_PEER_DELAY            Artificial exit delay in seconds.
  FAKE_PEER_ECHO_STDIN       Set to "1", "true", "yes" to enable echo-stdin.

Default Behavior:
  With no flags or environment variables, exits cleanly with code 0 and
  writes "FAKE_PEER_STDOUT\\n" to stdout.
"""

from __future__ import annotations

import argparse
import os
import sys
import time


def _env_bool(var_name: str, default: bool = False) -> bool:
    val = os.environ.get(var_name, "").strip().lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on")


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deterministic fake-peer executable for pipe runner integration tests.",
    )

    env_stdout = os.environ.get("FAKE_PEER_STDOUT", None)
    env_stderr = os.environ.get("FAKE_PEER_STDERR", None)
    env_exit_code = os.environ.get("FAKE_PEER_EXIT_CODE", None)
    env_chunk_delay = os.environ.get("FAKE_PEER_CHUNK_DELAY", None)
    env_delay = os.environ.get("FAKE_PEER_DELAY", None)
    env_echo_stdin = _env_bool("FAKE_PEER_ECHO_STDIN", False)

    default_exit_code = int(env_exit_code) if env_exit_code is not None else 0
    default_chunk_delay = float(env_chunk_delay) if env_chunk_delay is not None else 0.0
    default_delay = float(env_delay) if env_delay is not None else 0.0

    parser.add_argument(
        "--stdout",
        type=str,
        default=env_stdout,
        help="Fixed stdout content string.",
    )
    parser.add_argument(
        "--chunk",
        action="append",
        dest="chunks",
        help="Stdout chunk. Can be specified multiple times for streaming.",
    )
    parser.add_argument(
        "--chunk-delay",
        type=float,
        default=default_chunk_delay,
        help="Delay in seconds between stdout chunks.",
    )
    parser.add_argument(
        "--stderr",
        type=str,
        default=env_stderr,
        help="Fixed stderr content string.",
    )
    parser.add_argument(
        "--stderr-chunk",
        action="append",
        dest="stderr_chunks",
        help="Stderr chunk. Can be specified multiple times for streaming.",
    )
    parser.add_argument(
        "--exit-code",
        type=int,
        default=default_exit_code,
        help="Exit code to return (default: 0).",
    )
    parser.add_argument(
        "--echo-stdin",
        action="store_true",
        default=env_echo_stdin,
        help="Read stdin until EOF and write to stdout.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=default_delay,
        help="Artificial delay in seconds before exiting.",
    )

    return parser.parse_args(args)


def main(args: list[str] | None = None) -> int:
    parsed = parse_args(args)

    # 1. Handle Stdout / Chunks / Echo Stdin
    if parsed.echo_stdin:
        # Read binary stdin until EOF
        stdin_bytes = sys.stdin.buffer.read()
        if stdin_bytes:
            sys.stdout.buffer.write(stdin_bytes)
            sys.stdout.buffer.flush()

    if parsed.chunks:
        for i, chunk in enumerate(parsed.chunks):
            if i > 0 and parsed.chunk_delay > 0:
                time.sleep(parsed.chunk_delay)
            sys.stdout.write(chunk)
            sys.stdout.flush()
    elif parsed.stdout is not None:
        sys.stdout.write(parsed.stdout)
        sys.stdout.flush()
    elif not parsed.echo_stdin:
        # Default output if stdout/chunks/echo-stdin were not specified
        sys.stdout.write("FAKE_PEER_STDOUT\n")
        sys.stdout.flush()

    # 2. Handle Stderr / Stderr Chunks
    if parsed.stderr_chunks:
        for chunk in parsed.stderr_chunks:
            sys.stderr.write(chunk)
            sys.stderr.flush()
    elif parsed.stderr is not None:
        sys.stderr.write(parsed.stderr)
        sys.stderr.flush()

    # 3. Artificial Exit Delay
    if parsed.delay > 0:
        time.sleep(parsed.delay)

    return parsed.exit_code


if __name__ == "__main__":
    sys.exit(main())
