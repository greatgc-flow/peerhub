"""Provider-kind aliases shared by the Session Bridge entry (`ask`) and Observation collection.

A neutral, dependency-free module: extensions must not import each other (the composition root is `peerhub/cli`), so a
vocabulary both need lives here.
"""

from __future__ import annotations

ALIASES = {"codex": "cx", "cx": "cx", "claude": "cc", "cc": "cc", "agy": "ag", "ag": "ag",
           "ca": "ca", "oa": "oa"}  # the API peers (#378): Claude API through `ant`, OpenAI Responses API


def peer_kind(adapter_ref: str | None, peer_id: str) -> str | None:
    """The provider kind of a peer: its registered adapter decides; only an unregistered peer goes by its name."""
    return ALIASES.get(adapter_ref or peer_id)
