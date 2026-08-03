"""Unit tests for recover_interrupted_attempt in dispatch/service.py."""

import pytest
from peerhub.core.errors import InvalidMutationError
from peerhub.core.execution import ExecutionCertainty
from peerhub.dispatch.process import TerminalClassification
from peerhub.dispatch.service import recover_interrupted_attempt


def test_recover_interrupted_attempt_happy_path():
    """Happy path: single INTENT_PERSISTED entry returns START_UNCERTAIN."""
    journal_entries = ["INTENT_PERSISTED"]
    journal_digest = "sha256:valid_digest_123"

    outcome = recover_interrupted_attempt(
        journal_entries=journal_entries,
        journal_digest=journal_digest,
    )

    assert outcome.terminal_classification == TerminalClassification.START_UNCERTAIN
    assert outcome.execution_outcome.started is False
    assert outcome.execution_outcome.exit_code is None
    assert outcome.execution_outcome.timed_out is False
    assert outcome.execution_outcome.cancelled is False
    assert (
        outcome.execution_outcome.execution_certainty
        == ExecutionCertainty.MAY_HAVE_STARTED
    )
    assert outcome.effect_certainty == ExecutionCertainty.MAY_HAVE_STARTED
    assert outcome.automatic_replay_authorized is False
    assert outcome.journal_digest == journal_digest


def test_recover_interrupted_attempt_fail_closed_empty_journal():
    """Fail closed: empty journal_entries raises InvalidMutationError."""
    with pytest.raises(InvalidMutationError, match="journal_entries cannot be empty"):
        recover_interrupted_attempt(
            journal_entries=[],
            journal_digest="sha256:digest_empty",
        )


def test_recover_interrupted_attempt_fail_closed_post_intent_evidence():
    """Fail closed: journal_entries with SPAWNED or EXIT after INTENT_PERSISTED."""
    with pytest.raises(
        InvalidMutationError, match="post-intent evidence present"
    ):
        recover_interrupted_attempt(
            journal_entries=["INTENT_PERSISTED", "SPAWNED"],
            journal_digest="sha256:digest_spawned",
        )

    with pytest.raises(
        InvalidMutationError, match="post-intent evidence present"
    ):
        recover_interrupted_attempt(
            journal_entries=["INTENT_PERSISTED", "EXIT"],
            journal_digest="sha256:digest_exit",
        )


def test_recover_interrupted_attempt_fail_closed_missing_intent_persisted():
    """Fail closed: journal_entries missing INTENT_PERSISTED raises InvalidMutationError."""
    with pytest.raises(
        InvalidMutationError, match="missing INTENT_PERSISTED"
    ):
        recover_interrupted_attempt(
            journal_entries=["SPAWNED"],
            journal_digest="sha256:digest_no_intent",
        )
