"""Compatibility alias: the peer-kind vocabulary lives in `peerhub.core.peer_kinds` (importable in core-only mode)."""

from __future__ import annotations

from peerhub.core.peer_kinds import (
    ALIASES as ALIASES,
    API_PEER_KINDS as API_PEER_KINDS,
    BUILTIN_PEER_CHOICES as BUILTIN_PEER_CHOICES,
    BUILTIN_PEER_KINDS as BUILTIN_PEER_KINDS,
    BUILTIN_PEER_LABELS as BUILTIN_PEER_LABELS,
    CLI_PEER_ALIASES as CLI_PEER_ALIASES,
    QUOTA_CAPABLE_PEERS as QUOTA_CAPABLE_PEERS,
    peer_kind as peer_kind,
)
