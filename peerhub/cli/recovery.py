"""Guidance and recovery registry for PeerHub CLI outcomes (S2-A).

Maps non-success outcome codes to structured guidance and recovery advice:
- meaning: plain English explanation of what happened
- why: why the outcome occurred
- recover: list of recovery actions with argv arrays and purpose/prereqs
  (shell-rendered for human output; never interpolated with provider text)
- avoid: explicit advice on what NOT to do
- docs: documentation reference pointer
"""
from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast


@dataclass(frozen=True)
class GuidanceEntry:
    code: str
    meaning: str
    why: str
    recover: list[dict[str, Any]]
    avoid: str
    docs: str
    exits: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())

    @property
    def exit_code(self) -> int | None:
        """Scalar exit code if entry has exactly one exit code; None if context-dependent."""
        distinct_codes = {int(e["code"]) for e in self.exits}
        return next(iter(distinct_codes)) if len(distinct_codes) == 1 else None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "code": self.code,
            "meaning": self.meaning,
            "why": self.why,
            "recover": self.recover,
            "avoid": self.avoid,
            "docs": self.docs,
            "exits": self.exits,
        }
        if self.exit_code is not None:
            data["exit_code"] = self.exit_code
        return data

    def summary(self) -> str:
        next_action = self.recover[0]["purpose"] if self.recover else "Inspect diagnostics"
        return f"{self.meaning} Next: {next_action}. {self.avoid}"


@dataclass(frozen=True)
class ResultStatusEntry:
    status: str
    exit_code: int
    guidance_code: str
    meaning: str
    safe_action: str
    processed_status_cases: list[dict[str, Any]] = field(default_factory=lambda: list[dict[str, Any]]())

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "status": self.status,
            "exit_code": self.exit_code,
            "guidance_code": self.guidance_code,
            "meaning": self.meaning,
            "safe_action": self.safe_action,
        }
        if self.processed_status_cases:
            data["processed_status_cases"] = self.processed_status_cases
        return data


RESULT_STATUS_REGISTRY: dict[str, ResultStatusEntry] = {
    "blocked_uncertain": ResultStatusEntry(
        status="blocked_uncertain",
        exit_code=1,
        guidance_code="ask_blocked_uncertain",
        meaning="New request on this stream is blocked because an earlier record on the stream ended in an uncertain state.",
        safe_action="Inspect stream health and uncommitted evidence via `peerhub diag health --stream STREAM_ID`. Abandon the blocked stream and continue on a new stream.",
    ),
    "delivered": ResultStatusEntry(
        status="delivered",
        exit_code=0,
        guidance_code="(none)",
        meaning="Request executed successfully and terminal response was committed to the stream.",
        safe_action="None required. Continue subsequent turns on the same stream to reuse the active session.",
    ),
    "denied_empty": ResultStatusEntry(
        status="denied_empty",
        exit_code=1,
        guidance_code="ask_denied_empty",
        meaning="Turn ended (certainty TERMINAL) with an empty response after the provider denied tool action(s); not a usable answer and task effects are unverified.",
        safe_action="Treat the reply as unusable; the denied tool names are in denied_actions. Do not resume the stream: start a new stream with the needed content inlined via --query-file, and verify the workspace.",
    ),
    "failed_not_started": ResultStatusEntry(
        status="failed_not_started",
        exit_code=1,
        guidance_code="ask_prompt_too_large / ask_launch_failed / ask_delivery_failed",
        meaning="Provider execution failed before starting (prompt limit, missing executable, OS spawn rejection or pre-spawn policy check).",
        safe_action="For ask_prompt_too_large, split the prompt under the limit, or for an ag argv limit use cc/cx on a separate stream, with a new request-id; otherwise inspect peer adapter availability and PATH configuration via `peerhub diag health`.",
    ),
    "not_executed": ResultStatusEntry(
        status="not_executed",
        exit_code=1,
        guidance_code="ask_refusal",
        meaning="Provider explicitly refused execution before performing tool calls (e.g. quota exhausted, auth failure, invalid model).",
        safe_action="Inspect quota pools and reset times via `peerhub diag quota` or verify profile configuration via `peerhub profile list`.",
    ),
    "pending_earlier_record": ResultStatusEntry(
        status="pending_earlier_record",
        exit_code=1,
        guidance_code="context-dependent",
        meaning="Cycle processed an earlier record on the stream rather than this prompt. Meaning and guidance depend on processed_status (earlier may have delivered, refused, failed, or ended uncertain).",
        safe_action="Inspect processed_status: if earlier delivered, retry this queued prompt with the same request-id, prompt, binding and selector; check quota/health if refused/failed; abandon stream if uncertain.",
        processed_status_cases=[
            {
                "processed_status": "delivered / recovered_terminal",
                "guidance_code": "(none)",
                "meaning": "Earlier record delivered successfully; prompt was queued behind it and remains unexecuted in this cycle.",
                "safe_action": "Retry peerhub ask on the same stream with the same request-id, prompt, binding and selector to advance the queued prompt; compatible retained sessions can be reused.",
            },
            {
                "processed_status": "not_executed",
                "guidance_code": "ask_refusal",
                "meaning": "Earlier record was refused by the provider (quota, auth, invalid model).",
                "safe_action": "Inspect quota via peerhub diag quota or verify profile configuration.",
            },
            {
                "processed_status": "failed_not_started",
                "guidance_code": "ask_prompt_too_large / ask_launch_failed / ask_delivery_failed",
                "meaning": "Earlier record failed before execution started (prompt limit, missing executable or pre-spawn policy check).",
                "safe_action": "For ask_prompt_too_large, split the prompt under the limit, or for an ag argv limit use cc/cx on a separate stream, with a new request-id; otherwise inspect provider binary and PATH configuration via peerhub diag health.",
            },
            {
                "processed_status": "uncertain / blocked_uncertain",
                "guidance_code": "ask_blocked_uncertain",
                "meaning": "Earlier record ended in an uncertain state, blocking further progress on this stream.",
                "safe_action": "Inspect stream via peerhub diag health --stream STREAM_ID. Abandon stream; start a new stream for subsequent work.",
            },
        ],
    ),
    "recovered_terminal": ResultStatusEntry(
        status="recovered_terminal",
        exit_code=0,
        guidance_code="(none)",
        meaning="Prior idempotent execution or replayed terminal response recovered.",
        safe_action="None required. Prior committed result recovered.",
    ),
    "session_unavailable": ResultStatusEntry(
        status="session_unavailable",
        exit_code=2,
        guidance_code="ask_selector_error",
        meaning="Session selector or handle validation failed before invocation could begin.",
        safe_action="List valid sessions via `peerhub session list` or pass `--fresh` to allocate a fresh session.",
    ),
    "uncertain": ResultStatusEntry(
        status="uncertain",
        exit_code=1,
        guidance_code="ask_uncertain",
        meaning="Request started execution but ended without terminal certainty (timeout or abort).",
        safe_action="Inspect stream health via `peerhub diag health --stream STREAM_ID`. Do not retry or pass `--fresh` on this stream; abandon stream.",
    ),
}




REGISTRY: dict[str, GuidanceEntry] = {
    "account_invalid": GuidanceEntry(
        code="account_invalid",
        meaning="Account binding or launch validation failed before provider execution.",
        why="The peer identity, immutable registry entry or protected root boundary is invalid.",
        recover=[{"argv": ["peerhub", "account", "show", "--peer", "PEER_ID", "--show-root"],
                  "purpose": "Inspect the binding and correct the reported validation problem",
                  "prereqs": "Use the intended peer and selected store."}],
        avoid="Do not overwrite or reuse a binding for another login; bind a new peer and root.",
        docs="docs/accounts.md",
        exits=[{"code": 1, "context": "Account registration or launch validation failed."}],
    ),
    "invalid_manifest": GuidanceEntry(
        code="invalid_manifest",
        meaning="Batch validation failed before any worker was launched.",
        why="The manifest, peer resolution, workspace boundaries or batch options are invalid.",
        recover=[{"argv": ["peerhub", "batch", "run", "tasks.json", "--batch-id", "BATCH_ID",
                           "--max-parallel", "1", "--dry-run"],
                  "purpose": "Correct the reported validation error and validate the manifest",
                  "prereqs": "Use the intended manifest and batch identifier."}],
        avoid="Do not change the batch identifier to bypass an uncertain earlier execution.",
        docs="docs/batch.md",
        exits=[{"code": 1, "context": "Batch manifest or option validation failed."}],
    ),
    "ask_uncertain": GuidanceEntry(
        code="ask_uncertain",
        meaning="Request started execution on the peer adapter but ended without terminal certainty (e.g. timeout or abort).",
        why="The provider or process exceeded timeout or was interrupted before producing terminal truth. Execution state remains uncertain.",
        recover=[
            {
                "argv": ["peerhub", "diag", "health", "--stream", "STREAM_ID"],
                "purpose": "Inspect stream health and check for uncommitted or partial activity",
                "prereqs": "Provide the affected stream identifier.",
            },
            {
                "argv": ["peerhub", "diag", "health"],
                "purpose": "Check overall store and peer health",
                "prereqs": "None.",
            },
        ],
        avoid="Do not retry the prompt under a new request-id, do not pass --fresh, and do not delete claims. Continuing work on this stream is blocked to prevent split-brain execution; start a new stream for subsequent work. If the ask used the default timeout, do not retry on the same stream; start a new stream with explicit --timeout-seconds (long reviews: 1800 or more).",
        docs="docs/remote-ask.md",
        exits=[
            {"code": 1, "context": "Execution timed out or aborted without terminal certainty (uncertain result status)."}
        ],
    ),
    "ask_blocked_uncertain": GuidanceEntry(
        code="ask_blocked_uncertain",
        meaning="New request on this stream is blocked because an earlier record on the stream ended in an uncertain state.",
        why="Stream ordering requires resolving or abandoning uncertain prior work before appending new turns on the stream. Inspect stream health and uncommitted evidence before deciding whether to retry or start a new stream.",
        recover=[
            {
                "argv": ["peerhub", "diag", "health", "--stream", "STREAM_ID"],
                "purpose": "Inspect the blocked stream and review uncommitted evidence",
                "prereqs": "Provide the affected stream identifier.",
            },
        ],
        avoid="Do not retry the prompt under a new request-id, do not pass --fresh, and do not delete claims. Abandon the blocked stream and continue genuinely new work on a new stream with a summary of prior context. If the ask used the default timeout, do not retry on the same stream; start a new stream with explicit --timeout-seconds (long reviews: 1800 or more).",
        docs="docs/remote-ask.md",
        exits=[
            {"code": 1, "context": "Stream has an earlier record in an uncertain state, blocking subsequent turns (blocked_uncertain result status)."}
        ],
    ),
    "ask_claim_held": GuidanceEntry(
        code="ask_claim_held",
        meaning="A concurrent ask operation or active lease currently holds the delivery claim for this peer and stream.",
        why="Only one caller can execute an ask turn per peer and stream at a time to prevent concurrent dispatch races. Wait for the active turn to finish, or use a distinct --stream for genuinely separate parallel work. If your own earlier ask looked cut off because your tool stopped waiting, it may still be running: wait for the claim to end, then rerun the identical command with the same --request-id; a completed turn replays its stored reply; otherwise follow the returned uncertainty guidance (ask_uncertain or ask_blocked_uncertain) (lesson caller-timeout-claim-held).",
        recover=[
            {
                "argv": ["peerhub", "diag", "health", "--stream", "STREAM_ID"],
                "purpose": "Inspect store connectivity and stream activity (note: diag health checks store and stream records, not active in-memory leases or claims)",
                "prereqs": "Wait for the active turn or lease timeout to expire.",
            },
        ],
        avoid="Do not delete claim tables or database files, do not force --fresh, and do not bypass claim contention with duplicate request IDs.",
        docs="docs/design/a2a-inbound-server.md",
        exits=[
            {"code": 1, "context": "Concurrent ask operation or active lease currently holds the delivery claim (ClaimHeldError)."}
        ],
    ),
    "ask_selector_error": GuidanceEntry(
        code="ask_selector_error",
        meaning="Session selector or handle validation failed before invocation could begin.",
        why="The specified session handle is invalid, tombstoned, incompatible with the runtime binding, or flags like --fresh and --session conflict.",
        recover=[
            {
                "argv": ["peerhub", "session", "list"],
                "purpose": "List available valid sessions and check session handles",
                "prereqs": "None.",
            },
        ],
        avoid="Do not invent session handles, do not combine --fresh with --session, and do not retry without correcting the session selector.",
        docs="docs/implementation/RETAINED_SESSIONS.md",
        exits=[
            {"code": 1, "context": "Session lost error (SessionLostError) when retained session continuity is broken."},
            {"code": 2, "context": "Selector syntax error, unsupported selector, conflicting flags, or session handle not found (session_unavailable status or invalid_arguments)."},
        ],
    ),
    "ask_refusal": GuidanceEntry(
        code="ask_refusal",
        meaning="The provider explicitly refused execution before performing any tool calls (task outcome NOT_EXECUTED).",
        why="The provider reported quota exhaustion, invalid model selection, authentication failure, or another refusal before start.",
        recover=[
            {
                "argv": ["peerhub", "diag", "quota"],
                "purpose": "Inspect provider quota pools and reset times",
                "prereqs": "None.",
            },
            {
                "argv": ["peerhub", "profile", "list"],
                "purpose": "Review available model profiles and configured bindings",
                "prereqs": "None.",
            },
        ],
        avoid="Do not blindly retry the prompt with a new request ID without resolving the quota, auth, or model configuration issue.",
        docs="docs/adapters/README.md",
        exits=[
            {"code": 1, "context": "Provider refused execution before starting (quota exhaustion, invalid model, auth failure; not_executed status)."}
        ],
    ),
    "ask_prompt_too_large": GuidanceEntry(
        code="ask_prompt_too_large",
        meaning="The prompt exceeds the provider inline byte limit; no provider process started.",
        why="Every provider has a 1,000,000-byte inline maximum; ag passes the prompt in argv, so its limit is lower (30,000 bytes on Windows, 120,000 bytes on POSIX). The byte count includes injected catch-up history. cc/cx use stdin and only have the 1,000,000-byte maximum.",
        recover=[{
            "argv": ["peerhub", "ask", "cc", "--query-file", "CONTENT_FILE", "--stream", "NEW_STREAM_ID", "--request-id", "NEW_REQUEST_ID"],
            "purpose": "split the prompt into parts under the limit (one ask each); for an ag argv limit, send it to cc/cx instead (stdin, 1,000,000-byte maximum) on a separate stream, since a stream belongs to one peer; if the prompt itself is under the limit, catch-up history pushed it over, so start a new --stream; a changed prompt needs a new request-id",
            "prereqs": "Use the byte count and limit in the error detail; switching providers does not help above the shared 1,000,000-byte maximum.",
        }],
        avoid="Do not retry an unchanged over-limit prompt or reuse its request-id for a changed prompt.",
        docs="docs/adapters/ag.md",
        exits=[{"code": 1, "context": "Prompt exceeds the provider inline limit before spawn (PrespawnError or failed_not_started status)."}],
    ),
    "ask_launch_failed": GuidanceEntry(
        code="ask_launch_failed",
        meaning="Provider CLI executable could not be found or failed to spawn before execution started.",
        why="The configured provider binary (codex, claude, or agy) is missing from PATH or the OS rejected process creation before any turn started.",
        recover=[
            {
                "argv": ["peerhub", "diag", "health"],
                "purpose": "Inspect peer adapter availability and PATH configuration",
                "prereqs": "Ensure the provider CLI is installed and on PATH.",
            },
        ],
        avoid="Do not loop retries or pass --fresh until the provider CLI executable is installed and reachable on PATH.",
        docs="docs/remote-ask.md",
        exits=[
            {"code": 1, "context": "Provider CLI binary missing from PATH or process spawn rejected by OS (failed_not_started status or PrespawnError)."}
        ],
    ),
    "ask_stream_peer_mismatch": GuidanceEntry(
        code="ask_stream_peer_mismatch",
        meaning="The target stream is already bound to a different ask target peer.",
        why="Each ask stream maintains ordered single-peer session continuity and cannot be retargeted to another peer mid-stream.",
        recover=[
            {
                "argv": ["peerhub", "stream", "list"],
                "purpose": "Inspect existing streams and select or create a stream for this peer",
                "prereqs": "Use a distinct --stream per target peer.",
            },
        ],
        avoid="Do not mix multiple target peers on the same ask stream or force --fresh to override stream peer binding.",
        docs="docs/remote-ask.md",
        exits=[
            {"code": 2, "context": "Target stream is already bound to a different peer (StreamPeerMismatchError)."}
        ],
    ),
    "ask_delivery_failed": GuidanceEntry(
        code="ask_delivery_failed",
        meaning="Adapter invocation failed before execution started or failed critically during delivery.",
        why="The adapter rejected pre-spawn execution policy (e.g. ag without --writable), failed pre-spawn validation, or encountered a storage/delivery error.",
        recover=[
            {
                "argv": ["peerhub", "diag", "health"],
                "purpose": "Inspect peer adapter status and diagnostic health",
                "prereqs": "None.",
            },
        ],
        avoid="Do not loop blind retries or pass --fresh without verifying adapter flags and availability. After a proven pre-spawn failure, retry unchanged binding with the same request-id; a corrected binding requires a new request-id and supersedes the failed request. Uncertain execution still requires stopping that stream.",
        docs="docs/remote-ask.md",
        exits=[
            {"code": 1, "context": "Adapter invocation failure, rejected pre-spawn execution policy, or general execution error (failed_not_started status or adapter exception)."},
            {"code": 4, "context": "Storage layer failure (StorageReadOnlyError, StorageFullError, StorageCorruptError)."},
        ],
    ),
    "ask_schema_version_error": GuidanceEntry(
        code="ask_schema_version_error",
        meaning="The selected store uses an unsupported schema version.",
        why="This PeerHub version cannot read the selected store schema.",
        recover=[{
            "argv": ["peerhub", "--version"],
            "purpose": "Check the installed version and use a PeerHub version compatible with this store",
            "prereqs": "Verify the selected --db path is the intended store.",
        }],
        avoid="Do not edit schema version metadata or downgrade the store to bypass this check.",
        docs="docs/migrations.md",
        exits=[{"code": 6, "context": "Unsupported store schema version (SchemaVersionError)."}],
    ),
    "ask_cas_conflict": GuidanceEntry(
        code="ask_cas_conflict",
        meaning="A compare-and-swap revision check failed.",
        why="The stored revision changed before the guarded update could commit.",
        recover=[{
            "argv": ["peerhub", "diag", "health", "--stream", "STREAM_ID"],
            "purpose": "Inspect current stream state before retrying the original request",
            "prereqs": "Provide the affected stream identifier.",
        }],
        avoid="Do not bypass revision checks or change the request ID to force another execution; resolve uncertain delivery first.",
        docs="docs/remote-ask.md",
        exits=[{"code": 3, "context": "Compare-and-swap revision conflict (CasMismatchError)."}],
    ),
    "ask_denied_empty": GuidanceEntry(
        code="ask_denied_empty",
        meaning="The turn ended with an empty response after the provider denied tool action(s); this is not a usable answer.",
        why="The provider denied tool action(s) (headless cannot prompt), so the turn can end with no reply text; the denied names or commands are in denied_actions. Task effects are unverified.",
        recover=[
            {
                "argv": ["peerhub", "ask", "PEER_ID", "--query-file", "CONTENT_FILE", "--stream", "NEW_STREAM_ID", "--writable"],
                "purpose": "Start a new stream and inline the needed content via --query-file (say \"no tools\"); do not resume this stream",
                "prereqs": "Put the content the task needs into the query file; one task per ask; check the workspace for partial effects first.",
            },
        ],
        avoid="Do not resume this stream (a resumed turn may fabricate paths), and do not treat the empty response as a completed answer or assume the denied action ran.",
        docs="docs/remote-ask.md",
        exits=[
            {"code": 1, "context": "Empty response with denied tool action(s) (denied_empty result status)."}
        ],
    ),
    "ask_idempotency_conflict": GuidanceEntry(
        code="ask_idempotency_conflict",
        meaning="An idempotency key was reused with conflicting request data.",
        why="The stored request identity differs from the request supplied with the same key.",
        recover=[{
            "argv": ["peerhub", "record", "read", "--stream", "STREAM_ID"],
            "purpose": "Inspect the original request and reuse its prompt, binding, and session selector for a retry",
            "prereqs": "Provide the affected stream identifier.",
        }],
        avoid="Do not change request IDs to bypass a conflict or retry uncertain execution blindly.",
        docs="docs/remote-ask.md",
        exits=[{"code": 2, "context": "Conflicting data for an existing idempotency key (IdempotencyConflictError)."}],
    ),
    "ask_unexpected_failure": GuidanceEntry(
        code="ask_unexpected_failure",
        meaning="An unclassified or unexpected error occurred during ask dispatch.",
        why="An unhandled exception or internal error interrupted the ask command.",
        recover=[
            {
                "argv": ["peerhub", "diag", "health"],
                "purpose": "Inspect diagnostic evidence and stream state",
                "prereqs": "None.",
            },
        ],
        avoid="Do not retry destructive actions or invent unverified recovery commands.",
        docs="docs/remote-ask.md",
        exits=[
            {"code": 1, "context": "Unhandled exception or unexpected runtime error during ask dispatch."},
            {"code": 2, "context": "Invalid CLI arguments or unrecognized ask options (ValueError other than selector/session errors)."},
        ],
    ),
}

# The store-missing lesson owns the detailed store-selection recovery recipe.
REGISTRY["store_missing"] = GuidanceEntry(
    code="store_missing",
    meaning="The selected database does not exist.",
    why="A Git worktree may have no local store while the database lives in the main checkout; see lesson store-missing.",
    recover=[{
        "argv": ["peerhub", "explain", "store-missing"],
        "purpose": "Pass --db PATH, set PEERHUB_DB, or run from the main checkout; see the store-missing lesson for the recipe",
        "prereqs": "Identify the intended store before selecting it or explicitly creating a new one.",
    }],
    avoid="Do not create a substitute store when you intend to use existing data.",
    docs="README.md#store-selection-and-existing-data",
    exits=[{"code": 5, "context": "The selected database does not exist."}],
)

# Shared CLI errors; ask-specific guidance retains its more precise recovery policy.
for _code, _exit, _meaning, _argv in (
    ("invalid_arguments", 2, "The command arguments were rejected before dispatch.", ["peerhub", "guide"]),
    ("peer_not_found", 1, "The selected peer does not exist in this store.", ["peerhub", "peer", "list"]),
    ("stream_not_found", 1, "The selected stream does not exist in this store.", ["peerhub", "stream", "list"]),
    ("session_not_found", 1, "The selected retained session does not exist.", ["peerhub", "session", "list"]),
    ("diag_unavailable", 5, "Diagnostic evidence is unavailable or incomplete.", ["peerhub", "diag", "health"]),
    ("schema_version_error", 6, "The selected store uses an unsupported schema version.", ["peerhub", "guide"]),
    ("storage_corrupt", 4, "The selected store could not complete a storage operation.", ["peerhub", "diag", "integrity"]),
    ("idempotency_conflict", 2, "The request conflicts with an existing idempotency key.", ["peerhub", "diag", "health"]),
    ("cas_conflict", 3, "The offset revision changed before the update.", ["peerhub", "offset", "get", "--peer", "PEER", "--stream", "STREAM"]),
    ("delivery_failed", 1, "The command failed before returning a usable result.", ["peerhub", "diag", "health"]),
):
    REGISTRY[_code] = GuidanceEntry(
        code=_code, meaning=_meaning,
        why="The selected store, command arguments or persisted state did not satisfy the operation's requirements.",
        recover=[{"argv": _argv, "purpose": "Inspect the reported state and correct the command or store selection", "prereqs": "Use the intended store and inspect uncertain effects before retrying."}],
        avoid="Do not blindly replay provider work whose execution may already have begun.",
        docs="docs/config-hierarchy.md", exits=[{"code": value, "context": _meaning} for value in ((1, 2) if _code == "invalid_arguments" else (4, 5) if _code == "diag_unavailable" else (_exit,))],
    )

ALIASES: dict[str, str] = {
    "uncertain": "ask_uncertain",
    "blocked_uncertain": "ask_blocked_uncertain",
    "pending_earlier_record": "ask_blocked_uncertain",
    "claim_held": "ask_claim_held",
    "selector_error": "ask_selector_error",
    "refusal": "ask_refusal",
    "launch_failed": "ask_launch_failed",
    "prompt_too_large": "ask_prompt_too_large",
    "stream_peer_mismatch": "ask_stream_peer_mismatch",
    "delivery_failed": "ask_delivery_failed",
    "unexpected_failure": "ask_unexpected_failure",
}


def instantiate_entry(
    entry: GuidanceEntry,
    *,
    db_path: str | Path | None = None,
    stream_id: str | None = None,
) -> GuidanceEntry:
    """Instantiate trusted absolute DB and stream argv from the actual execution context."""
    if not db_path and not stream_id:
        return entry
    abs_db = str(Path(db_path).resolve()) if db_path else None
    instantiated_recover: list[dict[str, Any]] = []
    for step in entry.recover:
        argv = list(step["argv"])
        if stream_id and "STREAM_ID" in argv:
            argv = [stream_id if arg == "STREAM_ID" else arg for arg in argv]
        if abs_db and argv and argv[0] == "peerhub" and "--db" not in argv:
            argv = [argv[0], "--db", abs_db, *argv[1:]]
        new_step = dict(step, argv=argv)
        if stream_id and "STREAM_ID" in step["argv"] and step.get("prereqs") == "Provide the affected stream identifier.":
            new_step["prereqs"] = "None."
        instantiated_recover.append(new_step)
    return GuidanceEntry(
        code=entry.code,
        meaning=entry.meaning,
        why=entry.why,
        recover=instantiated_recover,
        avoid=entry.avoid,
        docs=entry.docs,
        exits=entry.exits,
    )


def get_guidance(
    code: str,
    *,
    db_path: str | Path | None = None,
    stream_id: str | None = None,
) -> GuidanceEntry | None:
    """Retrieve guidance entry by code or alias, optionally instantiated with execution context."""
    canonical = ALIASES.get(code, code)
    entry = REGISTRY.get(canonical)
    if entry is None:
        return None
    return instantiate_entry(entry, db_path=db_path, stream_id=stream_id)


def _is_launch_failure_text(text: object) -> bool:
    if not isinstance(text, str):
        return False
    low = text.lower()
    return "executable not found" in low or "cannot spawn" in low


def _is_prompt_too_large_text(text: object) -> bool:
    return isinstance(text, str) and re.search(r"exceeding the \w+ inline limit", text) is not None


def select_guidance_code(
    *,
    status: str | None = None,
    processed_status: str | None = None,
    certainty: str | None = None,
    reason: str | None = None,
    detail_error: object = None,
    exception: BaseException | None = None,
) -> str:
    """Select guidance code using composite context (not error.code or message text)."""
    if exception is not None:
        from peerhub.extensions.bridge_claims import ClaimHeldError
        from peerhub.extensions.bridge import (
            PrespawnError,
            SessionBridgeError,
            ConflictingSessionFlagsError,
            InvalidSessionHandleSyntaxError,
        )
        from peerhub.extensions.ask import LegacySelectorUnsupported, StreamPeerMismatchError
        from peerhub.core.schema_version import SchemaVersionError
        from peerhub.core.store import CasMismatchError, IdempotencyConflictError, StorageReadOnlyError, StorageFullError, StorageCorruptError

        if isinstance(exception, SchemaVersionError):
            return "ask_schema_version_error"
        if isinstance(exception, CasMismatchError):
            return "ask_cas_conflict"
        if isinstance(exception, IdempotencyConflictError):
            return "ask_idempotency_conflict"
        if isinstance(exception, ClaimHeldError):
            return "ask_claim_held"
        if isinstance(exception, StreamPeerMismatchError):
            return "ask_stream_peer_mismatch"
        if isinstance(exception, (SessionBridgeError, LegacySelectorUnsupported, ConflictingSessionFlagsError, InvalidSessionHandleSyntaxError)):
            return "ask_selector_error"
        if isinstance(exception, PrespawnError):
            if _is_prompt_too_large_text(str(exception)):
                return "ask_prompt_too_large"
            return "ask_launch_failed" if _is_launch_failure_text(str(exception)) else "ask_delivery_failed"
        if isinstance(exception, (StorageReadOnlyError, StorageFullError, StorageCorruptError)):
            return "ask_delivery_failed"
        if isinstance(exception, ValueError) and "session" in type(exception).__name__.lower():
            return "ask_selector_error"
        return "ask_unexpected_failure"

    if status in ("delivered", "recovered_terminal"):
        return ""
    if status == "denied_empty" or (status == "pending_earlier_record" and processed_status == "denied_empty"):
        return "ask_denied_empty"
    if status == "pending_earlier_record":
        if processed_status in ("delivered", "recovered_terminal"):
            return ""
        if processed_status == "not_executed":
            return "ask_refusal"
        if processed_status == "failed_not_started":
            if _is_prompt_too_large_text(detail_error):
                return "ask_prompt_too_large"
            return "ask_launch_failed" if _is_launch_failure_text(detail_error) else "ask_delivery_failed"
        return "ask_blocked_uncertain"
    if status == "blocked_uncertain" or processed_status in ("uncertain", "blocked_uncertain"):
        return "ask_blocked_uncertain"
    if status == "uncertain" or (certainty in ("STARTED", "MAY_HAVE_STARTED") and status != "not_executed"):
        return "ask_uncertain"
    if status == "not_executed" or reason in ("quota_exhausted", "usage_limit", "invalid_model", "auth_failure", "refusal"):
        return "ask_refusal"
    if status == "session_unavailable":
        return "ask_selector_error"
    if status == "failed_not_started":
        if _is_prompt_too_large_text(detail_error):
            return "ask_prompt_too_large"
        if _is_launch_failure_text(detail_error):
            return "ask_launch_failed"
        return "ask_delivery_failed"
    return "ask_unexpected_failure"


def make_guidance_payload(
    code: str,
    *,
    db_path: str | Path | None = None,
    stream_id: str | None = None,
) -> dict[str, Any]:
    """Build additive top-level guidance dict for JSON responses."""
    entry = get_guidance(code, db_path=db_path, stream_id=stream_id)
    if entry is None:
        return {}
    return {
        "code": entry.code,
        "explain_cmd": f"peerhub explain {entry.code}",
        "summary": entry.summary(),
        "recover": entry.recover,
    }


def render_guidance_hint(entry: GuidanceEntry) -> str:
    """Format concise human stderr hint."""
    first_recover = entry.recover[0] if entry.recover else None
    action_text = f"{first_recover['purpose']} ({shlex.join(first_recover['argv'])})" if first_recover else "Inspect diagnostics."
    return f"{action_text}; {entry.avoid}; details: peerhub explain {entry.code}"


def render_exception_guidance_hint(code: str, exception: BaseException | None) -> str:
    """Keep exception recovery specific to the pre-spawn policy that rejected execution."""
    from peerhub.extensions.adapters.base import AgRequiresWritableError

    if code == "ask_delivery_failed" and isinstance(exception, AgRequiresWritableError):
        return (
            "add --writable to your command (ag has no read-only sandbox; it allows edits, "
            "so trusted workspaces only); details: peerhub explain ag-requires-writable"
        )
    if code == "ask_prompt_too_large":
        return render_guidance_hint(REGISTRY[code])
    return f"peerhub explain {code}"


def render_explain(entry: GuidanceEntry) -> str:
    """Format human output for peerhub explain CODE."""
    lines = [
        f"Guidance Code: {entry.code}",
        f"Meaning:       {entry.meaning}",
        f"Why:           {entry.why}",
    ]
    if entry.exit_code is not None:
        lines.append(f"Exit Code:     {entry.exit_code}")
    elif entry.exits:
        lines.append("Exit Codes:    Context-dependent:")
        for exit_info in entry.exits:
            lines.append(f"  - {exit_info['code']}: {exit_info['context']}")
    lines.extend([
        f"Avoid:         {entry.avoid}",
        "Recovery Steps:",
    ])
    for idx, step in enumerate(entry.recover, 1):
        rendered_cmd = shlex.join(step["argv"])
        lines.append(f"  {idx}. Command: {rendered_cmd}")
        lines.append(f"     Purpose: {step['purpose']}")
        if step.get("prereqs"):
            lines.append(f"     Prereqs: {step['prereqs']}")
    lines.append(f"Documentation: {entry.docs}")
    return "\n".join(lines)


_PROFILE_ID = re.compile(r"(?:ag|cc|cx)\.\S+")  # one token; the resolver decides known vs unknown


def _explain_profile(profile: str, *, json_output: bool) -> int:
    """Print one effective profile card (config only, current directory as workspace; no store)."""
    from peerhub.cli.support import error_line
    from peerhub.extensions.adapters.profiles import inspect_profile

    try:
        info = inspect_profile(profile, Path.cwd())
    except ValueError as exc:
        reason = "unknown profile" if "unknown model profile" in str(exc) else str(exc)
        if json_output:
            print(json.dumps({"error": f"{reason}: {profile}"}, indent=2, ensure_ascii=True))
        error_line("invalid_arguments", f"{reason}: {profile!r}", "peerhub profile list")
        return 1
    if json_output:
        print(json.dumps({"profile": info}, indent=2, ensure_ascii=True))
        return 0
    fields: dict[str, Any] = info["fields"]
    card: dict[str, dict[str, Any]] = info.get("card") or {}
    lines = [f"Profile: {profile} ({fields['model']['value'] or 'CLI default'} / {fields['effort']['value'] or 'provider default'})"]
    if not card:
        lines.append("No card (none packaged, or an overlay changed the model binding).")
    for key, item in card.items():
        value: object = item["value"]
        text = "; ".join(str(v) for v in cast("list[object]", value)) if isinstance(value, list) else str(value)
        lines.append(f"{key + ':':<14} {text}  [{item['source']}]")
    print("\n".join(lines))
    return 0


def run_explain_cli(code: str, *, json_output: bool = False) -> int:
    """Execute peerhub explain CODE [--json]. Offline; no store/provider/network."""
    entry = get_guidance(code)
    if entry is None and _PROFILE_ID.fullmatch(code.strip()):
        return _explain_profile(code.strip(), json_output=json_output)
    if entry is None:
        from peerhub.cli import lessons
        from peerhub.cli.lessons import render_lesson

        # Order: guidance code > exact profile id (above) > lessons index > exact lesson id > keywords.
        if code.strip().casefold() == "lessons":
            index = lessons.lesson_index()
            print(json.dumps(index, indent=2, ensure_ascii=True) if json_output
                  else "\n".join(f"{item['id']}: {item['title']}" for item in index))
            return 0
        matches = lessons.lesson_by_id(code) or lessons.lookup_lessons(code)
        if matches:
            if json_output:
                print(json.dumps({"lessons": matches}, indent=2, ensure_ascii=True))
            else:
                print("\n".join(render_lesson(lesson) for lesson in matches))
            return 0
        known = sorted(REGISTRY.keys())
        msg = f"No matching lessons for {code!r}. Try fewer keywords, 'peerhub explain lessons' (index) or 'peerhub guide'."
        if json_output:
            print(json.dumps({"error": f"Unknown guidance code: {code!r}", "known_codes": known}, indent=2, ensure_ascii=True))
        from peerhub.cli.support import error_line
        error_line("invalid_arguments", msg, "peerhub guide")
        return 1

    if json_output:
        print(json.dumps(entry.to_dict(), indent=2, ensure_ascii=True))
    else:
        print(render_explain(entry))
    return 0


def render_guidance_reference() -> str:
    """Generate the guidance and session lifecycle reference documentation."""
    lines: list[str] = [
        "# PeerHub Guidance & Session Reference",
        "",
        "Reference documentation for PeerHub CLI outcome status codes, recovery guidance, and session lifecycle states.",
        "",
        "## Execution Result Statuses",
        "",
        "The table below maps actual result statuses (`result[\"status\"]` returned by `peerhub ask`) to CLI exit codes, associated guidance codes, and safe recovery actions derived from `RESULT_STATUS_REGISTRY`.",
        "",
        "| Status | Exit Code | Guidance Code | Meaning | Safe Action |",
        "|---|---|---|---|---|",
    ]

    for status_key in sorted(RESULT_STATUS_REGISTRY.keys()):
        item = RESULT_STATUS_REGISTRY[status_key]
        clean_meaning = item.meaning.replace("|", "\\|")
        clean_action = item.safe_action.replace("|", "\\|")
        guidance_cell = f"`{item.guidance_code}`" if item.guidance_code != "(none)" else "(none)"
        lines.append(f"| `{item.status}` | {item.exit_code} | {guidance_cell} | {clean_meaning} | {clean_action} |")

    pending_entry = RESULT_STATUS_REGISTRY.get("pending_earlier_record")
    if pending_entry and pending_entry.processed_status_cases:
        lines.extend([
            "",
            "### `pending_earlier_record` Case Matrix",
            "",
            "When `peerhub ask` processes an earlier record instead of the requested prompt, it returns `pending_earlier_record` with exit 1. This does not by itself mean earlier work remains unfinished or uncertain: `processed_status` reports what happened to that earlier record during this cycle. The requested prompt remains queued. Guidance and safe recovery depend on that earlier outcome:",
            "",
            "| Earlier `processed_status` | Guidance Code | Meaning | Safe Action |",
            "|---|---|---|---|",
        ])
        for case in pending_entry.processed_status_cases:
            p_status = case["processed_status"]
            p_guidance = f"`{case['guidance_code']}`" if case["guidance_code"] != "(none)" else "(none)"
            clean_case_meaning = case["meaning"].replace("|", "\\|")
            clean_case_action = case["safe_action"].replace("|", "\\|")
            lines.append(f"| `{p_status}` | {p_guidance} | {clean_case_meaning} | {clean_case_action} |")

    lines.extend([
        "",
        "## Guidance Registry & Outcome Codes",
        "",
        "The table below is generated from the PeerHub guidance registry (`peerhub/cli/recovery.py`). A guidance code can cover several CLI exit codes; use the context lists below to interpret the actual process exit. Context-dependent explain JSON contains `exits` without a scalar `exit_code`.",
        "",
        "| Outcome Code | Exit Code | Meaning | Safe Recovery Action | Avoid |",
        "|---|---|---|---|---|",
    ])

    for code in sorted(REGISTRY.keys()):
        entry = REGISTRY[code]
        first_step = entry.recover[0] if entry.recover else {"argv": ["peerhub", "diag", "health"], "purpose": "Inspect health"}
        safe_action = f"`{shlex.join(first_step['argv'])}` ({first_step['purpose']})"
        clean_meaning = entry.meaning.replace("|", "\\|")
        clean_action = safe_action.replace("|", "\\|")
        clean_avoid = entry.avoid.replace("|", "\\|")
        if entry.exit_code is not None:
            exit_display = str(entry.exit_code)
        else:
            distinct_codes = sorted({int(e["code"]) for e in entry.exits})
            if len(distinct_codes) == 2:
                exit_display = f"{distinct_codes[0]} or {distinct_codes[1]} (context-dependent)"
            else:
                prefix = ", ".join(str(c) for c in distinct_codes[:-1])
                exit_display = f"{prefix}, or {distinct_codes[-1]} (context-dependent)"
        lines.append(f"| `{code}` | {exit_display} | {clean_meaning} | {clean_action} | {clean_avoid} |")

    lines.extend([
        "",
        "### Detailed Recovery Advice by Outcome Code",
        "",
    ])

    for code in sorted(REGISTRY.keys()):
        entry = REGISTRY[code]
        doc_rel = entry.docs[len("docs/"):] if entry.docs.startswith("docs/") else "../" + entry.docs
        lines.extend([
            f"#### `{code}`",
            "",
        ])
        if entry.exit_code is not None:
            lines.append(f"- **Exit Code**: {entry.exit_code}")
        else:
            lines.append("- **Exit Codes** (context-dependent):")
            for e in entry.exits:
                lines.append(f"  - `{e['code']}`: {e['context']}")
        lines.extend([
            f"- **Meaning**: {entry.meaning}",
            f"- **Why**: {entry.why}",
            "- **Safe Recovery Actions**:",
        ])
        for idx, step in enumerate(entry.recover, 1):
            rendered_cmd = shlex.join(step["argv"])
            prereq = f" (Prerequisites: {step['prereqs']})" if step.get("prereqs") else ""
            lines.append(f"  {idx}. `{rendered_cmd}`: {step['purpose']}{prereq}")
        lines.extend([
            f"- **Avoid**: {entry.avoid}",
            f"- **Documentation**: [{entry.docs}]({doc_rel})",
            "",
        ])

    lines.extend([
        "## Session Lifecycle & State Definitions",
        "",
        "PeerHub manages durable session continuity for AI peer adapters (`cc`, `cx`, `ag`) via retained sessions in SQLite storage (`bridge_session_history`, `bridge_sessions`).",
        "",
        "### Session States",
        "",
        "- **`ACTIVE`**: The session record is in the active lifecycle state. Note that having `state = 'ACTIVE'` alone does not guarantee immediate turn reuse or resumption eligibility; actual eligibility also requires meeting retention age bounds (`0 <= now - last_seen <= MAX_AGE_S`), `resumable = 1`, bootstrap integrity, and valid native vendor session evidence (non-null `vendor_session_id` and `context_watermark`). A session remains `ACTIVE` across successful subsequent turns on the same stream, peer, and compatible binding.",
        "- **`FRESH`**: A newly allocated session generation that has been created but has not yet completed a successful native vendor execution.",
        "- **`LOST`**: Session continuity has been broken or tainted. This occurs when the adapter reports context loss, when a prior execution ends in an uncertain state (e.g. timeout or abort), or when a session resumption is rejected. A `LOST` session cannot be resumed. Note that marking a session `LOST` does not mean the next request automatically starts a new generation: an uncertain delivery can block the stream with `ask_blocked_uncertain`, and session selection during recovery can select another compatible retained session from history if available.",
        "- **`TOMBSTONED`**: The session has been permanently retired because it exceeded the maximum retention age (`MAX_AGE_S` = 30 days) or the per-stream history capacity (`MAX_RETAINED` = 20 sessions). Tombstoned sessions cannot be resumed or resurrected.",
        "",
        "### Resumability (`resumable`)",
        "",
        "- **`resumable`**: A boolean flag (`0` or `1`) indicating whether the adapter and runtime target support multi-turn session resumption. CLI peers with native session continuation support (`cc`, `cx`, `ag`) operate with `resumable = 1`. If an adapter is not resumable, every turn executes in a fresh generation.",
        "",
        "### Generation Semantics (`session_generation`)",
        "",
        "The session generation counter (`session_generation`) tracks distinct conversation lineages within a stream and peer:",
        "",
        "- **Generation Stays Constant (`gen = 1`)**: Within a retained session, the generation remains constant (e.g. `1`) across turns as long as the caller continues on the same stream and peer with compatible options. Subsequent asks on that stream automatically reuse the warm session without bumping the generation.",
        "- **Generation Bumps (`gen -> gen + 1`)**: PeerHub allocates a new generation (incrementing `session_generation` via `bridge_session_counters`) in any of the following circumstances:",
        "  1. **Binding Change**: When the effective runtime binding changes. For example, in Antigravity (`ag`), switching between read-only and `--writable` changes the CLI execution mode (`--mode accept-edits`), modifying the binding and bumping the generation.",
        "  2. **No Eligible Compatible Retained Session**: When a prior session was marked `LOST` (e.g. context loss or resumption rejection) or tombstoned, and no other compatible retained session is eligible in history, the next eligible turn on an unblocked stream allocates a new generation. (Note: an uncertain delivery blocks subsequent turns on the stream with `ask_blocked_uncertain` rather than automatically bumping generations, and recovery can select another compatible retained session if available.)",
        "  3. **Explicit `--fresh`**: When the caller explicitly requests `--fresh`, forcing a new session generation.",
        "  4. **Expiration or Tombstoning**: When the retained session exceeds 30 days or is pruned due to quota.",
        "  5. **Workspace or Fingerprint Mismatch**: When the workspace lineage or adapter binary fingerprint differs from the retained session.",
        "  6. **Context Delta Overflow**: When the catch-up delta exceeds the context window budget (`delta_too_large`).",
        "",
    ])

    return "\n".join(lines)

