"""Load limits an AI needs before it starts parallel asks (advice, not enforced).

One source for the numbers that docs/batch.md, `peerhub explain parallel-quota` and `peerhub diag --plan` show. Pure and
dependency-free, so it stays importable when the first-party extensions are disabled (core-only mode).
"""

from __future__ import annotations

from typing import Any, Mapping

ACTIVE_ASKS_DEFAULT = 4  # a batch takes --max-parallel 3, one slot stays free for a manual ask
ACTIVE_ASKS_CEILING = 6
PER_PEER_LIMITS: dict[str, int] = {"cc": 3, "cx": 3, "ag": 1, "ca": 3, "oa": 3, "ga": 3}
CLI_PEERS = ("cc", "cx", "ag")  # always shown; API peers appear only while active
MIN_FREE_RAM_GB = 6
_GB = 1024 ** 3


def load_snapshot(active_by_peer: Mapping[str, int] | None, ram_available_bytes: int | None) -> dict[str, Any]:
    """Counts of lease-active asks against the limits; slots_free is measured against the default limit, never below 0.

    `active_by_peer=None` means the leases could not be counted: every count is None (shown as ?), never 0."""
    known = active_by_peer is not None
    counts: Mapping[str, int] = active_by_peer or {}
    active = sum(counts.values()) if known else None
    peers: dict[str, dict[str, int | None]] = {}
    for peer in dict.fromkeys([*CLI_PEERS, *PER_PEER_LIMITS, *counts]):
        count = counts.get(peer, 0) if known else None
        limit = PER_PEER_LIMITS.get(peer)
        if peer in CLI_PEERS or count:
            peers[peer] = {"active": count, "limit": limit,
                           "free": None if limit is None or count is None else max(0, limit - count)}
    ram = None if ram_available_bytes is None else round(ram_available_bytes / _GB, 1)
    return {"active": active, "limit_default": ACTIVE_ASKS_DEFAULT, "limit_ceiling": ACTIVE_ASKS_CEILING,
            "slots_free": None if active is None else max(0, ACTIVE_ASKS_DEFAULT - active), "peers": peers,
            "ram_free_gb": ram, "ram_min_gb": MIN_FREE_RAM_GB, "ram_ok": None if ram is None else ram >= MIN_FREE_RAM_GB}


def format_load(load: Mapping[str, Any]) -> str:
    """One line; leases and RAM are sampled at query time, unlike the stored quota evidence above it."""
    def count(value: Any) -> str:
        return "?" if value is None else str(value)

    peers = " ".join(f"{p} {count(v['active'])}/{v['limit']}" if v["limit"] is not None else f"{p} {count(v['active'])}"
                     for p, v in load["peers"].items())
    ram = "?" if load["ram_free_gb"] is None else f"{load['ram_free_gb']:.1f}"
    return (f"load: active {count(load['active'])}/{load['limit_default']} (ceil {load['limit_ceiling']}) | {peers} | "
            f"free RAM {ram} GB (min {load['ram_min_gb']}) | sampled now")
