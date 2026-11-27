"""Peer, Stream, Record, and Offset commands."""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from peerhub.cli.output import emit
from peerhub.cli.support import error_line
from peerhub.core.models import AppendRequest, Peer, Stream, utc_now_iso
from peerhub.core.peer_kinds import BUILTIN_PEER_CHOICES
from peerhub.core.store import CoreStore

if TYPE_CHECKING:
    from peerhub.cli.arguments import Subparsers


def register_core_parser(subparsers: Subparsers) -> None:
    # peer
    peer_parser = subparsers.add_parser("peer", help="Peer commands")
    peer_sub = peer_parser.add_subparsers(dest="action", required=True)

    p_reg = peer_sub.add_parser("register", help="Register a peer")
    p_reg.add_argument("--peer", required=True, dest="peer_id", help="Peer ID to register")
    p_reg.add_argument("--name", dest="display_name", help="Display name for the peer")
    p_reg.add_argument("--adapter", dest="adapter_ref", help=f"Adapter the peer runs on: {BUILTIN_PEER_CHOICES}")

    peer_get, peer_show = "get", "show"
    peer_alias_help = f"{peer_show} is an alias of {peer_get}"
    p_get = peer_sub.add_parser(peer_get, aliases=[peer_show],
                                help=f"Get a peer ({peer_alias_help})", description=peer_alias_help)
    # Keep the action canonical even when argparse matches the alias.
    p_get.set_defaults(action=peer_get)
    p_get.add_argument("--peer", required=True, dest="peer_id", help="Peer ID")

    p_list = peer_sub.add_parser("list", help="List peers (read-only, bounded)")
    p_list.add_argument("--limit", type=_page_limit, default=50, help="Maximum peers per page (default %(default)s)")
    p_list.add_argument("--cursor", help="Continue after the next_cursor of the previous page")
    p_list.add_argument("--format", choices=["auto", "table", "json"], default="auto", help=argparse.SUPPRESS)

    # stream
    stream_parser = subparsers.add_parser("stream", help="Stream commands")
    st_sub = stream_parser.add_subparsers(dest="action", required=True)

    s_create = st_sub.add_parser("create", help="Create a stream")
    s_create.add_argument("--stream", required=True, dest="stream_id", help="Stream ID to create")
    s_create.add_argument("--title", help="Stream title")
    s_create.add_argument("--members", nargs="*", default=[], help="Peer IDs that are members of the stream")

    s_show = st_sub.add_parser("show", help="Show stream details")
    s_show.add_argument("--stream", required=True, dest="stream_id", help="Stream ID")

    s_list = st_sub.add_parser("list", help="List streams (read-only, bounded)")
    s_list.add_argument("--limit", type=_page_limit, default=50, help="Maximum streams per page (default %(default)s)")
    s_list.add_argument("--cursor", help="Continue after the next_cursor of the previous page")
    s_list.add_argument("--format", choices=["auto", "table", "json"], default="auto", help=argparse.SUPPRESS)

    # record
    record_parser = subparsers.add_parser("record", help="Record commands")
    rec_sub = record_parser.add_subparsers(dest="action", required=True)

    r_append = rec_sub.add_parser("append", help="Append a record")
    r_append.add_argument("--stream", required=True, dest="stream_id", help="Stream ID to append to")
    r_append.add_argument("--author-peer", required=True, dest="author_peer_id", help="Peer ID of the record author")
    kind_pattern = next(rule.pattern for rule in AppendRequest.model_fields["kind"].metadata if hasattr(rule, "pattern"))
    r_append.add_argument("--kind", required=True, help=f"Record kind matching {kind_pattern} (for example, message)")
    r_append.add_argument("--body", required=True, help="Record body as JSON. Input that is not valid JSON is stored as text.")
    r_append.add_argument("--idempotency-key", required=True, dest="idemp_key", help="Key that makes a retry of this append safe")
    r_append.add_argument("--created-at", default=None,
                          help="RFC 3339 timestamp (default: current UTC time). "
                               "Pass the same timestamp on retries because it is part of the idempotency digest.")
    r_append.add_argument("--targets", nargs="*", default=[], help="Peer IDs the record is addressed to")

    r_read = rec_sub.add_parser("read", help="Read records from stream")
    r_read.add_argument("--stream", required=True, dest="stream_id", help="Stream ID to read")
    r_read.add_argument("--after", type=int, default=0, help="Read records after this position (default %(default)s)")
    r_read.add_argument("--limit", type=int, default=50, help="Maximum records to read (default %(default)s)")

    # offset
    offset_parser = subparsers.add_parser("offset", help="Offset commands")
    off_sub = offset_parser.add_subparsers(dest="action", required=True)

    o_get = off_sub.add_parser("get", help="Get peer offset")
    o_get.add_argument("--peer", required=True, dest="peer_id", help="Peer ID")
    o_get.add_argument("--stream", required=True, dest="stream_id", help="Stream ID")

    o_adv = off_sub.add_parser("advance", help="Advance peer offset with CAS")
    o_adv.add_argument("--peer", required=True, dest="peer_id", help="Peer ID")
    o_adv.add_argument("--stream", required=True, dest="stream_id", help="Stream ID")
    o_adv.add_argument("--position", required=True, type=int, dest="new_position", help="New offset position")
    o_adv.add_argument("--revision", required=True, type=int, dest="expected_revision", help="Offset revision this update expects (from offset get)")


def _page_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be an integer from 1 to 200") from exc
    if not 1 <= limit <= 200:
        raise argparse.ArgumentTypeError("limit must be from 1 to 200")
    return limit


def _page_cursor(kind: str, cursor: str | None) -> str | None:
    if cursor is None:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid page cursor") from exc
    if not isinstance(data, dict):
        raise ValueError("invalid page cursor")
    cursor_data = cast(dict[str, Any], data)
    if cursor_data.get("v") != 1 or cursor_data.get("kind") != kind or not isinstance(cursor_data.get("after"), str):
        raise ValueError("page cursor does not match this listing")
    return cast(str, cursor_data["after"])


def _make_page_cursor(kind: str, after: str) -> str:
    raw = json.dumps({"v": 1, "kind": kind, "after": after}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def run_core(args: argparse.Namespace) -> int:
    core_read = (args.subcommand, args.action) in {
        ("peer", "get"), ("peer", "list"), ("stream", "show"), ("stream", "list"),
        ("record", "read"), ("offset", "get"),
    }
    if core_read:
        from peerhub.cli.store_select import report_missing_store
        use_json = bool(getattr(args, "json", False) or getattr(args, "format", None) == "json")
        db_source = getattr(args, "db_source", None)
        if report_missing_store(args.db, json_output=use_json, selected=db_source in ("explicit", "env"), source=db_source):
            return 5
    else:
        Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    return _run_core_cli(args, CoreStore(args.db, readonly=core_read))


def _run_core_cli(args: argparse.Namespace, store: CoreStore) -> int:
    if args.subcommand == "peer":
        if args.action == "register":
            p = store.register_peer(
                Peer(
                    peer_id=args.peer_id,
                    display_name=args.display_name,
                    adapter_ref=args.adapter_ref,
                )
            )
            emit(p.model_dump(mode="json"), args.json)
        elif args.action == "list":
            after = _page_cursor("peer", args.cursor)
            fetched = store.list_peers_page(after_peer_id=after, limit=args.limit + 1)
            items = fetched[:args.limit]
            next_cursor = _make_page_cursor("peer", items[-1].peer_id) if len(fetched) > args.limit else None
            _print_page([item.model_dump(mode="json") for item in items], next_cursor, args.format,
                        "peer_id", "display_name", store_selection={"path": args.db, "source": getattr(args, "db_source", "unknown")})
        elif args.action == "get":
            p = store.get_peer(args.peer_id)
            if not p:
                error_line("peer_not_found", f"Peer not found: {args.peer_id}", "peerhub peer list")
                return 1
            emit(p.model_dump(mode="json"), args.json)

    elif args.subcommand == "stream":
        if args.action == "create":
            s = store.create_stream(
                Stream(
                    stream_id=args.stream_id,
                    title=args.title,
                    members=args.members,
                )
            )
            emit(s.model_dump(mode="json"), args.json)
        elif args.action == "show":
            s = store.get_stream(args.stream_id)
            if not s:
                error_line("stream_not_found", f"Stream not found: {args.stream_id}", "peerhub stream list")
                return 1
            emit(s.model_dump(mode="json"), args.json)
        elif args.action == "list":
            after = _page_cursor("stream", args.cursor)
            fetched = store.list_streams_page(after_stream_id=after, limit=args.limit + 1)
            items = fetched[:args.limit]
            next_cursor = _make_page_cursor("stream", items[-1].stream_id) if len(fetched) > args.limit else None
            _print_page([item.model_dump(mode="json") for item in items], next_cursor, args.format,
                        "stream_id", "state", "title", "members",
                        store_selection={"path": args.db, "source": getattr(args, "db_source", "unknown")})

    elif args.subcommand == "record":
        if args.action == "append":
            try:
                body_data: Any = json.loads(args.body)
            except Exception:
                body_data = args.body

            rec = store.append_record(
                stream_id=args.stream_id,
                author_peer_id=args.author_peer_id,
                kind=args.kind,
                body=body_data,
                idempotency_key=args.idemp_key,
                targets=args.targets,
                created_at=args.created_at or utc_now_iso(),
            )
            emit(rec.model_dump(mode="json"), args.json)

        elif args.action == "read":
            records = store.read_records(
                stream_id=args.stream_id,
                after_position=args.after,
                limit=args.limit,
            )
            out = [r.model_dump() for r in records]
            emit(out, args.json)

    elif args.subcommand == "offset":
        if args.action == "get":
            off = store.get_offset(args.peer_id, args.stream_id)
            emit(off.model_dump(mode="json"), args.json)
        elif args.action == "advance":
            off = store.advance_offset_cas(
                peer_id=args.peer_id,
                stream_id=args.stream_id,
                new_position=args.new_position,
                expected_revision=args.expected_revision,
            )
            emit(off.model_dump(mode="json"), args.json)

    return 0


def _print_page(items: list[dict[str, Any]], next_cursor: str | None, output_format: str,
                *columns: str, store_selection: dict[str, str]) -> None:
    use_json = output_format == "json"
    if use_json:
        print(json.dumps({"schema_version": "1.0", "store_selection": store_selection,
                          "items": items, "next_cursor": next_cursor}, ensure_ascii=True, indent=2))
        return
    if not items:
        print("(no results)")
        return
    values = [[", ".join(row[key]) if isinstance(row[key], list) else str(row[key] or "") for key in columns]
              for row in items]
    widths = [max(len(column), *(len(row[i]) for row in values)) for i, column in enumerate(columns)]
    print("  ".join(column.upper().ljust(widths[i]) for i, column in enumerate(columns)))
    print("  ".join("-" * width for width in widths))
    for row in values:
        print("  ".join(value.ljust(widths[i]) for i, value in enumerate(row)))
    if next_cursor:
        print(f"next_cursor: {next_cursor}")
