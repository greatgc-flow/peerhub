"""Declared-hint profile routing (R4 section 2.8, B7).

Routing relies on explicit capability matching and user preference only. A caller DECLARES the
work shape with ``--effort-hint``; ``routing.preference_map`` maps each hint to an ordered list of
registered profile-binding IDs. Nothing is inferred from prompt text, model names or tier strings.

Modes (``routing.effort_routing``, built-in default ``advisory``):
* ``off`` / ``advisory`` -- selection unchanged (advisory reports what opt-in would pick);
* ``opt-in``             -- hard filters first, then the first eligible binding in declared order;
* an explicit ``--profile`` is a PIN: exactly that profile or a rejection, never substituted.
A missing hint leaves selection unchanged; an unknown hint key is a validation error; no eligible
candidate under opt-in is an explicit blocker. Quality stays ABSENT (never scored).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

EFFORT_ROUTING_MODES = ("off", "advisory", "opt-in")


class EffortRoutingError(ValueError):
    """Invalid hint / mode, or no eligible target (CLI exit 2)."""


@dataclass(frozen=True)
class RoutingDecision:
    profile_id: str | None  # None: leave the peer's default selection unchanged
    applied: bool  # True only when opt-in actually chose the profile
    note: str | None = None  # advisory / pin explanation for the operator


def decide_profile(
    *,
    peer_kind: str,
    explicit_profile: str | None,
    hint: str | None,
    mode: str,
    preference_map: Mapping[str, Sequence[str]],
    is_eligible: Callable[[str], bool],
) -> RoutingDecision:
    """Choose the profile for one dispatch target from a declared hint."""

    if mode not in EFFORT_ROUTING_MODES:
        raise EffortRoutingError(
            f"invalid effort_routing {mode!r}; expected one of {EFFORT_ROUTING_MODES}"
        )
    if hint is None:
        return RoutingDecision(explicit_profile, False)
    if hint not in preference_map:
        known = ", ".join(sorted(preference_map)) or "(none configured)"
        raise EffortRoutingError(f"unknown effort hint {hint!r}; known hints: {known}")

    if explicit_profile is not None:
        return RoutingDecision(
            explicit_profile,
            False,
            f"--profile {explicit_profile} pins the target; --effort-hint {hint!r} not applied",
        )

    prefix = f"{peer_kind}."
    candidates = [binding for binding in preference_map[hint] if binding.startswith(prefix)]
    eligible = next((binding for binding in candidates if is_eligible(binding)), None)

    if mode == "opt-in":
        if eligible is None:
            raise EffortRoutingError(
                f"no eligible profile for hint {hint!r} on peer {peer_kind!r} "
                f"(declared candidates: {', '.join(candidates) or 'none for this peer'})"
            )
        return RoutingDecision(eligible, True, f"effort hint {hint!r} selected {eligible}")

    if mode == "off":
        return RoutingDecision(None, False)
    if eligible is None:
        note = f"advisory: hint {hint!r} has no eligible profile on peer {peer_kind!r}"
    else:
        note = f"advisory: hint {hint!r} would select {eligible} under effort_routing=opt-in"
    return RoutingDecision(None, False, note)
