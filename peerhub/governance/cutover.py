"""
Cutover protocol for B8a phase replacement.
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
    """
    Encode EVERY row of the 8.2 table:
    - proposed/voting -> voting/core
    - quorum_reached -> quorum_reached, or final_call if floor_requires_final_call
    - final_call(partial) -> final_call, retain_unbound_acks=True, evaluator frozen_legacy
    - resolved/timeout -> approved|rejected|escalated from generic_outcome and timeout_evidence
      (legacy phase "resolved" is NOT "approved"; unknown outcome -> ambiguity hold, never guess)
    - pending/target-unmaterialized -> preserve_approval_snapshot=True, recovery re-route to exclusive materializer
    - claimed-with-no-target / target-materialized-without-result -> evaluate from immutable evidence, recovery_action must never be "force_execute" after a revocation
    - Unknown phase -> SchemaError.
    """
    raise NotImplementedError("RED phase")


class FrozenLegacyEvaluator:
    """
    Given the frozen legacy eligible set and a set of legacy ACKs, is_complete() requires
    a legacy-equivalent ACK from every member of the frozen set; new mandatory floors do NOT 
    retroactively apply (a floor flag passed in is ignored); unbound legacy ACKs retained.
    """
    def __init__(self, frozen_eligible_set: Set[str]):
        raise NotImplementedError("RED phase")

    def is_complete(self, legacy_acks: Set[str], *, floor_flag: bool = False) -> bool:
        raise NotImplementedError("RED phase")


def classify_activation_state(
    has_governed_targets: bool,
    has_governed_targets_v1: bool,
    epoch_before: int,
    epoch_now: int,
    metadata_written: bool
) -> Literal["v1_intact", "v2_active", "inconsistent"]:
    """
    Classify activation state of the V2 cutover atomic transaction.
    v2_active only when v1-renamed table present, governed_targets absent,
    epoch incremented and metadata written ALL together (atomic all-or-nothing).
    v1_intact when nothing changed.
    every partial combination -> "inconsistent" (fail closed, caller must not proceed).
    """
    raise NotImplementedError("RED phase")
