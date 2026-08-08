"""Built-in peer-adapter registry for runtime composition."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from types import MappingProxyType
from typing import TypeAlias

from peerhub.adapters.agy_adapter import RealAgyAdapter
from peerhub.adapters.claude_adapter import RealClaudeAdapter
from peerhub.adapters.codex_adapter import RealCodexAdapter
from peerhub.adapters.contract import PeerAdapter
from peerhub.builtins.fake_adapter import FakePeerAdapter
from peerhub.core.protocol import require_text


AdapterFactory: TypeAlias = Callable[[], PeerAdapter]

_ADAPTER_FACTORIES: Mapping[str, AdapterFactory] = MappingProxyType(
    {
        "fake": FakePeerAdapter,
        "ag": RealAgyAdapter,
        "cc": RealClaudeAdapter,
        "cx": RealCodexAdapter,
    }
)


def resolve_peer_adapter(peer_kind: str) -> PeerAdapter:
    """Create the built-in adapter registered for ``peer_kind``."""

    normalized = require_text(peer_kind, "peer_kind")
    try:
        factory = _ADAPTER_FACTORIES[normalized]
    except KeyError as error:
        supported = ", ".join(sorted(_ADAPTER_FACTORIES))
        raise ValueError(
            f"unsupported peer_kind {normalized!r}; expected one of: "
            f"{supported}"
        ) from error
    return factory()
