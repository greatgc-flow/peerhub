from datetime import datetime, timezone

from peerhub.dispatch.duty_lease import DutyLeaseSnapshot, DutyLeaseState, DutyOwnerIdentity
from peerhub.telemetry.domain_rows import (
    format_consensus_row,
    format_duty_row,
    format_task_row,
    format_task_row_narrow,
)


def test_consensus_row_uses_real_nested_schema_and_no_fake_deadline():
    state = {
        "phase": "voting",
        "votes": {"cx": "AGREE", "ag": "AGREE"},
        "participants": {"quorum": {"required": 2}},
    }
    assert format_consensus_row(state, 1_000) == "CONSENSUS VOTING 2/2 Q:2 T-—"


def test_task_rows_show_checkpoint_and_approval_flags():
    state = {"current_stage": "IMPLEMENT", "state": "RUNNING", "checkpoint": {}, "approval": {"required": True}}
    assert format_task_row(state) == "TASK IMPLEMENT RUNNING CP:yes AP:yes"
    assert format_task_row_narrow(state) == "TASK IMPLEMENT CP✓ AP✓"


def test_duty_row_handles_unheld_active_and_expired_leases():
    assert format_duty_row(None, 100) == "DUTY UNHELD"
    lease = DutyLeaseSnapshot("l", "room-1", "coordinator", DutyOwnerIdentity("i", "p"), "principal", 1, 7, None, DutyLeaseState.ACTIVE, 200, 1, 1, 1)
    assert format_duty_row(lease, 100) == "DUTY coordinator 7 HB:in 1m 40s room-1"
    assert format_duty_row(lease, 200) == "DUTY coordinator 7 HB:EXPIRED room-1"
