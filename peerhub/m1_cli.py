"""PeerHub M1 Very Simple CLI Entrypoint.

Commands:
  peer list / register
  stream list / create / show
  record append / read
  offset get / advance
  diag health
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from peerhub.m1.models import Offset, Peer, Record, Stream, StreamState, utc_now_iso
from peerhub.m1.store import CoreStore, IdempotencyConflictError, CasMismatchError, StorageCorruptError, StorageFullError, StorageReadOnlyError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="peerhub-m1", description="PeerHub M1 Very Simple CLI")
    parser.add_argument("--db", default=".peerhub_m1.db", help="Path to SQLite database")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # peer
    peer_parser = subparsers.add_parser("peer", help="Peer commands")
    peer_sub = peer_parser.add_subparsers(dest="action", required=True)
    
    p_reg = peer_sub.add_parser("register", help="Register a peer")
    p_reg.add_argument("--id", required=True, dest="peer_id")
    p_reg.add_argument("--name", dest="display_name")
    p_reg.add_argument("--adapter", dest="adapter_ref")

    p_get = peer_sub.add_parser("get", help="Get a peer")
    p_get.add_argument("--id", required=True, dest="peer_id")

    # stream
    stream_parser = subparsers.add_parser("stream", help="Stream commands")
    st_sub = stream_parser.add_subparsers(dest="action", required=True)
    
    s_create = st_sub.add_parser("create", help="Create a stream")
    s_create.add_argument("--id", required=True, dest="stream_id")
    s_create.add_argument("--title")
    s_create.add_argument("--members", nargs="*", default=[])

    s_show = st_sub.add_parser("show", help="Show stream details")
    s_show.add_argument("--id", required=True, dest="stream_id")

    # record
    record_parser = subparsers.add_parser("record", help="Record commands")
    rec_sub = record_parser.add_subparsers(dest="action", required=True)

    r_append = rec_sub.add_parser("append", help="Append a record")
    r_append.add_argument("--stream", required=True, dest="stream_id")
    r_append.add_argument("--author", required=True, dest="author_peer_id")
    r_append.add_argument("--kind", required=True)
    r_append.add_argument("--body", required=True, help="JSON body string")
    r_append.add_argument("--idemp-key", required=True)
    r_append.add_argument("--created-at", default=None, help="RFC3339; pass the same value on retries (part of the idempotency digest)")
    r_append.add_argument("--targets", nargs="*", default=[])

    r_read = rec_sub.add_parser("read", help="Read records from stream")
    r_read.add_argument("--stream", required=True, dest="stream_id")
    r_read.add_argument("--after", type=int, default=0)
    r_read.add_argument("--limit", type=int, default=50)

    # offset
    offset_parser = subparsers.add_parser("offset", help="Offset commands")
    off_sub = offset_parser.add_subparsers(dest="action", required=True)

    o_get = off_sub.add_parser("get", help="Get peer offset")
    o_get.add_argument("--peer", required=True, dest="peer_id")
    o_get.add_argument("--stream", required=True, dest="stream_id")

    o_adv = off_sub.add_parser("advance", help="Advance peer offset with CAS")
    o_adv.add_argument("--peer", required=True, dest="peer_id")
    o_adv.add_argument("--stream", required=True, dest="stream_id")
    o_adv.add_argument("--position", required=True, type=int, dest="new_position")
    o_adv.add_argument("--revision", required=True, type=int, dest="expected_revision")

    # diag
    diag_parser = subparsers.add_parser("diag", help="Readonly diagnostics")
    diag_sub = diag_parser.add_subparsers(dest="action", required=True)
    
    d_health = diag_sub.add_parser("health", help="Inspect stream health (read-only)")
    d_health.add_argument("--stream", required=True, dest="stream_id")
    d_health.add_argument("--obs-db", default=".peerhub_obs.db")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.subcommand == "diag":  # optional first-party extension: Core must work without it (E2E-009)
            try:
                from peerhub.extensions.diag import ReadonlyDiag
            except ImportError as e:
                print(f"ERROR: the diagnostics extension is unavailable ({e})", file=sys.stderr)
                return 5
        store = CoreStore(args.db)
        if args.subcommand == "peer":
            if args.action == "register":
                p = store.register_peer(
                    Peer(
                        peer_id=args.peer_id,
                        display_name=args.display_name,
                        adapter_ref=args.adapter_ref,
                    )
                )
                print(p.model_dump_json(indent=2))
            elif args.action == "get":
                p = store.get_peer(args.peer_id)
                if not p:
                    print(f"Peer not found: {args.peer_id}", file=sys.stderr)
                    return 1
                print(p.model_dump_json(indent=2))

        elif args.subcommand == "stream":
            if args.action == "create":
                s = store.create_stream(
                    Stream(
                        stream_id=args.stream_id,
                        title=args.title,
                        members=args.members,
                    )
                )
                print(s.model_dump_json(indent=2))
            elif args.action == "show":
                s = store.get_stream(args.stream_id)
                if not s:
                    print(f"Stream not found: {args.stream_id}", file=sys.stderr)
                    return 1
                print(s.model_dump_json(indent=2))

        elif args.subcommand == "record":
            if args.action == "append":
                try:
                    body_data = json.loads(args.body)
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
                print(rec.model_dump_json(indent=2))

            elif args.action == "read":
                records = store.read_records(
                    stream_id=args.stream_id,
                    after_position=args.after,
                    limit=args.limit,
                )
                out = [r.model_dump() for r in records]
                print(json.dumps(out, indent=2, ensure_ascii=False))

        elif args.subcommand == "offset":
            if args.action == "get":
                off = store.get_offset(args.peer_id, args.stream_id)
                print(off.model_dump_json(indent=2))
            elif args.action == "advance":
                off = store.advance_offset_cas(
                    peer_id=args.peer_id,
                    stream_id=args.stream_id,
                    new_position=args.new_position,
                    expected_revision=args.expected_revision,
                )
                print(off.model_dump_json(indent=2))

        elif args.subcommand == "diag":
            if args.action == "health":
                diag = ReadonlyDiag(args.db)
                result = diag.inspect_stream_health(args.stream_id)
                print(json.dumps(result, indent=2, ensure_ascii=False))

        return 0

    except (StorageReadOnlyError, StorageFullError, StorageCorruptError) as e:
        print(f"STORAGE ERROR ({type(e).__name__}): {e}", file=sys.stderr)
        return 4
    except IdempotencyConflictError as e:
        print(f"CONFLICT ERROR: {e}", file=sys.stderr)
        return 2
    except CasMismatchError as e:
        print(f"CAS ERROR: {e}", file=sys.stderr)
        return 3
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
