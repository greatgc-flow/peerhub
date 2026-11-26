"""Explain command routing and presentation for guidance, profiles, and lessons."""
from __future__ import annotations

import argparse
import json
import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from peerhub.cli.recovery import REGISTRY, RESULT_STATUS_REGISTRY, GuidanceEntry
from peerhub.cli.recovery_render import get_guidance
from peerhub.core.peer_kinds import BUILTIN_PEER_KINDS

if TYPE_CHECKING:
    from peerhub.cli.arguments import Subparsers


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


# Match one token here and let the resolver decide whether the profile is known.
_PROFILE_ID = re.compile(rf"(?:{'|'.join(map(re.escape, BUILTIN_PEER_KINDS))})\.\S+")


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

        # Order: guidance code > exact profile id (above) > lessons index > lesson id or alias > keywords.
        if code.strip().casefold() in {"", "lessons", "list"}:
            index = lessons.lesson_index()
            print(json.dumps({"lessons": index}, indent=2, ensure_ascii=True) if json_output
                  else "\n".join(f"{item['id']}: {item['title']}" for item in index))
            return 0
        exact_lesson = lessons.lesson_by_id(code)
        matches = exact_lesson or lessons.lookup_lessons(code)
        if matches:
            if json_output:
                if exact_lesson:
                    print(json.dumps({"lessons": matches}, indent=2, ensure_ascii=True))
                else:
                    print(json.dumps({"lessons": [{"id": lesson["id"], "title": lesson["title"]}
                                                   for lesson in matches]}, indent=2, ensure_ascii=True))
            else:
                # One match: the full text. Several: one `id: title` line each (an AI pays only for what it opens).
                print(render_lesson(matches[0]) if len(matches) == 1 else
                      "\n".join([*(f"{lesson['id']}: {lesson['title']}" for lesson in matches),
                                 "peerhub explain <id> shows one in full"]))
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


def register_explain_parser(subparsers: Subparsers) -> None:
    explain_parser = subparsers.add_parser("explain", help="Explain outcome codes or look up operational lessons")
    explain_parser.add_argument("code", nargs="*", help="Guidance code, profile, lesson ID or alias, or keywords. Omit it or use lessons (also list) for the index.")
    explain_parser.add_argument("--json", action="store_true", default=False)


def run_explain(args: argparse.Namespace) -> int:
    return run_explain_cli(" ".join(args.code), json_output=bool(getattr(args, "json", False)))
