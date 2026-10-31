"""PeerHub Quick Reference and Best Practices Guide (S5).

Static prose and generated profile table providing a concise (<=60 lines, words * 1.3 <= 900 tokens)
reference for session reuse, efficiency, safety, discovery, drift recovery, and issue reporting.
Works without store or provider access.

Token-count method:
The guide output is capped at <=60 lines and an estimated token budget of words * 1.3 <= 900 tokens.
The budget is asserted by evaluating whitespace-delimited word count with standard ~1.3 tokens/word
heuristics (int(len(out.split()) * 1.3) <= 900).
"""
from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any, cast


def _cell(value: object, width: int) -> str:
    """Sanitize and bound untrusted profile metadata for terminal presentation.

    The source model/profile is never modified; use 'peerhub profile list'
    to inspect a truncated value. Limit visual width (including CJK) as well
    as printable characters, so the quick guide cannot grow with config size.
    """
    chars: list[str] = []
    used = 0
    source = str(value)
    for raw in source:
        category = unicodedata.category(raw)
        char = "?" if category.startswith("C") else raw
        cells = 0 if unicodedata.combining(char) else (
            2 if unicodedata.east_asian_width(char) in ("F", "W") else 1
        )
        if used + cells > width:
            # Retain an explicit ellipsis without exceeding terminal width.
            while chars and used > width - 1:
                last = chars.pop()
                used -= 0 if unicodedata.combining(last) else (
                    2 if unicodedata.east_asian_width(last) in ("F", "W") else 1
                )
            chars.append("~")
            used += 1
            break
        chars.append(char)
        used += cells
    return "".join(chars) + " " * max(0, width - used)


PROFILE_ROWS = 10  # keeps the guide within its 60-line cap however many profiles are configured


def render_guide(workspace: Path | None = None) -> str:
    """Render the <=60 line quick reference guide."""
    from peerhub.extensions.adapters.profiles import list_profiles

    ws = workspace or Path(".")
    load_error: str | None = None
    try:
        profs = list_profiles(ws)
    except Exception as exc:
        # Raw parser errors may echo user-controlled profile data or paths.
        load_error = f"{type(exc).__name__} (details: peerhub profile list)"
        profs = []

    tier_order = {"standard": 0, "effort": 1, "deepthink": 2}

    def _tier_key(p: dict[str, Any]) -> tuple[int, str]:
        name = str(p.get("profile") or "")
        suffix = name.split(".")[-1] if "." in name else name
        return (tier_order.get(suffix, 9), name)

    def _layer_key(p: dict[str, Any]) -> int:
        raw_fields: object = p.get("fields")
        fields: dict[str, Any] = cast(dict[str, Any], raw_fields) if isinstance(raw_fields, dict) else {}
        raw: object = fields.get("model")
        src = str(cast(dict[str, Any], raw).get("source") or "") if isinstance(raw, dict) else ""
        return 0 if src == "packaged" else 1

    sorted_profs = sorted(profs, key=lambda p: (_layer_key(p), *_tier_key(p)))
    hidden = max(0, len(sorted_profs) - PROFILE_ROWS)
    if hidden:
        sorted_profs = sorted_profs[:PROFILE_ROWS - 1]
        hidden += 1
    lines: list[str] = [
        "PeerHub Quick Reference & Best Practices",
        "Profiles (defaulting to .standard first):",
        f"{'Profile':<14} {'Model':<24} {'Pools':<14} {'Layer'}",
    ]
    if load_error:
        lines.append(f"  (error loading profiles: {load_error})")
    for p in sorted_profs:
        raw_fields: object = p.get("fields")
        fields: dict[str, Any] = cast(dict[str, Any], raw_fields) if isinstance(raw_fields, dict) else {}
        p_name = str(p.get("profile") or "")
        raw_model: object = fields.get("model")
        model_dict: dict[str, Any] = cast(dict[str, Any], raw_model) if isinstance(raw_model, dict) else {}
        mod = str(model_dict.get("value") or "")
        src = str(model_dict.get("source") or "")
        raw_pools: object = fields.get("pools")
        pools_dict: dict[str, Any] = cast(dict[str, Any], raw_pools) if isinstance(raw_pools, dict) else {}
        pools_val: object = pools_dict.get("value")
        pools_list = cast(list[object], pools_val) if isinstance(pools_val, list) else []
        # Don't materialize an unbounded joined string from a custom list.
        pools_str = ", ".join(str(x)[:32] for x in pools_list[:3])
        if len(pools_list) > 3:
            pools_str += ", ..."
        lines.append(
            f"{_cell(p_name, 14)} {_cell(mod, 24)} "
            f"{_cell(pools_str, 14)} {_cell(src, 12).rstrip()}"
        )
    if hidden:
        lines.append(f"  +{hidden} more profiles: peerhub profile list")

    lines.extend([
        "Two-Asks Session Reuse Example:",
        '  peerhub ask cc "Explain the architecture" --profile cc.standard --stream arch-review --request-id req-001',
        '  peerhub ask cc "Now drill into data flow" --profile cc.standard --stream arch-review --request-id req-002',
        "",
        "Efficiency:",
        "  Reuse: asks automatically reuse sessions; keep one --stream per peer/role with compatible store/binding (single-peer stream).",
        "  Profiles: start with *.standard as an economical baseline (not guaranteed cheapest/fastest).",
        "  Transport: --query-file and --json select transport/parsing format (no inherent token saving).",
        "",
        "Long Asks:",
        "  'peerhub ask' blocks until the peer finishes and then prints reply + 'session:' line;",
        "  run foreground/background-with-exit-notification and wait for exit, never detach and poll;",
        "  ag prints nothing until done: for ag leave --silence-timeout-seconds unset or",
        "  set it >= --timeout-seconds (long tasks: --timeout-seconds 7200); uncertain/timeout => diag.",
        "",
        "Safety:",
        "  Writes: cx sandbox enforced; cc enforced; ag NOT enforced (issue #140); when edits are intended, pass --writable on every turn, including resumed turns.",
        "    PeerHub requires --writable for ag even for read tasks; it maps to accept-edits (workspace-wide edit approval).",
        "    ag -w must be the trusted project folder, not temp/scratch; direct agy reads need no accept-edits.",
        "    ag headless prompts auto-deny; shell needs matching command(...) or command(*) allow rules; details: 'peerhub explain ag silence'.",
        "  pending_earlier_record: inspect processed_status; delivered => retry same request-id/prompt/binding/selector; uncertain => stop.",
        "  Uncertain: stop that stream, 'peerhub diag health' to check effects;",
        "    never blind-retry original prompt under a new request-id, with --fresh, or delete claims;",
        "    Default-timeout ask: do not retry on the same stream; start a new stream with explicit --timeout-seconds (long reviews: 1800 or more); continue genuinely new work on a new stream with a summary; details: 'peerhub explain <code>'.",
        "  Store hazards: back up core.db before upgrading; new schema not openable by old versions;",
        "    stop 'peerhub monitor' -> pip install -U peerhub -> restart monitor (running monitor locks peerhub.exe on Windows; also wait for running asks, else a broken install: reinstall, check results before re-sending);",
        "    don't run dev builds on the real store; after restore, reopen stores (old instances are fenced).",
        "  Cost side effects: monitor watch / observation refresh may spend quota (snapshot/diag read-only); uncertain streams may remain quota-blocked after reset.",
        "  Batch: executes asks (spends quota); ag tasks always writable; overlapping writers rejected.",
        "    Cancel: Windows tree termination; POSIX best-effort. Inspect UNCERTAIN; keep manifest + batch-id for resume.",
        "  Accounts: cc/cx only; new peer+root per login; protected roots; overrides scrubbed (names retained for failures/replay); bound quota unobserved; backups contain roots.",
        "  Secrets: prompts/streams persist in local store; no tokens/passwords.",
        "Discovery & Inspection Commands:",
        "  peerhub explain ag silence   # look up operational lessons",
        "  peerhub monitor              # live dashboard: all quota pools, session lineage, facts",
        "  peerhub monitor --expand     # expand sessions/usage/context/runtime rows (or with --snapshot)",
        "  peerhub diag                 # read-only diagnostics across all peers",
        "  models: peerhub profile list; slugs: agy models; --model <slug>",
        "  peerhub batch run tasks.json --batch-id review-001 --max-parallel 2 --dry-run  # validate before execution",
        "  peerhub session list         # retained sessions; accounts: peerhub account list",
        "  peerhub observation refresh  # re-probe provider quota observations",
        "",
        "Model Facts & CLI Drift Recovery:",
        "  'Facts: needs_recheck' clears via refresh-model-profiles skill or release upgrade.",
        "Report Issues:",
        "  File at github.com/greatgc-flow/peerhub/issues after checking 'gh issue list -R greatgc-flow/peerhub --search KEYWORD'.",
        "  Include peerhub --version, OS, exact command, status/exit code, and workaround (no secrets/private paths).",
    ])
    return "\n".join(lines)

