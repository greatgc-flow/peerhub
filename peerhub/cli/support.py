"""Shared CLI entry points for reading and collecting quota evidence."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any, Sequence, cast


_PEER_ALIASES = {"cx": {"cx", "codex"}, "cc": {"cc", "claude"}, "ag": {"ag", "agy"}}
QUOTA_MAX_AGE_SECONDS = 15 * 60


def quota_workspace(db_path: str | Path) -> Path:
    database = Path(db_path).resolve()
    return database.parent.parent if database.parent.name == ".peerhub" else database.parent


def configured_pools(db_path: str | Path, peers: Sequence[str], kind: str) -> list[str] | None:
    """Resolve profile configuration at the CLI boundary, outside Observation probes (None: kind not requested)."""
    if not any(peer in _PEER_ALIASES[kind] for peer in peers):
        return None
    from peerhub.extensions.adapters.profiles import list_profiles
    profiles = list_profiles(quota_workspace(db_path))
    return sorted({str(pool) for profile in profiles if str(profile["profile"]).startswith(f"{kind}.")
                   for pool in cast(list[object], profile["fields"]["pools"]["value"] or [])})


def configured_cx_pools(db_path: str | Path, peers: Sequence[str]) -> list[str] | None:
    return configured_pools(db_path, peers, "cx")


def refresh_with_profile_pools(db_path: str | Path, peers: Sequence[str], **options: Any) -> dict[str, Any]:
    """The CLI entry to the quota collector. Resolve configured pools consistently for all callers."""
    from peerhub.extensions import quota_capture
    return quota_capture.refresh_quota(
        db_path, list(peers), cx_pools=configured_pools(db_path, peers, "cx"),
        cc_pools=configured_pools(db_path, peers, "cc"), ag_pools=configured_pools(db_path, peers, "ag"), **options)


def quota_items(db_path: str | Path, *, peer: str | None = None) -> list[dict[str, Any]]:
    from peerhub.extensions.diag_quota import quota_report
    from peerhub.extensions.observation_model import FreshnessPolicy

    # Observation freshness compares age >= TTL at microsecond precision. This gate includes exactly 15 minutes.
    policy = FreshnessPolicy(by_kind={"quota": QUOTA_MAX_AGE_SECONDS + .000001})
    report = quota_report(db_path, peer=peer, policy=policy)
    return [item for group in report["pools"] for item in group["items"] if item["kind"] == "quota"]


def quota_expired(item: dict[str, Any]) -> bool:
    age = item.get("age_seconds")
    return age is None or age > QUOTA_MAX_AGE_SECONDS


def try_quota_refresh(db_path: str | Path, peers: Sequence[str]) -> None:
    """A failed collection leaves quota unknown; ask delivery still has its own result."""
    try:
        refresh_with_profile_pools(db_path, peers)
    except (OSError, sqlite3.Error):
        pass


def refresh_dispatch_quota(db_path: Path, peer: str) -> None:
    if not any(not quota_expired(item) for item in quota_items(db_path, peer=peer)
               if item["subject_ref"] == peer):
        try_quota_refresh(db_path, [peer])


def print_warnings(result: dict[str, Any]) -> None:
    for w in result.get("warnings", []):
        print(f"warning: {w}", file=sys.stderr, flush=True)


def agy_token_command(result: dict[str, Any]) -> str | None:
    """The agy slash command that consumed model tokens in this refresh (`/credits` for an account probe, else `/usage`), or None."""
    hits = [o for o in result.get("observations", []) if o.get("payload", {}).get("evidence_ref") == "agy_usage_consumed_tokens"]
    if not hits:
        return None
    return "/credits" if any(o.get("kind") == "account" for o in hits) else "/usage"


def agy_token_use(result: dict[str, Any]) -> bool:
    return agy_token_command(result) is not None


def agy_token_stop_message(command: str) -> str:
    return (f"error: agy {command} consumed model tokens; stopping watch mode so quota is not burned every interval "
            "(pass --allow-agy-token-use to continue)")


def error_line(code: str, message: str, hint: str | None = None) -> None:
    hint = hint or f"peerhub explain {code}"
    print(f"[{code}] {' '.join(str(message).split())}; next: {' '.join(hint.split())}", file=sys.stderr)
