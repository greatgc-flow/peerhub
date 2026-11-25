"""Bounded-parallel batch commands."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from peerhub.core.load_limits import load_warnings
from peerhub.cli.arguments import ArgumentParser
from peerhub.cli.ask_cmd import resolve_peer_kind
from peerhub.cli.support import error_line, print_warnings

if TYPE_CHECKING:
    from peerhub.cli.arguments import Subparsers


class _BatchRunParser(ArgumentParser):
    def format_help(self, *args: Any, **kwargs: Any) -> str:
        # Core commands build the parser even when extensions are disabled.
        try:
            from peerhub.extensions.batch import manifest_help
        except ImportError:
            pass
        else:
            self.epilog = manifest_help()
        return super().format_help(*args, **kwargs)


def register_batch_parser(subparsers: Subparsers) -> None:
    batch_parser = subparsers.add_parser("batch", help="Flat bounded-parallel asks")
    batch_sub = batch_parser.add_subparsers(dest="action", required=True, parser_class=_BatchRunParser)
    batch_run = batch_sub.add_parser("run", help="Run a validated task manifest",
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    batch_run.add_argument("manifest", help="Path to the JSON manifest (see the example below)")
    batch_run.add_argument("--batch-id", required=True, help="Batch ID; stream and request IDs derive from it")
    batch_run.add_argument("--max-parallel", required=True, type=int, help="Caller-chosen maximum simultaneous tasks (integer >= 1)")
    batch_run.add_argument("--dry-run", action="store_true", help="Validate the manifest and list the tasks without running them")
    batch_run.add_argument("--json", action="store_true")
    batch_run.add_argument("--responses", action="store_true", help="Include each completed task's reply text in the report")


def _batch_load_sample(db: str) -> dict[str, Any]:
    try:
        from peerhub.extensions.diag_running import sample_load

        return sample_load(db)
    except Exception:  # diagnostic sampling is best-effort and cannot affect manifest validation
        from peerhub.core.load_limits import ResourceSample, load_snapshot

        return load_snapshot(None, ResourceSample())


def run_batch(args: argparse.Namespace) -> int:
    import signal
    import threading
    from dataclasses import replace
    from functools import cache

    from peerhub.extensions import batch

    db = os.path.abspath(args.db)
    warnings: list[str] | None = [] if args.dry_run else None

    @cache
    def kind_of(peer: str) -> str | None:
        return resolve_peer_kind(db, peer)

    try:
        max_parallel = batch.check_max_parallel(args.max_parallel)
        tasks = batch.load_manifest(args.manifest, batch_id=args.batch_id)
        workspaces = batch.validate_tasks(tasks, kind_of=kind_of, warnings=warnings)
        tasks = [replace(task, writable=True) if kind_of(task.peer) == "ag" else task for task in tasks]
        if warnings is not None:
            from peerhub.extensions.ask import build_ask_target, read_replay_counts
            from peerhub.extensions.runtime_contract import PrespawnError

            @cache
            def profile_display(peer: str, workspace: Path, writable: bool, profile: str | None) -> str:
                kind = kind_of(peer)
                if kind is not None:
                    try:
                        resolved = build_ask_target(kind, workspace, writable=writable, profile=profile).session_binding()["profile"]
                    except (OSError, ValueError, PrespawnError):
                        if profile is not None:
                            raise
                        resolved = None
                    if profile is not None:
                        return profile
                    if resolved:
                        return batch.RESOLVED_DEFAULT_PROFILE_DISPLAY.format(profile=resolved)
                return batch.DEFAULT_PROFILE_DISPLAY

            from peerhub.core.load_limits import format_load

            load = _batch_load_sample(db)
            warnings.extend(load_warnings(load))
            rows: list[dict[str, Any]] = []
            for task in tasks:
                stream, request = batch.derive_ids(args.batch_id, task.task_id)
                try:
                    display = profile_display(task.peer, task.workspace or Path.cwd(), task.writable, task.profile)
                except (OSError, ValueError, PrespawnError) as exc:
                    raise batch.ManifestError(f"task {task.task_id}: {exc}") from exc
                rows.append(dict(task_id=task.task_id, peer=task.peer, stream=stream,
                                 request_id=request, writable=task.writable, profile=task.profile,
                                 profile_display=display,
                                 workspace=workspaces[task.task_id]))
            replay = read_replay_counts(Path(db), ((row["stream"], row["peer"], row["request_id"]) for row in rows))
            if args.json:
                dry_report: dict[str, Any] = dict(dry_run=True, batch_id=args.batch_id, max_parallel=max_parallel,
                                                  tasks=rows, load=load)
                if replay is not None:
                    dry_report["replay"] = replay
                dry_report["warnings"] = warnings
                print(json.dumps(dry_report, indent=2, ensure_ascii=True))
            else:
                print(f"Dry run: {args.batch_id} (max_parallel={max_parallel})")
                print("TASK PEER STREAM REQUEST_ID WRITABLE PROFILE WORKSPACE")
                for row in rows:
                    print(f"{row['task_id']} {row['peer']} {row['stream']} {row['request_id']} {row['writable']} "
                          f"{row['profile_display']} {row['workspace']}")
                if replay is not None:
                    print(batch.REPLAY_LINE.format(**replay))
                print(format_load(load))
                for warning in warnings:
                    print(warning)
            return 0
        if getattr(args, "db_source", "default") not in ("explicit", "env"):
            from peerhub.cli.store_select import report_missing_store
            if report_missing_store(db, json_output=bool(getattr(args, "json", False)),
                                    selected=False, source=getattr(args, "db_source", "default")):
                return 5
        # Workers race to create workspace.generation on first use of a new workspace (WinError 32): create it once here.
        from peerhub.core.workspace import Workspace
        from peerhub.extensions.bridge_ledger import reap_dead_asks
        Workspace(Path(db).parent)
        reap_dead_asks(db)
        cancel = threading.Event()

        def request_cancel(signum: int, frame: object) -> None:
            cancel.set()

        signals = [signal.SIGINT, signal.SIGTERM]
        if sys.platform == "win32":
            signals.append(signal.SIGBREAK)
        previous: dict[int, Any] = {}
        try:
            for signum in signals:
                previous[signum] = signal.signal(signum, request_cancel)
            report = batch.run_batch(tasks, batch_id=args.batch_id, max_parallel=max_parallel,
                                     db=db, cancel=cancel, argv_builder=batch.default_argv_builder,
                                     responses=args.responses)
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
        print(json.dumps(report, indent=2, ensure_ascii=True) if args.json else batch.render_text(report))
        return batch.exit_code(report)
    except batch.ManifestError as exc:
        if args.json:
            error: dict[str, Any] = dict(status="error", error=dict(code="invalid_manifest", message=str(exc)))
            if warnings is not None:
                error["warnings"] = warnings
            print(json.dumps(error, ensure_ascii=True))
        error_line("invalid_manifest", str(exc), "peerhub batch run --help")
        if not args.json and warnings:
            print_warnings({"warnings": warnings})
        return 1
