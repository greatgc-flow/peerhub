"""PeerHub CLI monitor command.

Composition root for the combined quota refresh and read-only diagnostic dashboard.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from peerhub.cli.support import agy_token_use, print_warnings


def register_monitor_parser(subparsers: Any) -> None:
    p = subparsers.add_parser("monitor", help="Watch quota evidence and diagnostic dashboard")
    p.add_argument("--interval-seconds", dest="interval", type=float, default=60.0,
                   help="Collection cycles every N s (positive float); supported TTY countdown redraw at most 1 Hz")
    p.add_argument("--cycles", type=int, default=0,
                   help="Number of cycles to run (0 = until interrupted)")
    p.add_argument("--peers", nargs="+", default=["cx", "cc", "ag"],
                   help="Peers to refresh quota for")
    p.add_argument("--timeout-seconds", dest="timeout", type=float, default=15.0,
                   help="Probe timeout")
    p.add_argument("--json", action="store_true",
                   help="Emit NDJSON frames")
    p.add_argument("--allow-agy-token-use", action="store_true",
                   help="Keep collecting after agy /usage consumed model tokens")
    p.add_argument("--view", choices=["auto", "rich", "plain"], default="auto",
                   help="Dashboard style: rich (bars, colour, pace), plain (ASCII); auto = rich on a TTY. TTY with VT redraws in place at most 1 Hz; non-TTY or no VT appends once every N s without escapes")
    p.add_argument("--collect-every", type=int, default=1,
                   help="Collect only every Kth cycle (K>=1)")
    p.add_argument("--system-dir", dest="sys_dir", type=Path,
                   help="Explicit portable provider installation directory")


def validate_monitor_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if not args.interval > 0:
        parser.error("--interval-seconds must be a positive number")
    if args.cycles < 0:
        parser.error("--cycles must be >= 0")
    if not args.timeout > 0:
        parser.error("--timeout-seconds must be a positive number")
    if args.collect_every < 1:
        parser.error("--collect-every must be >= 1")


def _frame_drawer(format_frame: Any, view: str, *, color: bool, db_name: str, cycle: int, refresh: dict[str, Any], interval: float,
                  collect_every: int, output: Any, store_path: str = "", store_source: str = "") -> Any:
    unicode_ok = (sys.stdout.encoding or "").lower().startswith("utf")

    def draw(rep: Any, next_in: float | None) -> None:
        output.write(format_frame(rep, view, color=color, unicode=unicode_ok, width=output.width(), db_name=db_name, cycle=cycle,
                           refresh=refresh, next_refresh_in=next_in, interval=interval, refresh_every=collect_every,
                           store_path=store_path, store_source=store_source))

    return draw


def _tick(rep: Any, draw: Any, interval: float, collect_in: float, advance: Any) -> None:
    """Between two cycles redraw once per second from the same snapshot (clock, ages and countdowns move; nothing is re-read)."""
    start = time.monotonic()
    while True:
        left = interval - (time.monotonic() - start)
        if left <= 0:
            return
        time.sleep(min(1.0, left))
        elapsed = time.monotonic() - start
        if elapsed >= interval:
            return
        if elapsed >= 1.0:
            draw(advance(rep, rep.read_at + elapsed), max(0.0, collect_in - elapsed))


def run_monitor(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    validate_monitor_args(parser, args)
    from peerhub.cli.store_select import report_missing_store
    if report_missing_store(args.db, json_output=args.json):
        return 5
    # Lazy imports: registering the parser must work when first-party extensions are blocked (Core-only path); the
    # extension-backed pieces are only needed once the command actually runs.
    from peerhub.cli.view import FrameOutput, advance_report, dashboard_data, format_frame, use_color
    from peerhub.extensions.diag import ReadonlyDiag
    from peerhub.extensions.quota_capture import refresh_quota

    tty = sys.stdout.isatty()
    view = ("rich" if tty else "plain") if args.view == "auto" else args.view
    output = FrameOutput(interactive=not args.json)
    color = view == "rich" and use_color(output.live, os.environ)
    db_name = os.path.basename(str(args.db))
    store_path = str(args.db)
    store_source = getattr(args, "db_source", "unknown")
    live, draw = False, None
    total: int = args.cycles
    done: int = 0
    last_refresh_status_code: int = 0

    try:
        while True:
            if report_missing_store(args.db, json_output=args.json):
                return 5
            is_refresh_cycle = (done % args.collect_every) == 0
            refresh_info: dict[str, Any] = {}
            if is_refresh_cycle:
                result = refresh_quota(
                    args.db,
                    args.peers,
                    sys_dir=getattr(args, "sys_dir", None),
                    deadline_sec=args.timeout
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

                if agy_token_use(result) and not args.allow_agy_token_use:
                    print("error: agy /usage consumed model tokens; stopping watch mode so quota is not burned every interval "
                          "(pass --allow-agy-token-use to continue)", file=sys.stderr, flush=True)
                    return 1
            else:
                refresh_info = {"skipped": True}

            diag = ReadonlyDiag(args.db)
            rep = diag.render(["peers", "streams", "resource_pools", "observations", "activity"])

            if args.json:
                snapshot_dict = dashboard_data(rep)
                frame = {
                    "cycle": done,
                    "refresh": refresh_info,
                    "store_selection": {"path": store_path, "source": store_source},
                    "snapshot": snapshot_dict,
                }
                print(json.dumps(frame, ensure_ascii=True, separators=(",", ":")), flush=True)
            else:
                live = output.live
                draw = _frame_drawer(format_frame, view, color=color, db_name=db_name, cycle=done, refresh=refresh_info,
                                     interval=args.interval, collect_every=args.collect_every, output=output,
                                     store_path=store_path, store_source=store_source)
                until_collect = args.collect_every - done % args.collect_every
                draw(rep, until_collect * args.interval)

            done += 1
            if total > 0 and done >= total:
                return last_refresh_status_code

            if live and draw is not None:
                until_collect = ((done - 1) // args.collect_every + 1) * args.collect_every - (done - 1)
                _tick(rep, draw, args.interval, until_collect * args.interval, advance_report)
            else:
                time.sleep(args.interval)

    except KeyboardInterrupt:
        return 0
    except (FileNotFoundError, sqlite3.OperationalError):
        if report_missing_store(args.db, json_output=args.json):
            return 5
        raise
    finally:
        output.close()
