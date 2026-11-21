"""Provider-kind vocabulary: the built-in peer ids, their aliases and the quota-capable defaults.

A neutral, dependency-free core module: the CLI (composition root) and every extension read it, and it must stay importable
when the first-party extensions are disabled (core-only mode).
"""

from __future__ import annotations

CLI_PEER_ALIASES = {"cx": "codex", "cc": "claude", "ag": "agy"}
API_PEER_KINDS = ("ca", "oa", "ga")  # Claude API through ant, OpenAI and Gemini
BUILTIN_PEER_KINDS = (*CLI_PEER_ALIASES, *API_PEER_KINDS)
QUOTA_CAPABLE_PEERS = tuple(CLI_PEER_ALIASES)  # CLI peers have quota probes, in default collection order

ALIASES = {
    **{name: kind for kind, alias in CLI_PEER_ALIASES.items() for name in (alias, kind)},
    **{kind: kind for kind in API_PEER_KINDS},
}
BUILTIN_PEER_LABELS = tuple(f"{kind}/{alias}" for kind, alias in CLI_PEER_ALIASES.items()) + API_PEER_KINDS
BUILTIN_PEER_CHOICES = f"{', '.join(BUILTIN_PEER_KINDS[:-1])} or {BUILTIN_PEER_KINDS[-1]}"


def peer_kind(adapter_ref: str | None, peer_id: str) -> str | None:
    """The provider kind of a peer: its registered adapter decides; only an unregistered peer goes by its name."""
    return ALIASES.get(adapter_ref or peer_id)
