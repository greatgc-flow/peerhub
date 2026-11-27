"""Explicit, offline inspection of an existing activity source."""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from peerhub.cli.support import error_line

if TYPE_CHECKING:
    from peerhub.cli.arguments import Subparsers


def register(subparsers: Subparsers) -> None:
    sub = subparsers.add_parser("activity", help="Read collaboration evidence from an explicit store")
    actions = sub.add_subparsers(dest="action", required=True)
    export = actions.add_parser("export", help="Page through metadata and receipts")
    show = actions.add_parser("show", help="Read one prompt and its related attempts, receipts and outcomes")
    show.add_argument("ask_id", help="Prompt record ID")
    for parser in (export, show):
        parser.add_argument("--stream", help="Restrict reads to one exact stream")
        parser.add_argument("--cursor", help="Continue a page or poll a completed checkpoint")
        parser.add_argument("--limit", type=int, default=100, help="Maximum events per page (1 to 200, default %(default)s)")
        parser.add_argument("--max-bytes", type=int, default=1048576, help="Maximum encoded page bytes (512 to 1048576)")


def run(args: argparse.Namespace, *, explicit_db: bool) -> int:
    from peerhub.extensions.activity.reader import export_page
    from peerhub.extensions.activity.cursor import ActivityError, wire

    try:
        if not explicit_db or not args.db:
            raise ActivityError("EXPLICIT_DB_REQUIRED")
        result = export_page(Path(args.db), stream=getattr(args, "stream", None), ask=getattr(args, "ask_id", None),
                             token=args.cursor, limit=args.limit, max_bytes=args.max_bytes)
    except (OSError, sqlite3.Error, ValueError) as exc:
        error = exc if isinstance(exc, ActivityError) else ActivityError("SOURCE_UNAVAILABLE", "diag_unavailable", 5)
        if args.json:
            print(wire({"schema_version": "1.0", "status": "ERROR", "error": error.reason}))
        hints = {
            "PARTITION_LIMIT": "enumerate approved streams with stream list and keep one --stream checkpoint for each",
            "RESNAPSHOT_REQUIRED": "restart the approved snapshot and reconcile existing event keys",
            "CURSOR_SCOPE_MISMATCH": "use the original export filters or start a new authorized snapshot",
            "INVALID_CURSOR": "recover the last committed checkpoint or start a new snapshot",
            "LIMIT_EXCEEDED": "increase --max-bytes and retry the same checkpoint",
            "EXPLICIT_DB_REQUIRED": "provide --db PATH before activity",
        }
        error_line(error.code, error.reason, hints.get(error.reason))
        return error.exit_code
    if args.json:
        print(wire(result))
    else:
        print(f"{len(result['items'])} evidence items (metadata only). Use --json for events and the checkpoint.")
    return 0
