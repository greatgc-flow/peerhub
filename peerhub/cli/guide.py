"""PeerHub Quick Reference and Best Practices Guide (S5).

Static prose and generated profile table providing a concise (<60 lines, ~900 tokens)
reference for session reuse, delivery safety, discovery, and drift recovery.
Works without store or provider access.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, cast


def render_guide(workspace: Path | None = None) -> str:
    """Render the <=60 line quick reference guide."""
    from peerhub.extensions.adapters.profiles import list_profiles

    ws = workspace or Path(".")
    load_error: str | None = None
    try:
        profs = list_profiles(ws)
    except Exception as exc:
        load_error = f"{type(exc).__name__}: {exc}"
        profs = []

    tier_order = {"standard": 0, "effort": 1, "deepthink": 2}

    def _tier_key(p: dict[str, Any]) -> tuple[int, str]:
        name = str(p.get("profile") or "")
        suffix = name.split(".")[-1] if "." in name else name
        return (tier_order.get(suffix, 9), name)

    sorted_profs = sorted(profs, key=_tier_key)
    lines: list[str] = [
        "PeerHub Quick Reference & Best Practices",
        "========================================",
        "",
        "Profiles (defaulting to .standard first):",
        f"{'Profile':<14} {'Model':<24} {'Pools':<14} {'Layer'}",
        "-" * 64,
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
        pools_str = ", ".join(str(x) for x in pools_list)
        lines.append(f"{p_name:<14} {mod:<24} {pools_str:<14} {src}")

    lines.extend([
        "",
        "Two-Asks Session Reuse Example:",
        '  peerhub ask cc "Explain the architecture" --profile cc.standard --stream arch-review --request-id req-001',
        '  peerhub ask cc "Now drill into data flow" --profile cc.standard --stream arch-review --request-id req-002',
        "  (Subsequent asks on the same stream reuse the warm session automatically)",
        "  (--writable preserves workspace-write on resume; cx enforces read-only sandbox by default;",
        "   cc/ag read-only is provider-controlled without a peerhub-enforced sandbox flag)",
        "",
        "Long Asks:",
        "  'peerhub ask' blocks until the peer finishes and then prints reply + 'session:' line,",
        "  so run it as a foreground/background-with-exit-notification process and wait for its exit;",
        "  never detach it and poll output files; use --timeout-seconds/--silence-timeout-seconds;",
        "  uncertain/timeout => diag before retry.",
        "",
        "Delivery Safety & Uncertainty:",
        "  Never blindly retry when execution or delivery is uncertain (timeout, disconnect).",
        "  Check 'peerhub monitor' or 'peerhub diag' before dispatching a fresh attempt.",
        "",
        "Discovery & Inspection Commands:",
        "  peerhub monitor              # live dashboard: all quota pools, session lineage, facts",
        "  peerhub monitor --expand     # expand sessions/usage/context/runtime rows (or with --snapshot)",
        "  peerhub diag                 # read-only diagnostics across all peers",
        "  peerhub profile list         # inspect resolved configuration layers",
        "  peerhub session list         # list retained sessions and runtime state",
        "  peerhub observation refresh  # re-probe provider quota observations",
        "",
        "Model Facts & CLI Drift Recovery:",
        "  'peerhub observation refresh' only re-measures provider quotas.",
        "  'Facts: needs_recheck' clears only by running the refresh-model-profiles skill",
        "  from a repo checkout or upgrading to a release with new packaged facts.",
    ])
    return "\n".join(lines)
