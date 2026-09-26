"""
Pure specifications for migrating legacy (V1) rounds after the V2 activation.

NOTE: these are specs with tests but NO production caller yet -- after
activation V1 rounds are HELD (facade fails closed, storage fences V1 writes)
until an explicit administrator migration is built on top of them. The
activation-state classifier that used to live here modelled a table rename and
was superseded by ``persistence.consensus_activation.read_activation_state``
(option C, see docs/design/CONSENSUS-REPLACEMENT-ADDENDUM-2026-09-26.md).
"""
from dataclasses import dataclass
from typing import Optional, Set, Literal

from peerhub.core.errors import SchemaError
from peerhub.governance.policy_snapshot import ConfigurationError

@dataclass(frozen=True)
class MigrationDisposition:
    target_phase: str
    recovery_action: Optional[str]
    retain_unbound_acks: bool
    evaluator: Literal["core", "frozen_legacy"]
    preserve_approval_snapshot: bool

def migration_disposition(
    phase: str,
    *,
    floor_requires_final_call: bool = False,
    generic_outcome: Optional[str] = None,
    timeout_evidence: bool = False,
    target_state: Optional[str] = None
) -> MigrationDisposition:
    """Map a legacy phase to its V2 migration disposition (design 8.2)."""
    _outcomes = ("approved", "rejected", "escalated")

    def disp(target: str, **kw: object) -> MigrationDisposition:
        return MigrationDisposition(
            target_phase=target,
            recovery_action=kw.get("recovery_action"),  # type: ignore[arg-type]
            retain_unbound_acks=bool(kw.get("retain_unbound_acks", False)),
            evaluator=kw.get("evaluator", "core"),  # type: ignore[arg-type]
            preserve_approval_snapshot=bool(kw.get("preserve_approval_snapshot", False)),
        )

    if phase in ("proposed", "voting"):
        return disp("voting")
    if phase == "quorum_reached":
        return disp("final_call" if floor_requires_final_call else "quorum_reached")
    if phase == "final_call":
        # Frozen legacy contract: new floors never apply retroactively.
        return disp("final_call", retain_unbound_acks=True, evaluator="frozen_legacy")
    if phase in ("resolved", "timeout"):
        if generic_outcome in _outcomes:
            return disp(generic_outcome)
        if phase == "timeout" and timeout_evidence:
            return disp("escalated")
        return disp("ambiguity_hold")
    if phase == "pending_unmaterialized":
        return disp(
            "approved",
            recovery_action="reroute_exclusive_materializer",
            preserve_approval_snapshot=True,
        )
    if phase in ("claimed_no_target", "target_materialized_no_result"):
        # Never force execution: decide from immutable target/result evidence.
        return disp(
            "approved",
            recovery_action="evaluate_from_evidence",
            preserve_approval_snapshot=True,
        )
    raise SchemaError(f"Unknown legacy phase: {phase!r}")


class FrozenLegacyEvaluator:
    """
    Given the frozen legacy eligible set and a set of legacy ACKs, is_complete() requires
    a legacy-equivalent ACK from every member of the frozen set; new mandatory floors do NOT 
    retroactively apply (a floor flag passed in is ignored); unbound legacy ACKs retained.
    """
    def __init__(self, frozen_eligible_set: Set[str]):
        self._frozen = frozenset(frozen_eligible_set)

    def is_complete(self, legacy_acks: Set[str], *, floor_flag: bool = False) -> bool:
        # floor_flag is intentionally ignored: floors are not retroactive.
        return bool(self._frozen) and self._frozen <= set(legacy_acks)
