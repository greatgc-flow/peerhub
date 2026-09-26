"""Resolved consensus policy for V2 rounds (never hard-coded in the facade).

Layers (design 4.1): package defaults < global ``dispatch-policy.toml`` <
workspace ``dispatch-policy.toml``, validated by ``PolicyResolver``. The result
is frozen into the round's ``policy_snapshot`` at creation; nothing is re-read
later. An unreadable or invalid layer fails closed (ConfigurationError).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from peerhub.application.config_paths import (
    resolve_global_config_home,
    resolve_workspace_config_home,
)
from peerhub.dispatch.policy_resolver import PolicyResolver
from peerhub.governance.policy_snapshot import ConfigurationError

POLICY_FILE = "dispatch-policy.toml"
STRICT_ACTIONS = frozenset({"governance.proposal.create"})


def required_votes_for(formula: str, required_count: int, eligible_count: int, *, strict: bool) -> int:
    """Fixed agreement count for a quorum formula (never below the 2-agreement floor)."""
    if strict or formula == "unanimous":
        return max(eligible_count, 2)  # unanimous of the electorate
    if formula == "all_required":
        return max(required_count, 2)
    if formula == "majority":
        return max(required_count // 2 + 1, 2)
    if formula == "supermajority":
        return max(math.ceil(required_count * 2 / 3), 2)
    raise ConfigurationError(f"unknown quorum formula {formula!r}")


class ConsensusPolicyProvider:
    """Resolve the frozen per-round policy config from the layered dispatch policy."""

    def __init__(self, workspace_config: Path, global_config: Path) -> None:
        self._resolver = PolicyResolver(workspace_config, global_config)

    @classmethod
    def for_workspace(cls, workspace_root: Path) -> "ConsensusPolicyProvider":
        return cls(
            resolve_workspace_config_home(workspace_root).path / POLICY_FILE,
            resolve_global_config_home().path / POLICY_FILE,
        )

    def round_config(
        self, action: str, risk: str, required_count: int, eligible_count: int
    ) -> dict[str, Any]:
        try:
            policy = self._resolver.resolve(action).consensus
        except ConfigurationError:
            raise
        except Exception as exc:  # unreadable / malformed layer: fail closed, never {}
            raise ConfigurationError(f"consensus policy could not be resolved: {exc}") from exc
        strict = action in STRICT_ACTIONS
        return {
            "formula": "unanimous" if strict else policy.quorum_formula,
            "required_votes": required_votes_for(
                policy.quorum_formula, required_count, eligible_count, strict=strict
            ),
            "final_call_rule": policy.final_call_rule,
            "deadlines": {"voting": policy.timeout_seconds, "escalation": policy.timeout_seconds},
            "escalation_paths": [policy.escalation_target],
        }
