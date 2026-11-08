"""Small helpers shared by the CLI commands that run the quota collector (observation refresh, monitor)."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Sequence, cast


_PEER_ALIASES = {"cx": {"cx", "codex"}, "cc": {"cc", "claude"}, "ag": {"ag", "agy"}}


def configured_pools(db_path: str | Path, peers: Sequence[str], kind: str) -> list[str] | None:
    """Resolve profile configuration at the CLI boundary, outside Observation probes (None: kind not requested)."""
    if not any(peer in _PEER_ALIASES[kind] for peer in peers):
        return None
    from peerhub.extensions.adapters.profiles import list_profiles
    database = Path(db_path).resolve()
    workspace = database.parent.parent if database.parent.name == ".peerhub" else database.parent
    profiles = list_profiles(workspace)
    return sorted({str(pool) for profile in profiles if str(profile["profile"]).startswith(f"{kind}.")
                   for pool in cast(list[object], profile["fields"]["pools"]["value"] or [])})


def configured_cx_pools(db_path: str | Path, peers: Sequence[str]) -> list[str] | None:
    return configured_pools(db_path, peers, "cx")


def refresh_with_profile_pools(db_path: str | Path, peers: Sequence[str], **options: Any) -> dict[str, Any]:
    """The one CLI entry to the quota collector: every caller (observation refresh, monitor) declares the profile pools alike."""
    from peerhub.extensions import quota_capture
    return quota_capture.refresh_quota(
        db_path, list(peers), cx_pools=configured_pools(db_path, peers, "cx"),
        cc_pools=configured_pools(db_path, peers, "cc"), ag_pools=configured_pools(db_path, peers, "ag"), **options)


def print_warnings(result: dict[str, Any]) -> None:
    for w in result.get("warnings", []):
        print(f"warning: {w}", file=sys.stderr, flush=True)


def agy_token_use(result: dict[str, Any]) -> bool:
    return any(o.get("payload", {}).get("evidence_ref") == "agy_usage_consumed_tokens" for o in result.get("observations", []))


def error_line(code: str, message: str, hint: str | None = None) -> None:
    hint = hint or f"peerhub explain {code}"
    print(f"[{code}] {' '.join(str(message).split())}; next: {' '.join(hint.split())}", file=sys.stderr)
