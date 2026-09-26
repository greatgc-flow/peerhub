"""TDD cases for Execution Certainty and Crash Fences (EX-* cases)."""
import pytest

from peerhub.core.errors import InvalidMutationError
from peerhub.dispatch.attempt_lifecycle import AttemptLifecycleManager
from peerhub.core.execution import ExecutionCertainty

# Note: The retry rules for NOT_STARTED and MAY_HAVE_STARTED are already covered 
# in `tests/unit/dispatch/test_validate_retry_authorizable.py`.
# The EX-* cases here cover the NEW cancellation boundary transitions introduced in R2b.

def test_ex_01_cancel_before_claim():
    """EX-01: Cancellation requested before execution claim -> NOT_STARTED. Cleanly cancelled."""
    manager = AttemptLifecycleManager()
    
    # State before claim
    current_state = {"claimed": False, "certainty": None}
    
    result = manager.cancel_attempt(current_state)
    assert result.certainty == ExecutionCertainty.NOT_STARTED
    assert result.status == "cancelled"

def test_ex_02_cancel_after_claim_uncertain():
    """EX-02: Cancellation requested after execution claim (uncertain crash) -> MAY_HAVE_STARTED."""
    manager = AttemptLifecycleManager()
    
    # State after durable claim but uncertain handoff
    current_state = {"claimed": True, "certainty": ExecutionCertainty.MAY_HAVE_STARTED}
    
    result = manager.cancel_attempt(current_state)
    assert result.certainty in (ExecutionCertainty.MAY_HAVE_STARTED, ExecutionCertainty.STARTED)
    assert result.status == "cancelled"

def test_ex_03_cancel_on_completed():
    """EX-03: Cancellation requested on COMPLETED effect -> Rejected."""
    manager = AttemptLifecycleManager()
    
    # State is already completed
    current_state = {"claimed": True, "certainty": ExecutionCertainty.TERMINAL}
    
    with pytest.raises(InvalidMutationError, match="Cannot overwrite completed history"):
        manager.cancel_attempt(current_state)
