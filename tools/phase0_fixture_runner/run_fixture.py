from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from runner import InvalidInvocationError, run_fixture


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run one provider-free PeerHub Phase 0 "
            "controlled-fake fixture."
        )
    )
    parser.add_argument(
        "--event-script",
        required=True,
        type=Path,
        help="Path to the deterministic JSON event script.",
    )
    parser.add_argument(
        "--fixture-id",
        required=True,
        help="Fixture identifier recorded in the evidence artifacts.",
    )
    parser.add_argument(
        "--out-root",
        required=True,
        type=Path,
        help=(
            "Fresh output directory to create. It must not "
            "already exist."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)

    try:
        record_path = run_fixture(
            event_script_path=arguments.event_script,
            fixture_id=arguments.fixture_id,
            out_root=arguments.out_root,
        )
    except InvalidInvocationError as exc:
        print(
            f"INVALID_INVOCATION: {exc}",
            file=sys.stderr,
        )
        return 2
    except Exception as exc:
        print(
            (
                "RUNNER_CRASH: "
                f"{type(exc).__name__}: {exc}"
            ),
            file=sys.stderr,
        )
        return 1

    if not record_path.is_file():
        print(
            "RUNNER_CRASH: fixture record was not produced",
            file=sys.stderr,
        )
        return 1

    print(f"FIXTURE_RECORD: {record_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
