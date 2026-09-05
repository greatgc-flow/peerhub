"""Gate 2 Lane 1: Built-in adapter discovery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from peerhub.adapters.registry import (
    resolve_peer_adapter,
    resolve_executable_path,
    ExecutableNotFoundError,
)
from peerhub.adapters.contract import AdapterRequest, SessionAction
from peerhub.core.execution import TransportLimits


@dataclass(frozen=True)
class AdapterFoundAndReady:
    peer_kind: str
    executable_path: Path
    profiles: list[str]


@dataclass(frozen=True)
class AdapterNotReady:
    peer_kind: str
    executable_path: Path
    reason: str


@dataclass(frozen=True)
class AdapterNotFound:
    peer_kind: str


def discover_builtin_adapters() -> list[AdapterFoundAndReady | AdapterNotReady | AdapterNotFound]:
    """Scan the built-in adapter kinds and report their installed state."""
    
    results: list[AdapterFoundAndReady | AdapterNotReady | AdapterNotFound] = []
    
    # Check explicitly required built-in peer kinds
    for peer_kind in ["ag", "cc", "cx"]:
        try:
            adapter = resolve_peer_adapter(peer_kind)
            
            # Extract first profile directly (fixes the single-profile assumption gap)
            if not adapter.descriptor.profiles:
                # Edge case if an adapter defines no profiles at all
                results.append(AdapterNotReady(peer_kind, Path(), "adapter has no profiles"))
                continue
                
            selected_profile = adapter.descriptor.profiles[0]
            
            class _DummyContract:
                contract_id: str = "dummy"

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
            
            executable_path = resolve_executable_path(executable_name)
            
            # Minimal readiness signal: executable exists and has nonzero size
            # Spawning a subprocess is disproportionate for a discovery sweep
            if not executable_path.is_file():
                results.append(AdapterNotReady(peer_kind, executable_path, "executable is not a file"))
                continue
                
            if executable_path.stat().st_size == 0:
                results.append(AdapterNotReady(peer_kind, executable_path, "executable size is 0"))
                continue
                
            profiles = [p.profile_id for p in adapter.descriptor.profiles]
            results.append(AdapterFoundAndReady(peer_kind, executable_path, profiles))
            
        except ExecutableNotFoundError:
            results.append(AdapterNotFound(peer_kind))
        except Exception as e:
            # If resolution or planning fails unexpectedly
            results.append(AdapterNotReady(peer_kind, Path("unknown"), f"resolution failed: {e}"))
            
    return results
