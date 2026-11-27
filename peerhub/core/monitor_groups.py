"""Monitor group choices for parser registration with or without extensions."""
from __future__ import annotations

GROUP_DESCRIPTIONS = {
    "overview": "Snapshot status and alerts",
    "activity": "Running and recent asks",
    "peers": "Peer profiles, quota, and session summaries",
    "execution": "Delivery certainty and outstanding work",
    "sessions": "Retained sessions and their bindings",
    "usage": "Recorded token usage and costs",
    "context": "Session context estimates",
    "capacity": "Quota pools and account status",
    "runtime": "Runtime profiles and launch evidence",
    "coverage": "Evidence coverage and sampling",
    "store": "Store identity and read status",
    "all": "Include every group",
}
GROUP_IDS: tuple[str, ...] = tuple(group for group in GROUP_DESCRIPTIONS if group != "all")
DEFAULT_GROUPS: tuple[str, ...] = ("overview", "activity", "peers", "execution", "sessions", "usage", "context", "capacity", "coverage")
