from peerhub.application.commands import SubmissionMetadata
from peerhub.application.legacy import LegacyActionCall, LegacyTranslator, TranslatedCommand


def _submission() -> SubmissionMetadata:
    return SubmissionMetadata("req", "corr", "client", "cx", {}, "idem", None, None, 1)


def test_duty_legacy_actions_translate_to_fenced_wire_commands() -> None:
    translator = LegacyTranslator()
    cases = [
        # Leadership is workspace-global: no room/lease/fence params at
        # all, unlike the terminal-duty cases below.
        ("leader-claim", {"agent": "cx", "reason": "planning", "needs": "design"}, "routing.leadership.claim"),
        ("leader-yield", {"agent": "cx", "reason": "context_exhausted"}, "routing.leadership.yield"),
        ("terminal-handoff", {"current_lease_id": "old", "room_id": "room", "current_instance_id": "i", "current_profile_id": "cx", "term": 1, "authority_epoch": 2, "new_instance_id": "j", "new_profile_id": "ag", "new_owner_principal_id": "p2", "new_authority_epoch": 3}, "coordination.terminal.handoff"),
        ("terminal-heartbeat", {"lease_id": "lease", "room_id": "room", "instance_id": "i", "profile_id": "cx", "term": 1, "authority_epoch": 2}, "coordination.terminal.heartbeat"),
    ]
    for action, arguments, method in cases:
        outcome = translator.translate(LegacyActionCall(action, arguments), _submission())
        assert isinstance(outcome, TranslatedCommand)
        assert outcome.command.method == method
        assert outcome.command.encode_params()


def test_leadership_legacy_actions_translate_to_workspace_global_params() -> None:
    translator = LegacyTranslator()

    claim = translator.translate(
        LegacyActionCall(
            "leader-claim",
            {"agent": "cx", "detail": "failover", "needs": "recovery"},
        ),
        _submission(),
    )
    assert isinstance(claim, TranslatedCommand)
    assert claim.command.encode_params() == {
        "peer_node_id": "cx",
        "actor_id": "cx",
        "reason": "failover",
        "domain": "recovery",
    }

    yielded = translator.translate(
        LegacyActionCall("leader-yield", {"agent": "cc"}),
        _submission(),
    )
    assert isinstance(yielded, TranslatedCommand)
    assert yielded.command.encode_params() == {
        "yielding_peer_id": "cc",
        "actor_id": "cx",
        "reason": "",
    }


def test_leader_actions_default_the_peer_to_unknown() -> None:
    translator = LegacyTranslator()
    for action, field in (
        ("leader-claim", "peer_node_id"),
        ("leader-yield", "yielding_peer_id"),
    ):
        outcome = translator.translate(
            LegacyActionCall(action, {}), _submission()
        )
        assert isinstance(outcome, TranslatedCommand)
        assert outcome.command.encode_params()[field] == "unknown"
