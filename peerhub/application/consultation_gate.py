"""Ingress consultation gate for ask/broadcast (R4 2.1, section 8.1 decision tree).

The resolved ``DispatchPolicy.consultation_depth`` decides what must happen before dispatch:

* NONE   -> proceed directly (no governance targets);
* NOTIFY -> non-blocking: dispatch proceeds (delivery outcome never blocks it);
* REVIEW / QUORUM / UNANIMOUS -> a consultation round is required before dispatch.

The consultation ROUND ENGINE for ask/broadcast (electorate source, round payload) is not
specified by the ratified text yet, so a policy that demands one FAILS CLOSED with an explicit
blocker instead of being silently bypassed: a stricter configured obligation must never be
downgraded by a missing implementation (R4: "editing a lower-trust TOML file cannot authorize its
own downgrade" -- and the inverse holds for missing machinery).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from peerhub.application.config_paths import (
    resolve_global_config_home,
    resolve_workspace_config_home,
)
from peerhub.core.errors import ConfigurationError
from peerhub.dispatch.policy import ConsultationDepth
from peerhub.dispatch.policy_resolver import PolicyResolver

POLICY_FILE = "dispatch-policy.toml"


class ConsultationBlockedError(ValueError):
    """The resolved policy requires a consultation round this build cannot run (CLI exit 2)."""


@dataclass(frozen=True)
class ConsultationDecision:
    depth: ConsultationDepth
    proceed: bool
    note: str | None = None


def evaluate_consultation(workspace_root: Path, action_name: str) -> ConsultationDecision:
    """Resolve the layered policy for ``action_name`` and apply the depth decision tree."""

    resolver = PolicyResolver(
        resolve_workspace_config_home(workspace_root).path / POLICY_FILE,
        resolve_global_config_home().path / POLICY_FILE,
    )
    try:
        depth = resolver.resolve(action_name).consultation_depth
    except ConfigurationError as error:  # an invalid layer is a blocker, never a silent default
        raise ConsultationBlockedError(f"invalid dispatch policy: {error}") from error
    if depth is ConsultationDepth.NONE:
        return ConsultationDecision(depth, True)
    if depth is ConsultationDepth.NOTIFY:
        return ConsultationDecision(
            depth,
            True,
            f"consultation NOTIFY for {action_name}: non-blocking; notification delivery "
            "is not implemented yet, dispatch proceeds",
        )
    raise ConsultationBlockedError(
        f"consultation depth {depth.value!r} is configured for {action_name!r} but the "
        "ask/broadcast consultation round engine is not available; refusing to bypass a "
        "configured obligation (set consultation.overrides to 'none' or 'notify' to dispatch)"
    )
