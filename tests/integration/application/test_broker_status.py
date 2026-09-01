"""Integration coverage for read-only unfinished effect status."""

from __future__ import annotations

import json
from pathlib import Path

from peerhub.application.commands import SubmissionMetadata
from peerhub.application.legacy import (
    EffectStatusCommand,
    InvalidLegacyArguments,
    LegacyActionCall,
    LegacyTranslator,
    TranslatedCommand,
)
from peerhub.cli import main
from peerhub.client import Client
from peerhub.core.context import PathLayout, RuntimeContext
from peerhub.core.identity import AuthenticatedSubject
from peerhub.core.ports import RequestContext
from peerhub.core.protocol import CommandSuccess
from peerhub.governance.contract import EffectIntent, EffectOutcome
from peerhub.governance.invariant_requests import (
    RATIFIED_INVARIANT_EFFECT_KIND,
)
from peerhub.runtime import Runtime, create_runtime
from tests.fakes import DeterministicClock, SequentialIdSource


def _runtime(tmp_path: Path) -> Runtime:
    return create_runtime(
        RuntimeContext(
            "broker-status-workspace",
            PathLayout.for_workspace(tmp_path),
            DeterministicClock(1_000),
            SequentialIdSource(),
        ),
        adapter_peer_kind="fake",
    )


def _submission(*, key: str = "default") -> SubmissionMetadata:
    return SubmissionMetadata(
        client_request_id=f"broker-status-request-{key}",
        correlation_id=f"broker-status-correlation-{key}",
        client_id="broker-status-client",
        actor_id="operator",
        scope={},
        idempotency_key=None,
        expected_policy_revision=None,
        expected_configuration_revision=None,
        client_timestamp=1_000,
    )


def _client(runtime: Runtime) -> Client:
    return Client(
        runtime.application_api,
        caller=RequestContext(
            principal=AuthenticatedSubject(
                "operator", "broker-status-test"
            ).principal_id,
            client_id="broker-status-client",
        ),
    )


def _seed_two_real_approval_effects(
    runtime: Runtime,
) -> tuple[str, str]:
    real_event_ids: list[str] = []
    for index in (1, 2):
        round_id = f"broker-status-round-{index}"
        service = runtime.consensus_service
        service.propose(
            round_id=round_id,
            title=f"Approval {index}",
            question=f"Approve {index}?",
            body=f"Changes:\nInvariant {index}",
            proposer_id="cc",
            required_participants=("cc", "cx"),
            eligible_participants=("cc", "cx"),
            risk="normal",
            source_hash=f"sha256:broker-status-{index}",
        )
        service.cast_vote(round_id, actor_id="cc", choice="agree")
        service.cast_vote(round_id, actor_id="cx", choice="agree")
        target = service.get_target(round_id)
        assert target is not None
        submission = service.resolve(
            round_id,
            "approved",
            "cc",
            "broker-status integration approval",
            target.revision,
            EffectIntent(
                kind=RATIFIED_INVARIANT_EFFECT_KIND,
                payload={
                    "round_id": round_id,
                    "request_id": f"request-{round_id}",
                },
            ),
        )
        real_event_ids.append(submission.receipt.outbox_event_id)

    # The consensus domain also emits noop deliveries for propose/vote. Mark
    # only those complete so the status page contains exactly the two real
    # approval effects under test.
    for pending in runtime.governance_broker.recover_pending_effects(
        limit=20
    ):
        if pending.event.event_id in real_event_ids:
            continue
        attempt_id = f"cleanup-{pending.event.event_id}"
        runtime.governance_broker.claim_effect(
            pending.event.event_id,
            owner_id="broker-status-test-cleanup",
            attempt_id=attempt_id,
        )
        runtime.governance_broker.record_effect_result(
            pending.event.event_id,
            owner_id="broker-status-test-cleanup",
            attempt_id=attempt_id,
            outcome=EffectOutcome.EFFECT_SUCCEEDED,
        )

    runtime.governance_broker.claim_effect(
        real_event_ids[1],
        owner_id="interrupted-projector",
        attempt_id="approval-attempt-2",
    )
    return real_event_ids[0], real_event_ids[1]


def test_effect_status_sqlite_page_fields_order_counts_and_has_more(
    tmp_path: Path,
) -> None:
    with _runtime(tmp_path) as runtime:
        event_ids = _seed_two_real_approval_effects(runtime)
        client = _client(runtime)

        full = client.submit(EffectStatusCommand(_submission(), limit=20))
        bounded = client.submit(
            EffectStatusCommand(_submission(key="bounded"), limit=1)
        )

    assert isinstance(full, CommandSuccess)
    deliveries = full.result["deliveries"]
    assert isinstance(deliveries, tuple)
    assert [row["event_id"] for row in deliveries] == list(event_ids)
    assert deliveries == (
        {
            "event_id": event_ids[0],
            "outbox_state": "PENDING",
            "recovery_disposition": "READY_TO_CLAIM",
            "effect_kind": RATIFIED_INVARIANT_EFFECT_KIND,
            "target_id": "broker-status-round-1",
            "target_revision": 4,
        },
        {
            "event_id": event_ids[1],
            "outbox_state": "CLAIMED",
            "recovery_disposition": "CONFIRMATION_REQUIRED",
            "effect_kind": RATIFIED_INVARIANT_EFFECT_KIND,
            "target_id": "broker-status-round-2",
            "target_revision": 4,
        },
    )
    assert full.result["has_more"] is False
    assert full.result["visible_unfinished_count"] == 2
    assert full.result["visible_unfinished_count_by_state"] == {
        "PENDING": 1,
        "CLAIMED": 1,
    }
    assert full.result["visible_unfinished_count_by_disposition"] == {
        "READY_TO_CLAIM": 1,
        "CONFIRMATION_REQUIRED": 1,
    }

    assert isinstance(bounded, CommandSuccess)
    assert bounded.result["deliveries"] == (deliveries[0],)
    assert bounded.result["has_more"] is True
    assert bounded.result["visible_unfinished_count"] == 1


def test_effect_status_zero_unfinished_effects(tmp_path: Path) -> None:
    with _runtime(tmp_path) as runtime:
        outcome = _client(runtime).submit(
            EffectStatusCommand(_submission(), limit=20)
        )

    assert isinstance(outcome, CommandSuccess)
    assert outcome.result == {
        "deliveries": (),
        "has_more": False,
        "visible_unfinished_count": 0,
        "visible_unfinished_count_by_state": {
            "PENDING": 0,
            "CLAIMED": 0,
        },
        "visible_unfinished_count_by_disposition": {
            "READY_TO_CLAIM": 0,
            "CONFIRMATION_REQUIRED": 0,
        },
    }


def test_broker_status_legacy_translation() -> None:
    translated = LegacyTranslator().translate(
        LegacyActionCall(
            action="broker-status", arguments={"limit": 2}
        ),
        _submission(),
    )
    invalid = LegacyTranslator().translate(
        LegacyActionCall(
            action="broker-status", arguments={"limit": 21}
        ),
        _submission(key="invalid"),
    )

    assert isinstance(translated, TranslatedCommand)
    assert isinstance(translated.command, EffectStatusCommand)
    assert translated.command.method == "governance.effect.status"
    assert translated.command.limit == 2
    assert isinstance(invalid, InvalidLegacyArguments)


def test_cli_broker_status_json(tmp_path: Path, capsys) -> None:
    with _runtime(tmp_path) as runtime:
        runtime.consensus_service.propose(
            round_id="cli-broker-status-round",
            title="CLI status",
            question="Visible?",
            body="CLI effect status",
            proposer_id="cc",
            required_participants=("cc", "cx"),
            eligible_participants=("cc", "cx"),
            risk="normal",
            source_hash="sha256:cli-broker-status",
        )

    assert main([
        "broker",
        "status",
        "--workspace",
        str(tmp_path),
        "--limit",
        "1",
        "--json",
    ]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["visible_unfinished_count"] == 1
    assert payload["has_more"] is False
    assert payload["deliveries"][0]["outbox_state"] == "PENDING"
    assert payload["deliveries"][0]["effect_kind"] == "consensus.noop"
    assert payload["deliveries"][0]["target_id"] == (
        "cli-broker-status-round"
    )
