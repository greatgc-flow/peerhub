"""Monitor group choices for parser registration with or without extensions."""
from __future__ import annotations

from typing import Any

GROUPS: tuple[dict[str, Any], ...] = (
    {"id": "overview", "order": 1, "description": "Cross-group peer summary and highest-priority state.", "collection_effect": "read_only"},
    {"id": "activity", "order": 2, "description": "Running and recent asks (state, request and reply lines) and the leads that launched them.", "collection_effect": "read_only"},
    {"id": "peers", "order": 3, "description": "Per-peer dependency chain: quota, profile, session reuse, and context.", "collection_effect": "read_only"},
    {"id": "execution", "order": 4, "description": "Bridge delivery certainty, session reuse, and measured delivery latency; not task success.", "collection_effect": "read_only"},
    {"id": "sessions", "order": 5, "description": "Retained session lifecycle, fresh/reuse state, and configured profile/model/effort.", "collection_effect": "read_only"},
    {"id": "usage", "order": 6, "description": "Provider-reported token usage and its reporting coverage.", "collection_effect": "read_only"},
    {"id": "context", "order": 7, "description": "Runtime context occupancy evidence; unknown when providers do not report it.", "collection_effect": "read_only"},
    {"id": "capacity", "order": 8, "description": "Quota pools, remaining capacity, freshness, and supported seven-day pace.", "collection_effect": "read_only"},
    {"id": "runtime", "order": 9, "description": "Current profile provenance and adapter/runtime details.", "collection_effect": "read_only"},
    {"id": "coverage", "order": 10, "description": "Data completeness, stale/unknown evidence, legacy gaps, and sampling limits.", "collection_effect": "read_only"},
    {"id": "store", "order": 11, "description": "Database schema, size, projection, and storage integrity summary.", "collection_effect": "read_only"},
)
GROUP_IDS = tuple(str(item["id"]) for item in GROUPS)
DEFAULT_GROUPS = ("overview", "activity", "peers", "execution", "sessions", "usage", "context", "capacity", "coverage")
