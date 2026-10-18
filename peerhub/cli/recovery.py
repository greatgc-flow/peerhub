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
import shlex
import sys
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GuidanceEntry:
    code: str
    meaning: str
    why: str
    recover: list[dict[str, Any]]
    avoid: str
    docs: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "meaning": self.meaning,
            "why": self.why,
            "recover": self.recover,
            "avoid": self.avoid,
            "docs": self.docs,
        }

    def summary(self) -> str:
        next_action = self.recover[0]["purpose"] if self.recover else "Inspect diagnostics"
        return f"{self.meaning} Next: {next_action}. {self.avoid}"


REGISTRY: dict[str, GuidanceEntry] = {
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
        avoid="Do not retry the prompt under a new request-id, do not pass --fresh, and do not delete claims. Continuing work on this stream is blocked to prevent split-brain execution; start a new stream for subsequent work.",
        docs="docs/remote-ask.md",
    ),
    "ask_blocked_uncertain": GuidanceEntry(
        code="ask_blocked_uncertain",
        meaning="New request on this stream is blocked because an earlier record on the stream ended in an uncertain state.",
        why="Stream ordering and exactly-once execution require resolving or abandoning uncertain prior work before appending new turns. Re-issuing the exact same in-flight request with its original --request-id, prompt, and binding inspects recovery status without creating a duplicate turn.",
        recover=[
            {
                "argv": ["peerhub", "diag", "health", "--stream", "STREAM_ID"],
                "purpose": "Inspect the blocked stream and review uncommitted evidence",
                "prereqs": "Provide the affected stream identifier.",
            },
        ],
        avoid="Do not retry the prompt under a new request-id, do not pass --fresh, and do not delete claims. Abandon the blocked stream and continue genuinely new work on a new stream with a summary of prior context.",
        docs="docs/remote-ask.md",
    ),
    "ask_claim_held": GuidanceEntry(
        code="ask_claim_held",
        meaning="A concurrent ask operation or active lease currently holds the delivery claim for this peer and stream.",
        why="Only one caller can execute an ask turn per peer and stream at a time to prevent concurrent dispatch races. Wait for the active turn to finish, or use a distinct --stream for genuinely separate parallel work.",
        recover=[
            {
                "argv": ["peerhub", "diag", "health", "--stream", "STREAM_ID"],
                "purpose": "Check active stream lease and claim status",
                "prereqs": "Wait for the active turn or lease timeout to expire.",
            },
        ],
        avoid="Do not delete claim tables or database files, do not force --fresh, and do not bypass claim contention with duplicate request IDs.",
        docs="docs/design/a2a-inbound-server.md",
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
        docs="docs/history/from-engram-repo/protocol-docs/ops/automatic-profile-routing-2026-06-20.md",
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
        avoid="Do not loop blind retries or pass --fresh without verifying adapter flags and availability.",
        docs="docs/remote-ask.md",
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
    ),
}

ALIASES: dict[str, str] = {
    "uncertain": "ask_uncertain",
    "blocked_uncertain": "ask_blocked_uncertain",
    "pending_earlier_record": "ask_blocked_uncertain",
    "claim_held": "ask_claim_held",
    "selector_error": "ask_selector_error",
    "refusal": "ask_refusal",
    "launch_failed": "ask_launch_failed",
    "stream_peer_mismatch": "ask_stream_peer_mismatch",
    "delivery_failed": "ask_delivery_failed",
    "unexpected_failure": "ask_unexpected_failure",
}


def get_guidance(code: str) -> GuidanceEntry | None:
    """Retrieve guidance entry by code or alias."""
    canonical = ALIASES.get(code, code)
    return REGISTRY.get(canonical)


def _is_launch_failure_text(text: object) -> bool:
    if not isinstance(text, str):
        return False
    low = text.lower()
    return "executable not found" in low or "cannot spawn" in low


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
        from peerhub.core.store import StorageReadOnlyError, StorageFullError, StorageCorruptError

        if isinstance(exception, ClaimHeldError):
            return "ask_claim_held"
        if isinstance(exception, StreamPeerMismatchError):
            return "ask_stream_peer_mismatch"
        if isinstance(exception, (SessionBridgeError, LegacySelectorUnsupported, ConflictingSessionFlagsError, InvalidSessionHandleSyntaxError)):
            return "ask_selector_error"
        if isinstance(exception, PrespawnError):
            return "ask_launch_failed" if _is_launch_failure_text(str(exception)) else "ask_delivery_failed"
        if isinstance(exception, (StorageReadOnlyError, StorageFullError, StorageCorruptError)):
            return "ask_delivery_failed"
        if isinstance(exception, ValueError) and "session" in type(exception).__name__.lower():
            return "ask_selector_error"
        return "ask_unexpected_failure"

    if status in ("delivered", "recovered_terminal"):
        return ""
    if status == "pending_earlier_record":
        if processed_status in ("delivered", "recovered_terminal"):
            return ""
        if processed_status == "not_executed":
            return "ask_refusal"
        if processed_status == "failed_not_started":
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
        if _is_launch_failure_text(detail_error):
            return "ask_launch_failed"
        return "ask_delivery_failed"
    return "ask_unexpected_failure"


def make_guidance_payload(code: str) -> dict[str, str]:
    """Build additive top-level guidance dict for JSON responses."""
    entry = get_guidance(code)
    if entry is None:
        return {}
    return {
        "code": entry.code,
        "explain_cmd": f"peerhub explain {entry.code}",
        "summary": entry.summary(),
    }


def render_guidance_hint(entry: GuidanceEntry) -> str:
    """Format concise human stderr hint."""
    first_recover = entry.recover[0] if entry.recover else None
    action_text = f"{first_recover['purpose']} ({shlex.join(first_recover['argv'])})" if first_recover else "Inspect diagnostics."
    return (
        f"Guidance [{entry.code}]:\n"
        f"  What happened:  {entry.meaning}\n"
        f"  Next action:    {action_text}\n"
        f"  What NOT to do: {entry.avoid}\n"
        f"  Details:        peerhub explain {entry.code}"
    )


def render_explain(entry: GuidanceEntry) -> str:
    """Format human output for peerhub explain CODE."""
    lines = [
        f"Guidance Code: {entry.code}",
        f"Meaning:       {entry.meaning}",
        f"Why:           {entry.why}",
        f"Avoid:         {entry.avoid}",
        "Recovery Steps:",
    ]
    for idx, step in enumerate(entry.recover, 1):
        rendered_cmd = shlex.join(step["argv"])
        lines.append(f"  {idx}. Command: {rendered_cmd}")
        lines.append(f"     Purpose: {step['purpose']}")
        if step.get("prereqs"):
            lines.append(f"     Prereqs: {step['prereqs']}")
    lines.append(f"Documentation: {entry.docs}")
    return "\n".join(lines)


def run_explain_cli(code: str, *, json_output: bool = False) -> int:
    """Execute peerhub explain CODE [--json]. Offline; no store/provider/network."""
    entry = get_guidance(code)
    if entry is None:
        known = sorted(REGISTRY.keys())
        msg = f"Unknown guidance code: {code!r}. Known codes: {', '.join(known)}"
        if json_output:
            print(json.dumps({"error": f"Unknown guidance code: {code!r}", "known_codes": known}, indent=2, ensure_ascii=True), file=sys.stderr)
        else:
            print(msg, file=sys.stderr)
        return 1

    if json_output:
        print(json.dumps(entry.to_dict(), indent=2, ensure_ascii=True))
    else:
        print(render_explain(entry))
    return 0
