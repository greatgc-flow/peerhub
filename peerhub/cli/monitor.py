"""PeerHub CLI monitor command.

Composition root for the combined quota refresh and read-only diagnostic dashboard.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from typing import Any

from peerhub.cli.support import agy_token_use, print_warnings


def register_monitor_parser(subparsers: Any) -> None:
    p = subparsers.add_parser("monitor", help="Watch quota evidence and diagnostic dashboard")
    p.add_argument("--interval-seconds", type=float, default=60.0,
                   help="Sleep interval between cycles (positive float)")
    p.add_argument("--count", type=int, default=0,
                   help="Number of cycles to run (0 = until interrupted)")
    p.add_argument("--peers", nargs="+", default=["cx", "cc", "ag"],
                   help="Peers to refresh quota for")
    p.add_argument("--timeout-seconds", type=float, default=15.0,
                   help="Probe timeout")
    p.add_argument("--json", action="store_true",
                   help="Emit NDJSON frames")
    p.add_argument("--keep-going-on-agy-token-use", action="store_true",
                   help="Keep collecting after agy /usage consumed model tokens")
    p.add_argument("--refresh-every", type=int, default=1,
                   help="Collect only every Kth cycle (K>=1)")


def validate_monitor_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if not args.interval_seconds > 0:
        parser.error("--interval-seconds must be a positive number")
    if args.count < 0:
        parser.error("--count must be >= 0")
    if not args.timeout_seconds > 0:
        parser.error("--timeout-seconds must be a positive number")
    if args.refresh_every < 1:
        parser.error("--refresh-every must be >= 1")


def run_monitor(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    validate_monitor_args(parser, args)
    # Lazy imports: registering the parser must work when first-party extensions are blocked (Core-only path); the
    # extension-backed pieces are only needed once the command actually runs.
    from peerhub.extensions import diag_quota
    from peerhub.extensions.diag import ReadonlyDiag
    from peerhub.extensions.quota_capture import refresh_quota

    total: int = args.count
    done: int = 0
    last_refresh_status_code: int = 0

    try:
        while True:
            is_refresh_cycle = (done % args.refresh_every) == 0
            refresh_info: dict[str, Any] = {}
            if is_refresh_cycle:
                result = refresh_quota(
                    args.db,
                    args.peers,
                    sys_dir=None,
                    deadline_sec=args.timeout_seconds
                )
                last_refresh_status_code = 1 if result["status"] != "OK" else 0
                refresh_info = {
                    "status": result["status"],
                    "warnings": result.get("warnings", []),
                    "observations": len(result.get("observations", []))
                }
                if not args.json:
                    print(f"refresh: {result['status']}", file=sys.stderr, flush=True)
                    print_warnings(result)

                if agy_token_use(result) and not args.keep_going_on_agy_token_use:
                    print("error: agy /usage consumed model tokens; stopping watch mode so quota is not burned every interval "
                          "(pass --keep-going-on-agy-token-use to continue)", file=sys.stderr, flush=True)
                    return 1
            else:
                refresh_info = {"skipped": True}

            diag = ReadonlyDiag(args.db)
            rep = diag.render(["peers", "streams", "resource_pools", "observations"])

            if args.json:
                frame = {
                    "cycle": done,
                    "refresh": refresh_info,
                    "snapshot": asdict(rep)
                }
                print(json.dumps(frame, ensure_ascii=True, separators=(",", ":")), flush=True)
            else:
                print(diag_quota.format_dashboard(rep), flush=True)

            done += 1
            if total > 0 and done >= total:
                return last_refresh_status_code

            time.sleep(args.interval_seconds)

    except KeyboardInterrupt:
        return 0
