"""Frozen schema 1.1 monitor watch adapter for existing JSON consumers."""
from __future__ import annotations

import json
import sqlite3
import sys
from typing import Any

from peerhub.cli.monitor_refresh import agy_token_command, agy_token_stop_message, monitor_auto_probe, refresh_monitor_quota
from peerhub.cli.store_select import report_missing_store


def _cycle_exit_code(report: Any, refresh_code: int) -> int:
    if report.status == "FAILED":
        return 6 if (report.error or "").startswith("SchemaVersionError:") else 4
    if report.status == "PARTIAL":
        return 4
    return refresh_code


def run_schema_11_watch(args: Any, *, sleep_fn: Any, monotonic_fn: Any) -> int:
    """Emit the captured 0.16.1 JSON frame contract; this adapter has no human renderer."""
    if not args.json:
        raise ValueError("schema 1.1 is a JSON-only compatibility contract")
    db_source = getattr(args, "db_source", None)
    is_explicit = db_source in ("explicit", "env")
    if report_missing_store(args.db, json_output=True, selected=is_explicit, source=db_source):
        return 5

    from peerhub.extensions.diag_read_model import dashboard_data
    from peerhub.extensions.diag import ReadonlyDiag

    store_path = str(args.db)
    store_source = getattr(args, "db_source", "unknown")
    names = ["peers", "streams", "resource_pools", "observations", "activity"]
    try:
        initial = ReadonlyDiag(args.db).render(names)
        print(json.dumps({
            "schema_version": "1.1", "cycle": 0, "phase": "initial",
            "refresh": {"status": "NOT_STARTED"},
            "store_selection": {"path": store_path, "source": store_source},
            "snapshot": dashboard_data(initial),
        }, ensure_ascii=True, separators=(",", ":")), flush=True)

        done = 0
        total = args.cycles
        last_refresh_status_code = 0
        auto_probe = monitor_auto_probe(args)
        next_deadline = monotonic_fn() + args.interval
        while True:
            if report_missing_store(args.db, json_output=True, selected=is_explicit, source=db_source):
                return 5
            result = None
            if done % args.collect_every == 0:
                if args.probe:
                    from peerhub.cli.monitor_refresh import refresh_with_profile_pools

                    result = refresh_with_profile_pools(args.db, args.peers, sys_dir=args.sys_dir,
                                                        deadline_sec=args.timeout)
                elif auto_probe:
                    result = refresh_monitor_quota(args)
            if result is not None:
                last_refresh_status_code = 1 if result["status"] != "OK" else 0
                refresh_info = {"status": result["status"], "warnings": result.get("warnings", []),
                                "observations": len(result.get("observations", []))}
                token_command = agy_token_command(result)
                if token_command and not args.allow_agy_token_use:
                    print(agy_token_stop_message(token_command), file=sys.stderr, flush=True)
                    return 1
            else:
                refresh_info = {"skipped": True}

            report = ReadonlyDiag(args.db).render(names)
            print(json.dumps({
                "schema_version": "1.1", "cycle": done, "phase": "complete",
                "refresh": refresh_info,
                "store_selection": {"path": store_path, "source": store_source},
                "snapshot": dashboard_data(report),
            }, ensure_ascii=True, separators=(",", ":")), flush=True)
            done += 1
            if total > 0 and done >= total:
                return _cycle_exit_code(report, last_refresh_status_code)

            remaining = next_deadline - monotonic_fn()
            if remaining > 0:
                sleep_fn(remaining)
            now = monotonic_fn()
            while next_deadline <= now:
                next_deadline += args.interval
    except KeyboardInterrupt:
        return 0
    except (FileNotFoundError, sqlite3.OperationalError):
        if report_missing_store(args.db, json_output=True, selected=is_explicit, source=db_source):
            return 5
        raise
