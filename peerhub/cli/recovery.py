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

from dataclasses import dataclass, field
from typing import Any


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
        safe_action="Treat the reply as unusable; for ag, denied_actions names the denied tools, not the path or command (causes: `peerhub explain ag-recipe`). Do not resume the stream: start a new stream with the needed content inlined via --query-file, and verify the workspace.",
    ),
    "failed_not_started": ResultStatusEntry(
        status="failed_not_started",
        exit_code=1,
        guidance_code="ask_prompt_too_large / ask_launch_failed / ask_delivery_failed",
        meaning="Provider execution failed before starting (prompt limit, missing executable, OS spawn rejection or pre-spawn policy check).",
        safe_action="For ask_prompt_too_large, split the prompt under the limit, or for an ag argv limit (an agy without stream-json input, or PEERHUB_AG_TRANSPORT=argv) use cc/cx on a separate stream, with a new request-id; otherwise inspect peer adapter availability and PATH configuration via `peerhub diag health`.",
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
                "safe_action": "For ask_prompt_too_large, split the prompt under the limit, or for an ag argv limit (an agy without stream-json input, or PEERHUB_AG_TRANSPORT=argv) use cc/cx on a separate stream, with a new request-id; otherwise inspect provider binary and PATH configuration via peerhub diag health.",
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
    "truncated": ResultStatusEntry(
        status="truncated",
        exit_code=1,
        guidance_code="truncated",
        meaning="An API peer (ca, oa, ga) reply was cut at its output limit (16,000 tokens for ca and oa; 32,000 for ga); the ask is not delivered, and the partial text is in partial_response.",
        safe_action="Ask again on a new stream with a narrower question or a request for a shorter answer; a ca, oa or ga stream takes one ask only.",
    ),
    "incomplete": ResultStatusEntry(
        status="incomplete",
        exit_code=1,
        guidance_code="incomplete",
        meaning="An API peer (ca, oa, ga) reply ended for a reason other than the token limit (for example a refusal); the ask is not delivered, and any text is in partial_response.",
        safe_action="Read the reason in the error message; rephrase, or use another profile or peer, on a new stream. Do not count the partial text as a reply.",
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
        why="Every provider has a 1,000,000-byte inline maximum and receives the prompt on stdin. Only ag on the argv transport (an agy without stream-json input, or PEERHUB_AG_TRANSPORT=argv) has a lower limit: 30,000 bytes on Windows, 120,000 bytes on POSIX. The byte count includes injected catch-up history.",
        recover=[{
            "argv": ["peerhub", "ask", "cc", "--query-file", "CONTENT_FILE", "--stream", "NEW_STREAM_ID", "--request-id", "NEW_REQUEST_ID"],
            "purpose": "split the prompt into parts under the limit (one ask each); for an ag argv limit (old agy or PEERHUB_AG_TRANSPORT=argv), send it to cc/cx instead (stdin, 1,000,000-byte maximum) on a separate stream, since a stream belongs to one peer; if the prompt itself is under the limit, catch-up history pushed it over, so start a new --stream; a changed prompt needs a new request-id",
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
        why="The provider denied tool action(s) (headless cannot prompt), so the turn can end with no reply text. Task effects are unverified. For ag, denied_actions reports only the tool names (for example command or read_file): the path or command of a denied action is intentionally not read, because a command line can hold secrets, so PeerHub cannot show it. For cc, each entry is `Tool: detail`, or just `Tool` when the detail is absent; secrets are redacted before the detail is cut to 120 characters, and `...[truncated]` is appended when it is cut. For ag, find the cause with the order of causes in `peerhub explain ag-recipe`: the files named in the prompt, a -w outside trustedWorkspaces, binary or huge files, allow-list scope, quota.",
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
            {"code": 2, "context": "Invalid CLI arguments or unrecognized ask options (ValueError other than selector/session errors and an unregistered peer)."},
        ],
    ),
    # API peers ca, oa and ga (#378, #381): one API call per ask, no tools, no session.
    "truncated": GuidanceEntry(
        code="truncated",
        meaning="The reply of an API peer (ca, oa, ga) was cut at its output limit (16,000 tokens for ca and oa; 32,000 for ga); the ask is not delivered.",
        why="The provider stopped at the output limit (ca stop_reason max_tokens; oa status incomplete with reason max_output_tokens; ga finishReason MAX_TOKENS, where thinking counts against the limit, so the cut text can be empty). The call ran and spent API credits; the cut text is kept in partial_response (--json) and is not a response Record.",
        recover=[{
            "argv": ["peerhub", "ask", "PEER_ID", "--query-file", "CONTENT_FILE", "--stream", "NEW_STREAM_ID", "--profile", "PROFILE"],
            "purpose": "Ask again on a new stream with a narrower question or a request for a shorter answer, the text inlined",
            "prereqs": "A ca, oa or ga stream takes one ask only; read partial_response first to see where the reply stopped.",
        }],
        avoid="Do not treat the cut text as a complete reply, and do not ask again on the same stream (it is refused: one ask per stream).",
        docs="docs/adapters/README.md",
        exits=[{"code": 1, "context": "API peer reply cut at the output token limit (truncated result status)."}],
    ),
    "incomplete": GuidanceEntry(
        code="incomplete",
        meaning="The reply of an API peer (ca, oa, ga) ended for a reason other than the token limit; the ask is not delivered.",
        why="The provider ended the reply without completing it (for example ca stop_reason refusal, an oa status other than completed, or a ga finishReason other than STOP or a blocked prompt). The call ran and spent API credits; the error message names the reason, and any text is kept in partial_response (--json).",
        recover=[{
            "argv": ["peerhub", "ask", "PEER_ID", "--query-file", "CONTENT_FILE", "--stream", "NEW_STREAM_ID", "--profile", "PROFILE"],
            "purpose": "Read the reason, then rephrase or use another profile or peer on a new stream",
            "prereqs": "A ca, oa or ga stream takes one ask only.",
        }],
        avoid="Do not count the partial text as a reply (for example as a vote), and do not ask again on the same stream.",
        docs="docs/adapters/README.md",
        exits=[{"code": 1, "context": "API peer reply ended without completing (incomplete result status)."}],
    ),
    "request_rejected": GuidanceEntry(
        code="request_rejected",
        meaning="An ask to an API peer (ca, oa, ga) was refused before anything was stored or sent.",
        why="ca, oa and ga take a model that matches their pattern (from --profile or --model), no --effort and no --writable, and one ask per stream; this request broke one of those rules. The message names the rule.",
        recover=[{
            "argv": ["peerhub", "explain", "PROFILE"],
            "purpose": "Correct the command named in the message: a ca/oa/ga profile or a valid --model, no --effort, no --writable, a new stream for a new ask",
            "prereqs": "PROFILE is ca.standard, ca.effort, ca.deepthink, oa.standard, oa.effort, oa.deepthink, ga.standard or ga.effort.",
        }],
        avoid="Do not retry the same command unchanged; nothing was sent, so no provider work needs recovery.",
        docs="docs/adapters/README.md",
        exits=[{"code": 2, "context": "API peer request refused before anything was stored or sent (RequestRejected)."}],
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
    ("peer_not_found", 1, "The selected peer does not exist in this store, or ask was given a peer with no adapter (register it with --adapter).", ["peerhub", "peer", "list"]),
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
