"""Immutable account-bound peer commands."""
from __future__ import annotations

import argparse
import json
import sqlite3
from typing import TYPE_CHECKING, Any

from peerhub.cli.support import error_line

if TYPE_CHECKING:
    from peerhub.cli.arguments import Subparsers


def register_account_parser(subparsers: Subparsers) -> None:
    account_parser = subparsers.add_parser("account", help="Immutable cc/cx account roots; peer register overwrite refuses later asks",
                                          description="Bind a new peer and root for each account. No rebind/unbind. Overwriting a bound peer with peer register makes ask refuse; recover with a new peer and root.")
    account_sub = account_parser.add_subparsers(dest="action", required=True)
    account_bind = account_sub.add_parser("bind", help="Bind a new peer to a cc/cx account root")
    account_bind.add_argument("--peer", required=True, help="New peer ID to bind")
    account_bind.add_argument("--provider", required=True, help="Provider of the account: cc or cx")
    account_bind.add_argument("--alias", required=True, help="Short name of the account")
    account_bind.add_argument("--root", required=True, help="Provider configuration root of the account")
    account_bind.add_argument("--json", action="store_true")
    for action, help_text in (("list", "List account bindings"), ("show", "Show one account binding")):
        account_read = account_sub.add_parser(action, help=f"{help_text} (read-only)")
        if action == "show":
            account_read.add_argument("--peer", required=True, help="Bound peer ID")
        account_read.add_argument("--json", action="store_true")
        account_read.add_argument("--show-root", action="store_true", help="Include stored configuration root paths")


def run_account(args: argparse.Namespace) -> int:
    from dataclasses import asdict
    from peerhub.extensions import accounts

    try:
        if args.action == "bind":
            rows = [accounts.bind_account(args.db, peer_id=args.peer, provider=args.provider, alias=args.alias, root=args.root)]
        elif args.action == "show":
            binding = accounts.get_binding(args.db, args.peer)
            if binding is None:
                raise accounts.AccountError("account peer is not bound")
            rows = [binding]
        else:
            rows = accounts.list_bindings(args.db)
        output: list[dict[str, Any]] = []
        for row in rows:
            item = asdict(row)
            if not getattr(args, "show_root", False):
                item.pop("config_root")
            output.append(item)
        if args.json:
            print(json.dumps(output if args.action == "list" else output[0], indent=2, ensure_ascii=True))
        else:
            if not output:
                print("(no accounts)")
                return 0
            print("PEER PROVIDER ALIAS SLOT_ID" + (" ROOT" if getattr(args, "show_root", False) else ""))
            for item in output:
                print(" ".join(str(item[key]) for key in ("peer_id", "provider", "alias", "slot_id"))
                      + (f" {item['config_root']}" if "config_root" in item else ""))
        return 0
    except (ValueError, sqlite3.IntegrityError) as exc:
        message = "account binding violates a registry constraint" if isinstance(exc, sqlite3.IntegrityError) else str(exc)
        if args.json:
            print(json.dumps(dict(status="error", error=dict(code="account_invalid", message=message)), ensure_ascii=True))
        error_line("account_invalid", message)
        return 1
