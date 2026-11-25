"""Explicit observation collection commands."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import TYPE_CHECKING

from peerhub.cli.output import emit
from peerhub.cli.support import print_warnings
from peerhub.core.peer_kinds import QUOTA_CAPABLE_PEERS
from peerhub.core.store import CoreStore

if TYPE_CHECKING:
    from peerhub.cli.arguments import Subparsers


def register_observation_parser(subparsers: Subparsers) -> None:
    observation_parser = subparsers.add_parser("observation", help="Explicit observation collection (mutating)")
    observation_sub = observation_parser.add_subparsers(dest="action", required=True)
    refresh = observation_sub.add_parser("refresh", help="Collect provider quota and account evidence into append-only Observations")
    refresh.add_argument("--peers", nargs="+", default=list(QUOTA_CAPABLE_PEERS),
                         help=f"Peers to collect quota evidence for (default: {' '.join(QUOTA_CAPABLE_PEERS)})")
    refresh.add_argument("--system-dir", dest="sys_dir", type=Path, help="Explicit portable provider installation directory")
    refresh.add_argument("--timeout-seconds", type=float, default=15, help="Probe timeout in seconds (default %(default)s)")


def run_observation(args: argparse.Namespace) -> int:
    from peerhub.cli.store_select import report_missing_store
    db_source = getattr(args, "db_source", "default")
    is_explicit = db_source in ("explicit", "env")
    if not is_explicit and report_missing_store(args.db, json_output=args.json, selected=is_explicit, source=db_source):
        return 5
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    CoreStore(args.db)  # explicit --db/PEERHUB_DB retains collection bootstrap semantics
    from peerhub.cli.monitor_refresh import refresh_with_profile_pools
    result = refresh_with_profile_pools(args.db, args.peers, sys_dir=args.sys_dir, deadline_sec=args.timeout_seconds)
    emit(result, args.json)
    print_warnings(result)
    return 0 if result["status"] == "OK" else 1
