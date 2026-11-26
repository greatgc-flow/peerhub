"""Prepare recovery guidance for CLI text and JSON."""
from __future__ import annotations

import shlex
from dataclasses import replace
from pathlib import Path
from typing import Any

from peerhub.cli.recovery import REGISTRY, GuidanceEntry


# Result labels emitted by PeerHub can be explained directly.
_RESULT_GUIDANCE_CODES: dict[str, str] = {
    "uncertain": "ask_uncertain",
    "blocked_uncertain": "ask_blocked_uncertain",
    "pending_earlier_record": "ask_blocked_uncertain",
    "refusal": "ask_refusal",
    "stream_peer_mismatch": "ask_stream_peer_mismatch",
    "delivery_failed": "ask_delivery_failed",
}


def _instantiate_entry(
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
    ag_command_denied: bool = False,
) -> GuidanceEntry | None:
    """Retrieve guidance entry by guidance code or result label, optionally instantiated with execution context."""
    canonical = _RESULT_GUIDANCE_CODES.get(code, code)
    entry = REGISTRY.get(canonical)
    if entry is None:
        return None
    if ag_command_denied and canonical == "ask_denied_empty":
        step = entry.recover[0]
        purpose = (f"{step['purpose']}. If the task needs file reads, omit shell fallbacks and say "
                   '"use only your file read tool; if a file cannot be read, say so"')
        entry = replace(entry, recover=[dict(step, purpose=purpose), *entry.recover[1:]])
    return _instantiate_entry(entry, db_path=db_path, stream_id=stream_id)


def make_guidance_payload(
    code: str,
    *,
    db_path: str | Path | None = None,
    stream_id: str | None = None,
    ag_command_denied: bool = False,
) -> dict[str, Any]:
    """Build additive top-level guidance dict for JSON responses."""
    entry = get_guidance(code, db_path=db_path, stream_id=stream_id, ag_command_denied=ag_command_denied)
    if entry is None:
        return {}
    first_recover = entry.recover[0] if entry.recover else None
    next_action = first_recover["purpose"] if first_recover else "Inspect diagnostics"
    action = f"Next: {next_action} ({shlex.join(first_recover['argv'])})" if first_recover else next_action
    # Preserve the structured first action consumed by existing scripts. Detailed
    # alternate steps remain available from `peerhub explain CODE`.
    return {
        "code": entry.code,
        "explain_cmd": f"peerhub explain {entry.code}",
        "summary": f"{action}. {entry.avoid}",
        "recover": [first_recover] if first_recover else [],
    }


def render_guidance_hint(entry: GuidanceEntry) -> str:
    """Format concise human stderr hint."""
    first_recover = entry.recover[0] if entry.recover else None
    action_text = f"{first_recover['purpose']} ({shlex.join(first_recover['argv'])})" if first_recover else "Inspect diagnostics."
    retry = " Do not retry this stream." if "Do not retry" in entry.avoid else ""
    return f"{action_text}.{retry} details: peerhub explain {entry.code}"


def render_exception_guidance_hint(code: str, exception: BaseException | None) -> str:
    """Keep exception recovery specific to the pre-spawn policy that rejected execution."""
    from peerhub.extensions.recovery_classification import is_ag_writable_error

    if code == "ask_delivery_failed" and is_ag_writable_error(exception):
        return (
            "add --writable to your command (ag has no read-only sandbox; it allows edits, "
            "so trusted workspaces only); details: peerhub explain ag-requires-writable"
        )
    if code == "ask_prompt_too_large":
        return render_guidance_hint(REGISTRY[code])
    return f"peerhub explain {code}"
