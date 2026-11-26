"""Provider asks and corrections to stored ask outcomes."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, cast

from peerhub.cli.arguments import WORKSPACE_OPTIONS
from peerhub.cli.output import emit
from peerhub.cli.support import error_line
from peerhub.core import load_limits
from peerhub.core.peer_kinds import BUILTIN_PEER_LABELS

if TYPE_CHECKING:
    from peerhub.cli.arguments import Subparsers
    from peerhub.extensions.bridge_claims import ClaimToken


ASK_QUERY_FILE_OPTIONS = ("--query-file", "--prompt-file")


def register_ask_parser(subparsers: Subparsers) -> None:
    ask_parser = subparsers.add_parser("ask", help="Deliver one prompt through the durable Session Bridge",
                                     description="Reuse the latest compatible retained session of this peer/stream; otherwise start fresh with bounded catch-up. Select --fresh or an exact --session HANDLE. Results include a session handle and mode.")
    ask_parser.add_argument("peer", help=f"Peer ID (built-ins: {', '.join(BUILTIN_PEER_LABELS)})")
    ask_parser.add_argument("prompt", nargs="?", help=f"Prompt text (exclusive with {' or '.join(ASK_QUERY_FILE_OPTIONS)})")
    ask_parser.add_argument(*ASK_QUERY_FILE_OPTIONS, action="append", metavar="PATH",
                            help="Repeatable UTF-8 prompt file, exclusive with prompt. Files are joined in order with one blank line.")
    ask_parser.add_argument("--stream", dest="stream_id", help="Conversation Stream (default: peer:<id>:chat; ca, oa and ga: peer:<id>:<request-id>, one ask per stream)")
    ask_parser.add_argument("--request-id", help="Reuse on retries; uncertain execution is never replayed automatically")
    ask_parser.add_argument("--fresh", action="store_true", help="Allocate a fresh session with bounded Stream catch-up")
    ask_parser.add_argument("--session", metavar="HANDLE", help="Reuse this exact retained handle; incompatible sessions are rejected")
    ask_parser.add_argument("--author-peer", dest="author", default="user", help="Peer ID recorded as the prompt author (auto-registered)")
    ask_parser.add_argument(*WORKSPACE_OPTIONS, help="Provider working directory; default: current directory")
    ask_parser.add_argument("--model", help="Model to run; overrides the profile model")
    ask_parser.add_argument("--effort", help="Provider-specific reasoning effort override (default %(default)s, use the selected profile's effort if set)")
    ask_parser.add_argument("-p", "--profile", help="Explicit Runtime Target profile; model/effort override its defaults")
    ask_parser.add_argument("--writable", action="store_true", help="Let the peer write files in the workspace (default: read-only)")
    ask_parser.add_argument("--timeout-seconds", type=float, default=600,
                            help="Abort after this many seconds (default %(default)s); long asks: 1800-3600, ag up to 7200")
    ask_parser.add_argument("--silence-timeout-seconds", type=float, default=None,
                            help="Abort after this many seconds without stdout or stderr (default %(default)s, no silence timeout). "
                                 "Execution remains uncertain after an abort.")
    ask_parser.add_argument("--max-output-bytes", type=int, default=1_000_000,
                            help="Stop collecting reply output after this many bytes (default %(default)s)")
    ask_parser.add_argument("--dry-run", action="store_true", help="Print the resolved ask without contacting a provider or writing the store")
    ask_parser.add_argument("--final-only", action="store_true",
                            help="Print only the final response, status, ask ID and session handle as compact JSON (execution only)")
    ask_parser.add_argument("--json", action="store_true")


def register_outcome_parser(subparsers: Subparsers) -> None:
    outcome = subparsers.add_parser("outcome", help="Append an outcome correction to a stored ask")
    outcome.add_argument("ask_id", help="Ask ID from the receipt or requested_record_id in ask JSON")
    outcome_meanings = {"accepted": "used the result", "rejected": "declined the result", "retried": "attempted the ask again"}
    outcome.add_argument("outcome", choices=list(outcome_meanings),
                         help="Record how you handled the ask. "
                              + ", ".join(f"{choice} ({meaning})" for choice, meaning in outcome_meanings.items()))


def validate_ask_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    from peerhub.extensions.bridge_sessions import normalize_session_selector

    normalize_session_selector(fresh=args.fresh, session=args.session)
    if (args.prompt is None) == (args.query_file is None):
        parser.error(f"ask requires exactly one of prompt or {ASK_QUERY_FILE_OPTIONS[0]}")
    if args.final_only and args.dry_run:
        parser.error("ask --final-only applies to execution; remove --dry-run to use it")


def report_ask_parse_error(exc: SystemExit) -> int:
    from peerhub.cli.recovery_render import make_guidance_payload
    argument = getattr(exc, "argument_error", "").split(":", 1)[0]
    guidance_code = "ask_selector_error" if argument in ("argument --session", "argument --fresh") else "ask_unexpected_failure"
    print(json.dumps(dict(status="error", response=None, session=None,
                          error=dict(code="invalid_arguments", message="Invalid ask arguments."),
                          guidance=make_guidance_payload(guidance_code))))
    return 2


def report_ask_error(
    args: argparse.Namespace,
    argv: list[str],
    code: str,
    message: str,
    session: dict[str, Any] | None,
    exception: BaseException | None,
) -> None:
    from peerhub.cli.recovery_render import make_guidance_payload, render_exception_guidance_hint
    from peerhub.extensions.recovery_classification import select_guidance_code
    from peerhub.extensions.adapters.ag import AgRequiresWritableError
    guidance_code = select_guidance_code(exception=exception)
    db_arg = getattr(args, "db", None)
    sid = getattr(args, "stream_id", None)
    if sid is None and getattr(args, "peer", None):  # the stream this ask used, by the rule `ask` itself applies
        from peerhub.extensions.adapters.api import API_TARGETS
        from peerhub.extensions.ask import default_stream
        try:
            sid = default_stream(args.peer, getattr(args, "request_id", None), stateless=resolve_peer_kind(db_arg, args.peer, offline=getattr(args, "dry_run", False)) in API_TARGETS)
        except Exception:  # any failure the lookup itself does not handle: the error being reported wins, and no stream is named
            sid = None
    if args.final_only:
        _print_final_result({"status": "error", "session": session})
    elif args.json:
        payload: dict[str, Any] = dict(status="error", response=None, session=session, error=dict(code=code, message=message))
        if guidance_code:
            guidance = make_guidance_payload(guidance_code, db_path=db_arg, stream_id=sid)
            if isinstance(exception, AgRequiresWritableError):
                guidance.update(
                    explain_cmd="peerhub explain ag-requires-writable",
                    summary=("Next: Re-run your command with --writable (ag has no read-only sandbox; "
                             "it allows edits, so trusted workspaces only)."),
                    recover=[{
                        "argv": ["peerhub", *with_writable(argv)],
                        "purpose": "Re-run your command with --writable.",
                        "prereqs": "ag allows edits; use trusted workspaces only.",
                    }],
                )
            payload["guidance"] = guidance
        print(json.dumps(payload, ensure_ascii=True))
    hint = render_exception_guidance_hint(guidance_code or code, exception)
    error_line(guidance_code or code, message, hint)
    if not (args.json or args.final_only):
        if session is not None:
            print(_session_trailer(session))


def with_writable(argv: list[str]) -> list[str]:
    """Add --writable before a "--" separator (else at the end), so a dash-leading prompt stays a prompt (#359)."""
    if "--" in argv:
        cut = argv.index("--")
        return [*argv[:cut], "--writable", *argv[cut:]]
    return [*argv, "--writable"]


def resolve_peer_kind(db: str | None, peer: str, *, offline: bool = False) -> str | None:
    """The provider kind of a peer by the rule `ask` uses: the adapter registered in `db` (read-only) decides, else
    the name of a builtin peer; None when it is not known. A store that cannot be read (missing, a directory, locked,
    corrupt or not a database) counts as no registration."""
    from peerhub.core.peer_kinds import peer_kind

    adapter_ref: str | None = None
    if db:
        try:
            if offline:
                from peerhub.extensions.ask import open_ask_snapshot
                conn = open_ask_snapshot(Path(db).resolve())
            else:
                conn = sqlite3.connect(Path(os.path.abspath(db)).as_uri() + "?mode=ro", uri=True)
            try:
                row = conn.execute("SELECT adapter_ref FROM peers WHERE peer_id = ?", (peer,)).fetchone()
                adapter_ref = row[0] if row else None
            finally:
                conn.close()
        except sqlite3.DatabaseError:  # OperationalError (cannot open, locked) and a corrupt or foreign file
            pass
    return peer_kind(adapter_ref, peer)


def _provider_limit(detail_error: str | None) -> dict[str, Any] | None:
    """Limit evidence in a failed ask's error text; informational only, the ask stays uncertain (#186)."""
    from peerhub.extensions.adapters.common import classify_refusal_text, extract_reset_time
    limit = None
    for line in (detail_error or "").splitlines():
        r = classify_refusal_text(line.split(": ", 1)[1] if line.startswith(("ag exited", "cx exited", "cc exited")) else line)
        if r is not None and r["reason"] in ("quota_exhausted", "usage_limit"):
            if limit is None:
                limit = {"reason": r["reason"], "reset_time": r.get("reset_time")}
    if limit is not None:
        limit["reset_time"] = extract_reset_time(detail_error or "")
    return limit


def _session_trailer(session: dict[str, Any]) -> str:
    suffix = ""
    if session["mode"] == "fresh":
        fallback = " fallback" if session.get("prior_handle") and session["reason"] not in ("no_prior", "explicit_fresh") else ""
        suffix = f"{fallback} reason={session['reason']}"
    return f"session: {session['handle']} {session['mode']} gen{session['generation']}{suffix}"


def _print_final_result(result: dict[str, Any]) -> None:
    session: object = result.get("session")
    payload = {
        "response": result.get("response"),
        "status": result["status"],
        "requested_record_id": result.get("requested_record_id"),
        "session": cast("dict[str, Any]", session).get("handle") if isinstance(session, dict) else None,
    }
    print(json.dumps(payload, separators=(",", ":"), ensure_ascii=True))


ASK_RESOURCE_WAIT_SECONDS = 120.0
ASK_RESOURCE_POLL_SECONDS = 3.0


def _wait_for_ask_resources(
    acquire: Callable[[bool], ClaimToken | None],
    *,
    resource_reader: Callable[[], load_limits.ResourceSample] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> ClaimToken:
    resource_reader = resource_reader or load_limits.read_resources
    deadline: float | None = None
    warned = False
    while True:
        facts = resource_reader().facts()
        warnings = load_limits.load_warnings(facts)
        low_ram = facts["ram_ok"] is False
        token = acquire(low_ram)
        if token is not None:
            if warnings and not warned:
                print(f"Warning: {'; '.join(warnings)}. Starting ask.", file=sys.stderr, flush=True)
            return token
        now = monotonic()
        if deadline is None:
            deadline = now + ASK_RESOURCE_WAIT_SECONDS
        remaining = deadline - now
        if not warned and warnings:
            print(
                f"Warning: {'; '.join(warnings)}; waiting up to {ASK_RESOURCE_WAIT_SECONDS:g}s for active asks.",
                file=sys.stderr,
                flush=True,
            )
            warned = True
        if remaining <= 0:
            token = acquire(False)
            assert token is not None
            print(f"Warning: {'; '.join(warnings)}. Starting ask.", file=sys.stderr, flush=True)
            return token
        sleep(min(ASK_RESOURCE_POLL_SECONDS, remaining))


def run_ask(args: argparse.Namespace) -> int:
    from peerhub.cli.monitor_refresh import refresh_dispatch_quota
    from peerhub.extensions.ask import ask, open_ask_snapshot
    query_files: list[str] = args.query_file or []
    if query_files:
        parts: list[str] = []
        total_bytes = 0
        for index, path in enumerate(query_files):
            contents = Path(path).read_bytes()
            total_bytes += len(contents)
            part = contents.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
            if index:
                part = part.lstrip("\n")
            if index < len(query_files) - 1:
                part = part.rstrip("\n")
            parts.append(part)
        prompt = "\n\n".join(parts)
        if total_bytes > 64 * 1024:
            print("Warning: inlined query exceeds 64 KB. Consider paths or a diff.", file=sys.stderr)
    else:
        prompt = args.prompt
    text_output = not (args.json or args.final_only)
    terminal_notices = not args.dry_run and text_output and sys.stderr.isatty() and os.environ.get("PEERHUB_NO_RECEIPT") != "1"
    has_streamed = False
    output_has_newline = False
    def show_output(text: str) -> None:
        nonlocal has_streamed, output_has_newline
        has_streamed = True
        output_has_newline = text.endswith("\n")
        print(text, end="", flush=True)

    def show_budget_warning() -> None:
        writable = " --writable" if args.writable else ""
        profile = f" --profile {args.profile}" if args.profile else ""
        print(f"Stream budget reached. Use `peerhub ask {args.peer}{profile}{writable} --fresh --stream NEW_STREAM "
              "--query-file HANDOFF.md` with a handoff covering decisions, open work, and file refs.", file=sys.stderr)

    def show_receipt(result: dict[str, Any]) -> None:
        if terminal_notices:
            from peerhub.cli.profile_stats import render_ask_receipt
            from peerhub.extensions.profile_stats import read_receipt_measurements

            ask_id = result.get("requested_record_id")
            metrics: dict[str, Any] = read_receipt_measurements(args.db, ask_id) if ask_id else {}
            print(render_ask_receipt(ask_id, args.profile, metrics), file=sys.stderr)

    try:
        result = ask(args.db, args.peer, prompt, stream_id=args.stream_id, request_id=args.request_id,
                     author=args.author, workspace=args.workspace, model=args.model, effort=args.effort,
                     profile=args.profile, silence_timeout_s=args.silence_timeout_seconds, writable=args.writable,
                     timeout_s=args.timeout_seconds, max_bytes=args.max_output_bytes,
                     fresh=args.fresh, session=args.session, query_file=query_files[-1] if query_files else None,
                     on_output=show_output if text_output else None, on_stream_budget=show_budget_warning,
                     launch_gate=_wait_for_ask_resources, before_dispatch=refresh_dispatch_quota, notice_alias=terminal_notices,
                     dry_run=bool(args.dry_run))
    except Exception:
        if has_streamed and not output_has_newline:
            print()
        if not args.dry_run:
            show_receipt({})
        raise
    if args.dry_run and "store_exists" in result:
        from peerhub.extensions.diag_running import sample_load
        load = (sample_load(args.db, connection_factory=lambda path: open_ask_snapshot(Path(path).resolve()))
                if result["store_exists"] else load_limits.load_snapshot({}, load_limits.read_resources()))
        warnings = load_limits.load_warnings(load)
        if args.json:
            emit({**result, "load": load, "resource_warnings": warnings}, True)
        else:
            for key, value in result.items():
                print(f"{key}: {json.dumps(value) if not isinstance(value, str) or not value.isprintable() else value}")
            print(load_limits.format_load(load))
            for warning in warnings:
                print(f"Warning: {warning}", file=sys.stderr)
        return 0
    from peerhub.cli.recovery_render import get_guidance, make_guidance_payload, render_guidance_hint
    from peerhub.extensions.recovery_classification import select_guidance_code
    is_success = result.get("status") in ("delivered", "recovered_terminal") and result.get("error") is None
    guidance_entry = None
    detail_error: str | None = None
    if not is_success:
        detail_obj: Any = result.get("detail")
        raw_detail_err: Any = cast(dict[str, Any], detail_obj).get("error") if isinstance(detail_obj, dict) else None
        if not raw_detail_err and isinstance(result.get("error"), dict):
            raw_detail_err = result["error"].get("message")
        detail_error = raw_detail_err if isinstance(raw_detail_err, str) else None
        guidance_code = select_guidance_code(
            status=result.get("status"),
            processed_status=result.get("processed_status"),
            certainty=result.get("certainty"),
            reason=result.get("reason"),
            detail_error=detail_error,
        )
        limit = _provider_limit(detail_error) if result.get("status") == "uncertain" else None
        if limit is not None:
            result["provider_limit"] = limit
        if guidance_code:
            sid = result.get("stream_id") or getattr(args, "stream_id", None)
            denied = result.get("denied_actions")
            ag_command_denied = (guidance_code == "ask_denied_empty" and isinstance(denied, list)
                                 and "command" in denied and resolve_peer_kind(args.db, args.peer) == "ag")
            result["guidance"] = make_guidance_payload(guidance_code, db_path=args.db, stream_id=sid,
                                                       ag_command_denied=ag_command_denied)
            guidance_entry = get_guidance(guidance_code, db_path=args.db, stream_id=sid,
                                          ag_command_denied=ag_command_denied)
    if args.final_only:
        _print_final_result(result)
    elif args.json:
        print(json.dumps(result, indent=2, ensure_ascii=True))
    elif result["status"] in ("delivered", "recovered_terminal") and result.get("response") is not None:
        if result["response"] == "":
            print("note: empty response (see --json)", file=sys.stderr)
        if has_streamed and not result.get("display_truncated"):
            if not output_has_newline:
                print()
        else:
            if has_streamed:
                print()
            print(result["response"])
    if not is_success:
        code = guidance_entry.code if guidance_entry is not None else str(result.get("status") or "delivery_failed")
        hint = render_guidance_hint(guidance_entry) if guidance_entry is not None else f"peerhub explain {code}"
        error_obj: Any = result.get("error")
        detail: object = (cast(dict[str, object], error_obj).get("message") if isinstance(error_obj, dict) else error_obj) or detail_error \
            or result.get("reason") or result.get("status")
        if code == "ask_prompt_too_large" and detail_error:
            detail = detail_error
        if result.get("status") == "not_executed":
            refusal_reason = str(result.get("reason") or "refusal")
            reason_label = {
                "usage_limit": "usage limit / quota exhausted", "quota_exhausted": "usage limit / quota exhausted",
                "invalid_model": "invalid model selection", "auth_failure": "authentication failure",
            }.get(refusal_reason, refusal_reason.replace("_", " "))
            reset_time = result.get("reset_time") or result.get("resets_at")
            reset_note = f"; reset: {reset_time}" if reset_time else ""
            detail = f"Task outcome: NOT_EXECUTED (provider refusal: {reason_label}{reset_note}; no tool execution observed)"
        pl = result.get("provider_limit")
        if pl:
            detail = (f"{detail}; provider reported {pl['reason']}" + (f" (reset: {pl['reset_time']})" if pl.get("reset_time") else "")
                      + "; the stream may remain quota-blocked after reset; check the workspace for partial edits and follow uncertain-stream recovery")
        error_line(code, str(detail), hint)
    session = result.get("session")
    if text_output and isinstance(session, dict):
        session = cast("dict[str, Any]", session)
        if has_streamed and not output_has_newline and result["status"] not in ("delivered", "recovered_terminal"):
            print()
        from peerhub.extensions.ask import default_stream
        if args.stream_id is None and result.get("stream_id") not in (None, default_stream(args.peer, None, stateless=False)):
            print(f"stream: {result['stream_id']}")  # ca/oa/ga (#378): the stream this ask got; the next ask needs another
        print(_session_trailer(session))
    show_receipt(result)
    error: dict[str, Any] | None = result.get("error")
    if error is not None and error.get("code") != "session_lost" and (error.get("code") == "legacy_selector_unsupported" or str(error.get("code", "")).startswith("session_")):
        return 2
    return 0 if is_success else 1


def run_outcome(args: argparse.Namespace) -> int:
    from peerhub.extensions.profile_stats import mark_outcome

    mark_outcome(args.db, args.ask_id, args.outcome)
    payload = {"ask_id": args.ask_id, "outcome": args.outcome}
    emit(payload, True) if args.json else print(f"{args.ask_id} {args.outcome}")
    return 0
