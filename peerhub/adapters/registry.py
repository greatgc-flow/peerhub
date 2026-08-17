"""Built-in peer-adapter registry for runtime composition."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TypeAlias

from peerhub.adapters.agy_adapter import RealAgyAdapter
from peerhub.adapters.claude_adapter import RealClaudeAdapter
from peerhub.adapters.codex_adapter import RealCodexAdapter
from peerhub.adapters.contract import (
    AdapterRequest,
    PeerAdapter,
    ProfileDescriptor,
    SessionAction,
)
from peerhub.builtins.fake_adapter import FakePeerAdapter
from peerhub.core.protocol import require_text
from peerhub.core.execution import TransportLimits


class ExecutableNotFoundError(ValueError):
    """Raised when an adapter's executable cannot be found in PATH."""

class ProfileNotFoundError(ValueError):
    """Raised when a specified profile_id is not exposed by the adapter, or auto-selection fails."""

@dataclass(frozen=True)
class ResolvedPeerTarget:
    cli_name: str
    peer_kind: str
    adapter: PeerAdapter
    profile: ProfileDescriptor
    executable_path: Path


AdapterFactory: TypeAlias = Callable[[], PeerAdapter]

# The backing dicts are mutable so ``register_adapter_factory()`` can add a
# kind after import; the ``_ADAPTER_FACTORIES``/``_CLI_ALIASES`` proxies below
# are live read-only views over them, so every other reader keeps the original
# frozen-mapping guarantee.
_adapter_factories: dict[str, AdapterFactory] = {
    "fake": FakePeerAdapter,
    "ag": RealAgyAdapter,
    "cc": RealClaudeAdapter,
    "cx": RealCodexAdapter,
}

_ADAPTER_FACTORIES: Mapping[str, AdapterFactory] = MappingProxyType(
    _adapter_factories
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

_cli_aliases: dict[str, str] = {
    "agy": "ag",
    "ag": "ag",
    "claude": "cc",
    "cc": "cc",
    "codex": "cx",
    "cx": "cx",
}

_CLI_ALIASES: Mapping[str, str] = MappingProxyType(_cli_aliases)


def register_adapter_factory(
    peer_kind: str,
    cli_aliases: tuple[str, ...],
    factory: AdapterFactory,
) -> None:
    """Register a new adapter factory and its CLI aliases at runtime.

    Intended for an adapter package to call at its own import time, so a new
    peer kind does not require editing this module. Registration is validated
    completely before anything is written: a rejected call leaves both the
    factory table and the alias table exactly as they were.

    Raises ``ValueError`` if ``peer_kind`` is already registered, or if any
    alias is already owned by a different peer kind. Existing registrations
    are never silently replaced or rerouted.

    No lock is taken. Module-level registries in this package are otherwise
    immutable after import, and this call is expected during import or test
    setup, which the interpreter already serializes; adding a lock here would
    introduce a convention this package does not use elsewhere.
    """

    normalized_kind = require_text(peer_kind, "peer_kind")
    if normalized_kind in _adapter_factories:
        raise ValueError(
            f"peer_kind {normalized_kind!r} is already registered"
        )

    normalized_aliases: list[str] = []
    for index, alias in enumerate(cli_aliases):
        normalized_alias = require_text(alias, f"cli_aliases[{index}]")
        owner = _cli_aliases.get(normalized_alias)
        if owner is not None and owner != normalized_kind:
            raise ValueError(
                f"cli alias {normalized_alias!r} is already registered "
                f"for peer_kind {owner!r}"
            )
        normalized_aliases.append(normalized_alias)

    _adapter_factories[normalized_kind] = factory
    for normalized_alias in normalized_aliases:
        _cli_aliases[normalized_alias] = normalized_kind


def _resolve_executable_path(executable_name: str) -> Path:
    # Do not use shutil.which directly as it is vulnerable to cwd-hijacking on Windows
    # Instead, search PATH explicitly.
    import os
    path_env = os.environ.get("PATH", "")
    paths = [Path(p) for p in path_env.split(os.pathsep) if p]
    
    extensions = [""]
    if os.name == "nt":
        pathext = os.environ.get("PATHEXT", "").split(os.pathsep)
        extensions = [ext.lower() for ext in pathext if ext]
        if not extensions:
            extensions = [".com", ".exe", ".bat", ".cmd"]

    has_recognized_ext = Path(executable_name).suffix.lower() in extensions

    for p in paths:
        try:
            # Skip invalid paths
            if not p.is_dir():
                continue
        except OSError:
            continue
            
        candidate = p / executable_name
        if candidate.is_file():
            return candidate.absolute()
            
        if not has_recognized_ext:
            for ext in extensions:
                if not ext:
                    continue
                candidate_ext = p / f"{executable_name}{ext}"
                if candidate_ext.is_file():
                    return candidate_ext.absolute()

    raise ExecutableNotFoundError(f"executable {executable_name!r} not found in PATH")

def resolve_peer_target(name: str, *, profile_id: str | None = None) -> ResolvedPeerTarget:
    normalized = require_text(name, "name")
    if normalized not in _CLI_ALIASES:
        supported = ", ".join(sorted(_CLI_ALIASES))
        raise ValueError(
            f"unsupported cli_name {normalized!r}; expected one of: "
            f"{supported}"
        )
    
    peer_kind = _CLI_ALIASES[normalized]
    adapter = resolve_peer_adapter(peer_kind)
    
    profiles = adapter.descriptor.profiles
    if profile_id is not None:
        selected_profile = next((p for p in profiles if p.profile_id == profile_id), None)
        if selected_profile is None:
            raise ProfileNotFoundError(f"profile {profile_id!r} not supported by {peer_kind}")
    else:
        if len(profiles) != 1:
            raise ProfileNotFoundError(f"adapter {peer_kind} has {len(profiles)} profiles; profile_id is required")
        selected_profile = profiles[0]
        
    class _DummyContract:
        contract_id: str = "dummy"

    # The argv[0] of the adapter invocation is the executable
    dummy_req = AdapterRequest(
        request_id="dummy",
        prompt_content="dummy",
        prompt_reference=None,
        workspace_scope="dummy",
        profile_id=selected_profile.profile_id,
        requested_session_action=SessionAction.NONE,
        completion_contract=_DummyContract(), # type: ignore
    )
    plan = adapter.plan_invocation(
        dummy_req, selected_profile, session=None, limits=TransportLimits(60000, 60000, 1000000)
    )
    executable_name = plan.argv[0]
    
    executable_path = _resolve_executable_path(executable_name)
    
    return ResolvedPeerTarget(
        cli_name=normalized,
        peer_kind=peer_kind,
        adapter=adapter,
        profile=selected_profile,
        executable_path=executable_path,
    )
